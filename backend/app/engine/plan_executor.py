"""
PlanExecutor — 执行 QueryPlan (Decomposer Routing P2)

核心机制:
1. 预校验 {{stepN.var}} 引用合法性: N 必须是已执行步骤, var 必须在 exports 中
2. 串行执行 steps — 按 step.database 切换对应 mongo engine (registry 缓存)
3. 执行后从结果抽取 exports 字段, 作为 previous[idx] 供后续步骤替换

失败策略:
- 变量引用不合法 → VariableResolutionError (pre-validate 阶段拒绝)
- 某步执行失败 → PlanExecutionError (携带 step_idx + 原异常, 给 P3 Refiner 回喂)
- 跨库 datasource 未配置 → PlanExecutionError (明确提示)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langfuse import observe

from app.config import settings
from app.engine.drivers.base import ExecuteMode
from app.engine.plan_models import GENERIC_RESTRICTION_HINT, QueryPlan
from app.tracing import get_client as _lf_client

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  异常
# ══════════════════════════════════════════════════════════════════════════════

class VariableResolutionError(RuntimeError):
    """{{stepN.var}} 引用无法解析 (step 未执行 / var 未导出)."""


class PlanExecutionError(RuntimeError):
    """某步执行失败. P3 Refiner 捕获后回喂 LLM 重试."""

    def __init__(self, step_idx: int, cause: BaseException, pipeline: Any = None):
        super().__init__(f"step_idx={step_idx} 执行失败: {cause}")
        self.step_idx = step_idx
        self.cause = cause
        self.pipeline = pipeline
        # INV-ERRCODE on the plan path: surface the cause's numeric code (if any) so the
        # agent loop's getattr(e, "code", None) recovers it. pymongo OperationFailure → its
        # numeric code (16410/304/...); a code-less cause (capability_violation RuntimeError,
        # variable-resolution error, datasource-not-found, ...) → None, behavior unchanged.
        self.code = getattr(cause, "code", None)


# ══════════════════════════════════════════════════════════════════════════════
#  变量解析
# ══════════════════════════════════════════════════════════════════════════════

# 完整字符串必须匹配 "{{step<N>.<var>}}" — 部分替换 (形如 "prefix{{...}}suffix") 不支持
_VAR_PATTERN = re.compile(r"^\{\{step(\d+)\.([A-Za-z_][A-Za-z0-9_]*)\}\}$")
# 扫描用 (在字符串任意位置出现)
_VAR_SCAN = re.compile(r"\{\{step(\d+)\.([A-Za-z_][A-Za-z0-9_]*)\}\}")


def _walk_strings(obj: Any):
    """递归迭代 obj 中所有字符串."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _walk_strings(x)


def pre_validate_vars(plan: QueryPlan) -> None:
    """
    扫描每个 step 的 pipeline/query 中所有 {{stepN.var}} 引用, 校验:
    1. N < 当前 step_idx (不能引用未来步骤)
    2. step N 必须存在
    3. var 必须在 step N 的 exports 中声明

    引擎差异:
    - mongodb: 变量必须是完整 token (整串替换); 部分嵌入不会被替换 → WARN
    - mysql: 变量可嵌在 SQL 串中 (IN 列表), 由 _render_sql_vars 渲染 → 嵌入合法

    失败 raise VariableResolutionError (阻断执行).
    """
    export_map: dict[int, set[str]] = {s.step_idx: set(s.exports) for s in plan.steps}

    def _check_ref(step_idx: int, idx: int, var: str) -> None:
        if idx >= step_idx:
            raise VariableResolutionError(
                f"step {step_idx} 引用了未来步骤 step{idx}"
            )
        if idx not in export_map:
            raise VariableResolutionError(
                f"step {step_idx} 引用了不存在的 step{idx}"
            )
        if var not in export_map[idx]:
            raise VariableResolutionError(
                f"step {step_idx} 引用 step{idx}.{var}, "
                f"但 step{idx}.exports={sorted(export_map[idx])}"
            )

    from app.engine.db_types import SQL_DB_TYPES as _SQL_DB_TYPES
    for step in plan.steps:
        is_sql = step.db_type in _SQL_DB_TYPES
        carriers: list[Any] = [step.pipeline, step.query, step.projection, step.sort]
        for carrier in carriers:
            for s in _walk_strings(carrier):
                matches = list(_VAR_SCAN.finditer(s))
                if not matches:
                    continue
                full = _VAR_PATTERN.fullmatch(s)
                if full is None and not is_sql:
                    # mongodb 部分嵌入 — 不会被替换, 给出警告便于 debug, 但不 raise
                    log.warning(
                        "[plan_exec] step=%d 字符串内嵌变量但非完整 token 形式, "
                        "不会被替换 (s=%r)", step.step_idx, s[:120],
                    )
                    continue
                # 校验所有引用 (mysql 嵌入式 / mongodb 完整 token)
                for m in matches:
                    _check_ref(step.step_idx, int(m.group(1)), m.group(2))


def _resolve_vars(obj: Any, prev: dict[int, dict[str, Any]]) -> Any:
    """
    递归替换 obj 中 "{{stepN.var}}" 字符串为 prev[N][var].
    仅支持完整字符串形式; 部分嵌入原样返回 (已在 pre_validate 给过 WARN).
    """
    if isinstance(obj, str):
        m = _VAR_PATTERN.fullmatch(obj)
        if m:
            idx = int(m.group(1))
            var = m.group(2)
            if idx not in prev:
                raise VariableResolutionError(
                    f"引用未执行步骤 step{idx} (已执行: {sorted(prev.keys())})"
                )
            if var not in prev[idx]:
                raise VariableResolutionError(
                    f"step{idx}.{var} 未产出 (可用: {sorted(prev[idx].keys())})"
                )
            return prev[idx][var]
        return obj
    if isinstance(obj, dict):
        return {k: _resolve_vars(v, prev) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_vars(x, prev) for x in obj]
    return obj


def _sql_literal(v: Any) -> str:
    """把单个 Python 值渲染为安全的 SQL 字面量.

    值来源是上游查询结果 (DB 数据), 非用户直接输入, 但仍做类型化转义防注入:
    - None → NULL
    - bool → TRUE/FALSE
    - int/float → 裸数字
    - 其他 → 单引号字符串, 内部单引号与反斜杠转义
    """
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"


def _render_sql_vars(sql: str, prev: dict[int, dict[str, Any]]) -> str:
    """渲染 SQL 串中嵌入的 {{stepN.var}} 为安全字面量列表 (逗号分隔).

    与 _resolve_vars 的整串替换不同: SQL 里变量通常嵌在 IN (...) 中间, 形如
    "WHERE id IN ({{step1.ids}})". 这里按 token 扫描替换为转义后的字面量列表.
    单值导出渲染为单个字面量; 列表导出渲染为 "a, b, c".
    """
    def _sub(m: re.Match) -> str:
        idx = int(m.group(1))
        var = m.group(2)
        if idx not in prev:
            raise VariableResolutionError(
                f"SQL 引用未执行步骤 step{idx} (已执行: {sorted(prev.keys())})"
            )
        if var not in prev[idx]:
            raise VariableResolutionError(
                f"step{idx}.{var} 未产出 (可用: {sorted(prev[idx].keys())})"
            )
        val = prev[idx][var]
        if isinstance(val, list):
            if not val:
                return "NULL"  # 空列表 → IN (NULL) 匹配 0 行, 不报语法错
            return ", ".join(_sql_literal(x) for x in val)
        return _sql_literal(val)

    return _VAR_SCAN.sub(_sub, sql)


def _extract_exports(docs: list[dict], exports: list[str]) -> dict[str, list[Any]]:
    """
    从执行结果中抽取 exports 声明的字段, 返回 {var_name: [values...]}.

    规则:
    - 若字段值本身是 list → extend (扁平一层, 支持 $push 聚合)
    - 若字段值是标量 → append
    - None 跳过
    """
    out: dict[str, list[Any]] = {k: [] for k in exports}
    for d in docs:
        if not isinstance(d, dict):
            continue
        for k in exports:
            v = d.get(k)
            if v is None:
                continue
            if isinstance(v, list):
                out[k].extend(v)
            else:
                out[k].append(v)
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  能力预校验 (pre-validation) — 纯函数, 无 I/O, 可独立单测
# ══════════════════════════════════════════════════════════════════════════════
#
# 在 execute_plan 派发 mongodb step 前运行, 解析该 step 的 datasource caps,
# 比对三类限制 (unsupported_ops / unsupported_stage_variants / syntax_constraints),
# 命中则在执行前拦截. native/empty-caps step 与 mysql step 不校验.
#
# 检测是 evidence-backed 的: 每个谓词只匹配 DocumentDB 5.0.0 实测拒绝的形态
# (只读探针对 ds=3 验证, 见 design Verification). 泛化/flavor-agnostic 原则成立 —
# 谓词仅当 resolved caps 声明对应限制时才触发.


def _iter_operators(node: Any):
    """Yield every '$'-prefixed key found anywhere in a pipeline node (recursive).
    Used for expression-operator restrictions ($function, $round, ...), which may be
    nested at any depth inside a stage."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and k.startswith("$"):
                yield k
            yield from _iter_operators(v)
    elif isinstance(node, list):
        for x in node:
            yield from _iter_operators(x)


def _stage_names(pipeline: list) -> set[str]:
    """Top-level stage keys actually present in the pipeline (e.g. {'$match', '$lookup'}).
    Stage-level restrictions match against these, NOT against every nested operator."""
    names: set[str] = set()
    for stage in pipeline:
        if isinstance(stage, dict):
            for k in stage:
                if isinstance(k, str) and k.startswith("$"):
                    names.add(k)
    return names


# ── R5: $project $-fieldpath — narrowed to the PROVEN 16410 trigger ──
# Probe evidence (DocumentDB 5.0.0, ds=3):
#   {new: "$name"}       → OK   (plain rename, leading-$ ref, very common)
#   {x:   "$a.b"}        → OK   (nested path ref)
#   {x:   "$a.$b"}       → FAIL 16410 "Fieldpath should not contain '$'"
# So 16410 fires ONLY when the path portion AFTER the leading sigil still contains '$'.
# The original "flag any leading-$ string" would have killed legal renames (regression).
def _is_embedded_dollar_fieldpath(v: str) -> bool:
    """True for a field-path string DocumentDB rejects with 16410: starts with '$'
    AND the remainder after the leading '$' still contains '$' (e.g. "$a.$b").
    Plain references like "$name" / "$a.b" return False."""
    return v.startswith("$") and "$" in v[1:]


def _has_embedded_dollar_fieldpath_in_project(pipeline: list) -> bool:
    """Scan $project/$addFields/$set stage bodies (recursively, covering operator-nested
    string values too) for an embedded-$ fieldpath. Detector for project_no_dollar_fieldpath."""
    PROJECT_STAGES = ("$project", "$addFields", "$set")

    def _scan(v: Any) -> bool:
        if isinstance(v, str):
            return _is_embedded_dollar_fieldpath(v)
        if isinstance(v, dict):
            return any(_scan(x) for x in v.values())
        if isinstance(v, list):
            return any(_scan(x) for x in v)
        return False

    for stage in pipeline:
        if not isinstance(stage, dict):
            continue
        for sname, sbody in stage.items():
            if sname in PROJECT_STAGES and _scan(sbody):
                return True
    return False


# ── R2: dotted stage variant — inspect the stage body, do NOT reject the whole stage ──
# Probe evidence: basic $lookup (localField/foreignField) → OK; $lookup with let/pipeline → FAIL 304.
def _has_lookup_let_pipeline(pipeline: list) -> bool:
    """True if any $lookup stage uses the let/pipeline sub-query form. Basic
    localField/foreignField $lookup returns False (legal on DocumentDB)."""
    for stage in pipeline:
        if isinstance(stage, dict):
            body = stage.get("$lookup")
            if isinstance(body, dict) and ("let" in body or "pipeline" in body):
                return True
    return False


# ── R3: small registries so detection logic is data-driven and extensible ──
# Dotted (variant-of-a-stage) restrictions need a body-inspecting detector.
_STAGE_VARIANT_DETECTORS: dict[str, Callable[[list], bool]] = {
    "$lookup.let_pipeline": _has_lookup_let_pipeline,
}
# Syntax constraints (named, not a stage/operator) need a detector per constraint id.
_SYNTAX_CONSTRAINT_DETECTORS: dict[str, Callable[[list], bool]] = {
    "project_no_dollar_fieldpath": _has_embedded_dollar_fieldpath_in_project,
}


def validate_pipeline_against_caps(pipeline: list, caps: dict | None) -> dict | None:
    """Return an LLM-readable error dict if the pipeline violates caps, else None.

    Generic & flavor-agnostic: only restrictions present in `caps` are enforced.
    caps is None or has empty restrictions (native flavor) → always returns None (Req 5.4/5.6).
    """
    if not caps:
        return None
    hints = {h.get("restriction"): h.get("suggestion")
             for h in caps.get("equivalent_hints", [])}

    # (1) unsupported aggregation operators — may be nested anywhere in expressions.
    used_ops = set(_iter_operators(pipeline))
    for op in caps.get("unsupported_ops", []):
        if op in used_ops:
            return _cap_error(op, hints.get(op), caps)

    # (2) unsupported stage variants.
    #   - dotted id (e.g. "$lookup.let_pipeline"): use a body-inspecting detector so a
    #     legal basic $lookup is NOT rejected (R2). Unknown dotted id w/o a detector →
    #     do not block (let the driver surface it, INV-ERRCODE preserved).
    #   - flat id (e.g. "$facet"): reject when that stage appears in the pipeline.
    stage_names = _stage_names(pipeline)
    for variant in caps.get("unsupported_stage_variants", []):
        if "." in variant:
            detector = _STAGE_VARIANT_DETECTORS.get(variant)
            if detector is not None and detector(pipeline):
                return _cap_error(variant, hints.get(variant), caps)
        elif variant in stage_names:
            return _cap_error(variant, hints.get(variant), caps)

    # (3) syntax constraints — registry of constraint id → detector. A constraint with
    #     no registered detector is not enforced here (driver surfaces it; no false block).
    for constraint in caps.get("syntax_constraints", []):
        detector = _SYNTAX_CONSTRAINT_DETECTORS.get(constraint)
        if detector is not None and detector(pipeline):
            return _cap_error(constraint, hints.get(constraint), caps)

    return None


def _cap_error(restriction: str, suggestion: str | None, caps: dict) -> dict:
    """Build the {reason, suggested_next_step} payload (T4: LLM-readable error).

    reason is flavor-neutral on purpose: it names the restriction and that the step was
    intercepted before execution, without leaking the runtime flavor token. The actionable
    remediation lives in suggested_next_step."""
    return {
        "error": "capability_violation",
        "reason": (
            f"目标数据源不支持 {restriction}, 该 step 在执行前被拦截 (避免触发驱动层原始错误)."
        ),
        "suggested_next_step": suggestion or GENERIC_RESTRICTION_HINT,
        "restriction": restriction,
        "equivalent_hints": caps.get("equivalent_hints", []),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  执行
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlanExecutionResult:
    """Plan 执行的完整结果 — 包含所有中间结果便于调试."""
    step_results: dict[int, list[dict]] = field(default_factory=dict)
    step_exports: dict[int, dict[str, list[Any]]] = field(default_factory=dict)
    final: list[dict] = field(default_factory=list)
    # §4.6 渲染源截断显式: 末步走 mode=render, 疑似截断时补 count 拿精确总数
    final_truncated: bool = False
    final_total_row_count: int = 0

    @property
    def last_step_idx(self) -> int:
        return max(self.step_results.keys()) if self.step_results else 0


async def _execute_sql_step(
    step, slug: str, ns_id: int, prev_vars: dict[int, dict[str, Any]],
    *, mode: ExecuteMode = "single",
) -> tuple[list[dict], bool, int]:
    """SQL 型 step 执行 (MySQL / Oracle 共用): 通过 driver 层执行 SQL.

    返回 (rows, truncated, total_row_count). 末步 mode='render' 时疑似截断补 count.
    step.query 应为 {"sql": "SELECT ..."} 形态.
    render 补 count 路径通过 driver.strip_outer_row_limit() 剥离行数保护,
    不直接引用 MySQLDriver 或 OracleDriver 具体类.
    """
    from app.db.metadata import async_session
    from app.engine.drivers import get_driver
    from app.engine.tools._resolve_ds import resolve_ds

    db_type = step.db_type
    async with async_session() as db:
        ds = await resolve_ds(db, ns_id, db_type, step.database)
    if ds is None:
        raise PlanExecutionError(
            step.step_idx,
            RuntimeError(
                f"未找到 database={step.database} 的 {db_type} datasource (ns={ns_id})"
            ),
        )

    driver = get_driver(db_type)

    # 变量替换 query
    resolved_query = _resolve_vars(step.query, prev_vars)

    # 确保 query 有 sql key
    if "sql" not in resolved_query:
        raise PlanExecutionError(
            step.step_idx,
            RuntimeError(f"{db_type} step 需要 query.sql 字段"),
        )

    # SQL 串里嵌入的 {{stepN.var}} (如 IN (...)) 渲染为安全字面量列表
    # (_resolve_vars 只做整串替换, 嵌入式变量留给这里处理)
    try:
        resolved_query = {
            **resolved_query,
            "sql": _render_sql_vars(resolved_query["sql"], prev_vars),
        }
    except VariableResolutionError as e:
        raise PlanExecutionError(step.step_idx, e) from e

    result = await driver.execute_query(ds, step.collection, resolved_query, mode=mode)
    rows = list(result.get("rows", []))
    truncated = bool(result.get("truncated"))
    total = len(rows)
    if mode == "render" and truncated:
        # 疑似截断: 剥离最外层行保护后补 count, 拿精确总数 (不被 planner 末步保护封顶).
        # 通过 SqlDataSourceDriver 协议方法调用, 不引用具体 driver 类.
        count_sql = driver.strip_outer_row_limit(resolved_query["sql"])  # type: ignore[attr-defined]
        try:
            cres = await driver.execute_query(
                ds, step.collection, {**resolved_query, "sql": count_sql}, mode="count",
            )
            crows = cres.get("rows") or []
            first = crows[0] if crows and isinstance(crows[0], dict) else {}
            total = int(first.get("cnt", first.get("count", 0)) or 0)
            truncated = total > settings.render_row_limit
        except Exception:  # noqa: BLE001 — count 失败保守降级: 仍如实告知截断, 总数缺省
            total = 0
            truncated = True
    return rows, truncated, total


async def _execute_mongo_step(
    step, slug: str, ns_id: int, prev_vars: dict[int, dict[str, Any]],
    *, mode: ExecuteMode = "single",
) -> tuple[list[dict], bool, int]:
    """MongoDB step 执行: 通过 MongoDriver 执行.

    返回 (rows, truncated, total_row_count). 末步 mode='render' 时疑似截断补 count.
    将 PlanStep 的 operation/pipeline/query/projection/sort 转为
    MongoDriver.execute_query() 期望的 query dict 格式.
    """
    from app.db.metadata import async_session
    from app.engine.drivers import get_driver
    from app.engine.drivers.mongo import _strip_tail_row_stages
    from app.engine.tools._resolve_ds import resolve_ds

    async with async_session() as db:
        ds = await resolve_ds(db, ns_id, step.db_type, step.database)
    if ds is None:
        raise PlanExecutionError(
            step.step_idx,
            RuntimeError(f"未找到 database={step.database} 的 {step.db_type} datasource (ns={ns_id})"),
        )

    driver = get_driver(step.db_type)

    # 变量替换 pipeline/query
    resolved_pipeline = _resolve_vars(step.pipeline, prev_vars) if step.pipeline else []
    resolved_query = _resolve_vars(step.query, prev_vars) if step.query else {}

    # 构造 MongoDriver 期望的 query dict
    if step.operation == "aggregate":
        query_payload = {"pipeline": resolved_pipeline}
    elif step.operation == "find":
        # find 转为等价 aggregate pipeline
        pipeline: list[dict] = []
        if resolved_query:
            pipeline.append({"$match": resolved_query})
        resolved_projection = _resolve_vars(step.projection, prev_vars) if step.projection else {}
        if resolved_projection:
            pipeline.append({"$project": resolved_projection})
        resolved_sort = _resolve_vars(step.sort, prev_vars) if step.sort else []
        if resolved_sort:
            # sort 可能是 [[field, direction], ...] 或 {field: direction}
            if isinstance(resolved_sort, list):
                sort_dict = {k: v for k, v in resolved_sort}
            else:
                sort_dict = resolved_sort
            pipeline.append({"$sort": sort_dict})
        query_payload = {"pipeline": pipeline} if pipeline else {"filter": resolved_query}
    elif step.operation == "count_documents":
        query_payload = {"filter": resolved_query}
    else:
        raise PlanExecutionError(
            step.step_idx,
            RuntimeError(f"不支持的 MongoDB operation: {step.operation}"),
        )

    mode_for_data = "count" if step.operation == "count_documents" else mode
    result = await driver.execute_query(ds, step.collection, query_payload, mode=mode_for_data)
    rows = list(result.get("rows", []))
    truncated = bool(result.get("truncated"))
    total = len(rows)
    if mode == "render" and mode_for_data == "render" and truncated:
        # 疑似截断: 对【executor 侧剥离尾部 $limit/$skip/$sample 的同一查询】补 count.
        # driver count 模式按既有不变量刻意不剥离 $limit, 故剥离责任必须在 executor.
        count_payload = dict(query_payload)
        pl = count_payload.get("pipeline")
        if isinstance(pl, list):
            count_payload["pipeline"] = _strip_tail_row_stages(pl)
        try:
            cres = await driver.execute_query(ds, step.collection, count_payload, mode="count")
            crows = cres.get("rows") or []
            first = crows[0] if crows and isinstance(crows[0], dict) else {}
            total = int(first.get("count", first.get("cnt", 0)) or 0)
            truncated = total > settings.render_row_limit
        except Exception:  # noqa: BLE001 — count 失败保守降级: 仍如实告知截断
            total = 0
            truncated = True
    return rows, truncated, total


async def _resolve_step_caps(ns_id: int, db_type: str, database: str) -> dict | None:
    """Resolve the document datasource capabilities for a step. Failure-safe.

    resolve_ds → db_profile caps projection. Any missing datasource
    or empty profile → None (pre-validation must never block execution).
    """
    import json as _json

    from app.db.metadata import async_session
    from app.engine.tools._db_profile_projector import _project_db_profile
    from app.engine.tools._resolve_ds import resolve_ds

    async with async_session() as db:
        ds = await resolve_ds(db, ns_id, db_type, database)
    if ds is None:
        return None
    profile = _json.loads(ds.db_profile_json or "{}")
    caps = _project_db_profile(profile, "caps")
    return dict(caps) if caps else None


@observe(name="plan_executor", as_type="span", capture_input=False, capture_output=False)
async def execute_plan(
    plan: QueryPlan,
    slug: str,
    ns_id: int,
    sse_emit=None,
) -> PlanExecutionResult:
    """
    串行执行 plan. 每步按 step.db_type + step.database 选引擎执行.

    多态 dispatch:
    - db_type in SQL_DB_TYPES (mysql/oracle): 走 _execute_sql_step → 对应 driver
    - db_type="mongodb": 走 _execute_mongo_step → MongoDriver
    - 未知 db_type: 明确抛 UnsupportedDataSourceTypeError, 不落入 MongoDB 分支

    sse_emit: SSE 事件推送回调, 每 step 完成后 emit plan_step_done 事件.

    抛 PlanExecutionError 携带 step_idx 与原异常, 供上层降级 / Refiner 重试.
    """
    pre_validate_vars(plan)

    result = PlanExecutionResult()
    prev_vars: dict[int, dict[str, Any]] = {}
    lf = _lf_client()
    # 末步 = 渲染源, 走 mode="render" 用 IS_RENDER_ROW_LIMIT; 中间脚手架步仍 single.
    _last_idx = max((s.step_idx for s in plan.steps), default=0)
    _step_truncated: dict[int, bool] = {}
    _step_total: dict[int, int] = {}

    for step in plan.steps:
        log.info(
            "[plan_exec] step=%d db_type=%s db=%s target=%s op=%s",
            step.step_idx, step.db_type, step.database, step.collection,
            step.operation,
        )

        # 每步建一个子 span 便于 Langfuse 追踪
        step_span = None
        if lf is not None:
            try:
                step_span = lf.start_observation(
                    name=f"plan.step.{step.step_idx}", as_type="span",
                    input={
                        "db_type": step.db_type,
                        "database": step.database,
                        "collection": step.collection,
                        "operation": step.operation,
                    },
                )
            except Exception as e:
                log.warning("[plan_executor] langfuse step span 创建失败: %s", e, exc_info=True)
                step_span = None

        # ── 能力预校验 (defense-in-depth): 在派发 document 型 step 到 driver 之前拦截 ──
        # 解析该 step 的 datasource caps (failure-safe), 比对 resolved pipeline.
        # native/empty-caps step 与 relational step 不校验 → 直接放行到 driver.
        from app.engine.db_types import DOCUMENT_DB_TYPES
        if step.db_type in DOCUMENT_DB_TYPES:
            caps = await _resolve_step_caps(ns_id, step.db_type, step.database)
            resolved_pipeline = _resolve_vars(step.pipeline, prev_vars) if step.pipeline else []
            violation = validate_pipeline_against_caps(resolved_pipeline, caps)
            if violation is not None:
                log.warning(
                    "[plan_exec] step=%d capability pre-validation rejected: %s",
                    step.step_idx, violation["restriction"],
                )
                # R6: 把 reason 与 remediation 一并带进异常消息 (agent loop 的 _exec_tool
                # 把 str(e) 放进 error_message), 否则 agent 只看到 "blocked X" 没有 "do Y".
                msg = f"{violation['reason']} 建议: {violation['suggested_next_step']}"
                if step_span is not None:
                    try:
                        step_span.update(output={"error": msg}, level="ERROR")
                        step_span.end()
                    except Exception as span_err:
                        log.warning(
                            "[plan_executor] langfuse step span 错误写入失败: %s",
                            span_err, exc_info=True,
                        )
                raise PlanExecutionError(
                    step.step_idx,
                    RuntimeError(msg),
                    pipeline=resolved_pipeline,
                )

        try:
            from app.engine.db_types import DOCUMENT_DB_TYPES, SQL_DB_TYPES as _SQL_TYPES
            from app.engine.drivers._exceptions import UnsupportedDataSourceTypeError
            step_mode: ExecuteMode = "render" if step.step_idx == _last_idx else "single"
            if step.db_type in _SQL_TYPES:
                docs, _trunc, _total = await _execute_sql_step(
                    step, slug, ns_id, prev_vars, mode=step_mode,
                )
            elif step.db_type in DOCUMENT_DB_TYPES:
                docs, _trunc, _total = await _execute_mongo_step(
                    step, slug, ns_id, prev_vars, mode=step_mode,
                )
            else:
                raise UnsupportedDataSourceTypeError(
                    f"step_idx={step.step_idx} 不支持的 db_type={step.db_type!r}",
                    suggestion=f"当前仅支持: {sorted(_SQL_TYPES | DOCUMENT_DB_TYPES)}",
                )
            _step_truncated[step.step_idx] = _trunc
            _step_total[step.step_idx] = _total
        except Exception as e:
            if step_span is not None:
                try:
                    step_span.update(output={"error": str(e)}, level="ERROR")
                    step_span.end()
                except Exception as span_err:
                    log.warning("[plan_executor] langfuse step span 错误写入失败: %s", span_err, exc_info=True)
            raise PlanExecutionError(step.step_idx, e) from e

        result.step_results[step.step_idx] = docs
        exports = _extract_exports(docs, step.exports) if step.exports else {}
        result.step_exports[step.step_idx] = exports
        prev_vars[step.step_idx] = exports

        if step_span is not None:
            try:
                step_span.update(output={
                    "rows": len(docs),
                    "exports_count": {k: (len(v) if isinstance(v, list) else 1) for k, v in exports.items()},
                })
                step_span.end()
            except Exception as e:
                log.warning("[plan_executor] langfuse step span output 写入失败: %s", e, exc_info=True)

        # ── SSE plan_step_done 事件 (Stage 3: 跨源 plan 多步进度可视化) ──
        if sse_emit is not None:
            try:
                await sse_emit({"event": "plan_step_done", "data": {
                    "step_id": step.step_idx,
                    "db_type": step.db_type,
                    "target": step.collection,
                    "row_count": len(docs),
                    "exports": list(exports.keys()),
                }})
            except Exception as e:
                log.warning("[plan_executor] sse plan_step_done emit failed: %s", e, exc_info=True)

        log.info(
            "[plan_exec] step=%d docs=%d exports=%s",
            step.step_idx, len(docs),
            {k: (len(v) if isinstance(v, list) else 1) for k, v in exports.items()},
        )

    if result.step_results:
        last = max(result.step_results.keys())
        result.final = result.step_results[last]
        result.final_truncated = _step_truncated.get(last, False)
        result.final_total_row_count = _step_total.get(last, len(result.final))

    if lf is not None:
        try:
            lf.update_current_span(output={
                "strategy": plan.strategy,
                "steps": len(plan.steps),
                "final_rows": len(result.final),
            })
        except Exception as e:
            log.warning("[plan_executor] langfuse plan span output 写入失败: %s", e, exc_info=True)

    return result


__all__ = [
    "PlanExecutionError",
    "PlanExecutionResult",
    "VariableResolutionError",
    "execute_plan",
    "pre_validate_vars",
]
