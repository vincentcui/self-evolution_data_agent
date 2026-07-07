"""
PlanGenerator — LLM 生成跨引擎查询执行计划 (Plan A)

输入: question + collections (含 db_type) + filters + schemas + knowledge + rules
输出: QueryPlan {strategy, steps[]} — 单步 或 多步跨库/跨引擎串行

跨引擎 (Plan A):
- collections[].db_type 决定每个 step 走 mysql 还是 mongodb
- mysql step: operation="sql", query={"sql": "SELECT ..."}
- mongodb step: operation ∈ {find, aggregate, count_documents}, 用 pipeline/query

核心约束:
- 跨库/跨引擎 → multi_step, 一步一库
- 同库 mongodb → 可 single_aggregate + $lookup 或拆 multi_step (按 LLM 判断)
- 每步声明 exports 字段给后续步骤通过 {{stepN.var}} 引用
- 每步必有行数保护 ($limit 或 SQL LIMIT)

Knowledge / rules 业务规则注入:
- enum 映射 (如 rules 里声明 status 字段的 0/1/2 语义)
- 特定字段名大小写规则 (camelCase vs snake_case)
- 业务软约束 (如"默认过滤未审核" 等)
"""

from __future__ import annotations

import asyncio
import json
import logging

from langfuse import observe

from app.engine.json_parser import parse_llm_json
from app.engine.llm import chat_completion
from app.engine.plan_models import GENERIC_RESTRICTION_HINT, PlanStep, QueryPlan
from app.tracing import get_client as _lf_client

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  异常
# ══════════════════════════════════════════════════════════════════════════════

class PlanGenerationError(RuntimeError):
    """PlanGenerator 输出无效或 LLM 失败."""


# ══════════════════════════════════════════════════════════════════════════════
#  Prompt
# ══════════════════════════════════════════════════════════════════════════════

_PLANNER_SYSTEM = """Role: 你是跨引擎查询规划专家, 把已备齐的上下文翻成可串行执行的多步查询 Plan.
Goal: 产出一个合法 Plan — 每步绑定一个 (db_type, database, collection), 按 db_type 用对应查询形态, 步骤间可用变量传递结果, 且每步都符合该集合所在数据源的能力限制.

<inputs>
- 用户问题
- collections: 本次涉及的全部 (db_type, database, collection); 某些集合下方附【能力限制】块, 表示该集合所在数据源不支持的算子/stage 形态/语法
- filters: 过滤提示 (collection/field/op/value), 结合 schema 与规则收敛为实际查询条件
- schemas: 各表/集合的字段定义
- knowledge / rules: 业务知识与强制规则 (枚举映射 / 字段命名 / 软约束)
</inputs>

<step_shape>
按 step 的 db_type 选查询形态:
- db_type="mongodb": operation ∈ {aggregate, find, count_documents}; 用 "pipeline" (aggregate) 或 "query" (find/count). 末尾加 $limit.
- db_type="mysql": operation="sql"; 用 "query": {"sql": "SELECT ... LIMIT n"}. 只允许 SELECT.
- db_type="oracle": operation="sql"; 用 "query": {"sql": "SELECT ..."}.
  使用 Oracle SQL 方言. 只允许 SELECT.
  Oracle 不支持 MySQL 的 LIMIT 语法. 如需行数限制,
  用 FETCH FIRST n ROWS ONLY 或 WHERE ROWNUM <= n.
  执行层会自动包装行数保护, 不强制要求 SQL 内写 ROWNUM;
  若 LLM 写了则执行层会在 render/count 路径剥离并覆盖.
每步在 "exports" 声明要传给后续步骤的字段名.
</step_shape>

<humanize>
展示列输出可读名不输出编码: 某列存编码 (状态码/分类id/外键id) 且 schema 有可读来源时,
生成查询就翻译 — 有维表则 JOIN 取 label 列; 无维表但 rules/knowledge 给了枚举语义则 CASE WHEN 翻.
找不到可读来源则保留原列 (渲染端 code_label_map 兜底).
</humanize>

<capability_constraints>
某集合下方若附【能力限制】块, 说明该集合所在数据源不支持其中列出的算子 / stage 形态 / 语法约束 (没有该块的集合不受限制, 按通用 MongoDB 语法构造即可).
为该集合构造 pipeline 时, 先比对【能力限制】列出的三类项:
- 不支持算子: 该集合的 pipeline 避免使用这些聚合算子.
- 不支持 stage 形态: 避免使用这些 stage 或其受限选项形态.
- 语法约束: 写法遵守这些约束.
命中任一项时, 优先采用该项箭头 (→) 后给出的等效写法建议; 没有给出建议时, 改用不触发该限制的等价表达 (例如拆成多步、把表达式上移到 $set、关联过滤后置到下游 $match).
仅依据【能力限制】块的内容判断, 不要假设未列出的限制.
</capability_constraints>

<variable_passing>
后续步骤引用前序导出值, 格式 "{{step<N>.<varName>}}" (N=目标步骤 step_idx, varName 必须在该步 exports 中).
- mongodb 下游: 变量作为独立值, 例 {"categoryId": {"$in": "{{step1.ids}}"}}
- mysql 下游: 变量嵌在 SQL 的 IN 列表里, 例 "WHERE category_id IN ({{step1.ids}})" (执行层自动渲染为安全字面量列表)
</variable_passing>

【下面示例的 database / collection / field 名仅为格式演示, 真正生成时严格按输入的 schemas, 不要照抄示例字面量】
【输出严格 JSON, 不要 markdown 围栏, 不要解释】

<example name="跨引擎: MySQL 取 ID → MongoDB 聚合">
{
  "strategy": "multi_step",
  "steps": [
    {
      "step_idx": 1,
      "db_type": "mysql",
      "database": "shop_db",
      "collection": "orders",
      "operation": "sql",
      "query": {"sql": "SELECT DISTINCT product_id FROM orders WHERE status = 1 LIMIT 1000"},
      "exports": ["product_id"]
    },
    {
      "step_idx": 2,
      "db_type": "mongodb",
      "database": "catalog_db",
      "collection": "products",
      "operation": "aggregate",
      "pipeline": [
        {"$match": {"productId": {"$in": "{{step1.product_id}}"}}},
        {"$group": {"_id": "$productType", "count": {"$sum": 1}}},
        {"$limit": 1000}
      ],
      "exports": ["_id", "count"]
    }
  ],
  "post_process": "最后一步结果即为图表数据"
}
</example>

<example name="Oracle 单步 SQL 查询">
{
  "strategy": "single_aggregate",
  "steps": [
    {
      "step_idx": 1,
      "db_type": "oracle",
      "database": "sales_svc",
      "collection": "ORDERS",
      "operation": "sql",
      "query": {"sql": "SELECT ORDER_DATE, SUM(AMOUNT) AS total FROM ORDERS"},
      "exports": ["ORDER_DATE", "total"]
    }
  ],
  "post_process": ""
}
</example>

<example name="同库 MongoDB 单步聚合">
{
  "strategy": "single_aggregate",
  "steps": [
    {
      "step_idx": 1,
      "db_type": "mongodb",
      "database": "catalog_db",
      "collection": "products",
      "operation": "aggregate",
      "pipeline": [
        {"$match": {"categoryName": {"$regex": "phone", "$options": "i"}}},
        {"$group": {"_id": "$productType", "count": {"$sum": 1}}},
        {"$limit": 1000}
      ],
      "exports": ["_id", "count"]
    }
  ],
  "post_process": ""
}
</example>

【规则】
1. 跨 database 或跨 db_type 必须 multi_step (一步一库); 同 mongodb 库可 single_aggregate + $lookup
2. 每步必须有行数保护: mongodb 末尾 $limit,
   mysql 的 SQL 带 LIMIT n,
   oracle 的 SQL 带 FETCH FIRST n ROWS ONLY 或交执行层包装
3. 变量引用 "{{step<N>.<varName>}}" 的 varName 必须在 step N 的 exports 中
4. 前一步结果传给下一步: mongodb 用 $group+$push 或 $project 导出字段;
   mysql 用 SELECT 列名导出, 都在 exports 声明
5. 业务规则必须落到查询条件. 枚举值含义以输入的 knowledge/rules 为准,
   不要凭字面猜 (例: rules 写 "status=1 表示有效", 仅取有效用 status=1)
6. 字段名/表名大小写严格按 schemas, 不要猜
7. 最后一步结果是要给用户展示的数据, 其他步骤只是中间数据
8. step_idx 从 1 开始连续递增, 与 {{stepN.xxx}} 的 N 对齐
9. mysql/oracle step 的 SQL 仅允许 SELECT, 禁止 INSERT/UPDATE/DELETE/DDL/PL/SQL/多语句
10. 每步 pipeline 符合该集合【能力限制】块列出的限制; 无该块的集合不受额外限制

【证据不足时】若 schemas 或 knowledge 信息不足以安全生成某一步 (例如找不到合适的关联字段), 返回 strategy="single_aggregate" 仅覆盖能确定的部分, 不要编造字段名/表名/值.
"""


# ══════════════════════════════════════════════════════════════════════════════
#  JSON 解析
# ══════════════════════════════════════════════════════════════════════════════

_VALID_OPS_BY_DBTYPE: dict[str, set[str]] = {
    "mongodb": {"find", "aggregate", "count_documents"},
    "mysql": {"sql"},
    "oracle": {"sql"},
}
_VALID_DB_TYPES = set(_VALID_OPS_BY_DBTYPE.keys())
_VALID_STRATEGIES = {"single_aggregate", "multi_step"}


def _parse_plan(raw: str) -> QueryPlan:
    data = parse_llm_json(raw, expect="dict")
    if data is None:
        raise PlanGenerationError(f"Plan JSON 解析失败: head={raw[:200]!r}")

    strategy = (data.get("strategy") or "").strip()
    if strategy not in _VALID_STRATEGIES:
        raise PlanGenerationError(f"无效 strategy={strategy!r}")

    steps_raw = data.get("steps") or []
    if not isinstance(steps_raw, list) or not steps_raw:
        raise PlanGenerationError("plan.steps 为空或非列表")

    steps: list[PlanStep] = []
    seen_idx: set[int] = set()
    for i, s in enumerate(steps_raw):
        if not isinstance(s, dict):
            log.warning("[plan_gen] step#%d 非 dict, 跳过: %r", i, s)
            continue
        idx = s.get("step_idx")
        if not isinstance(idx, int) or idx < 1:
            raise PlanGenerationError(f"step#{i} step_idx 非法: {idx!r}")
        if idx in seen_idx:
            raise PlanGenerationError(f"step_idx={idx} 重复")
        seen_idx.add(idx)

        db_type = (s.get("db_type") or "mongodb").strip()
        if db_type not in _VALID_DB_TYPES:
            raise PlanGenerationError(
                f"step_idx={idx} db_type={db_type!r} 不在白名单 {sorted(_VALID_DB_TYPES)}"
            )

        op = (s.get("operation") or "").strip()
        valid_ops = _VALID_OPS_BY_DBTYPE[db_type]
        if op not in valid_ops:
            raise PlanGenerationError(
                f"step_idx={idx} db_type={db_type} operation={op!r} 不在白名单 {sorted(valid_ops)}"
            )

        db_name = (s.get("database") or "").strip()
        coll = (s.get("collection") or "").strip()
        if not db_name or not coll:
            raise PlanGenerationError(f"step_idx={idx} 缺 database/collection")

        query = s.get("query") or {}
        pipeline = s.get("pipeline") or []
        from app.engine.db_types import DOCUMENT_DB_TYPES, SQL_DB_TYPES as _SQL_DB_TYPES
        if db_type in _SQL_DB_TYPES:
            if not isinstance(query, dict) or not (query.get("sql") or "").strip():
                raise PlanGenerationError(f"step_idx={idx} {db_type} step 缺 query.sql")
        elif db_type in DOCUMENT_DB_TYPES:
            if not pipeline and not query:
                raise PlanGenerationError(
                    f"step_idx={idx} {db_type} step 缺 pipeline/query"
                )
        else:
            raise PlanGenerationError(
                f"step_idx={idx} 不支持的 db_type={db_type!r}, "
                f"仅支持: {sorted(_SQL_DB_TYPES | DOCUMENT_DB_TYPES)}"
            )

        steps.append(PlanStep(
            step_idx=idx,
            database=db_name,
            collection=coll,
            operation=op,
            pipeline=pipeline,
            query=query,
            projection=s.get("projection") or {},
            sort=s.get("sort") or [],
            exports=[str(x) for x in (s.get("exports") or []) if x],
            db_type=db_type,
        ))

    # 按 step_idx 排序, 保证执行顺序
    steps.sort(key=lambda s: s.step_idx)

    # 校验 step_idx 连续: 1, 2, 3, ...
    for expected, step in enumerate(steps, start=1):
        if step.step_idx != expected:
            raise PlanGenerationError(
                f"step_idx 不连续: 预期 {expected}, 实际 {step.step_idx}"
            )

    # multi_step 至少两步; single_aggregate 必须单步
    if strategy == "multi_step" and len(steps) < 2:
        log.warning("[plan_gen] strategy=multi_step 但只有 1 步, 退化到 single_aggregate")
        strategy = "single_aggregate"
    if strategy == "single_aggregate" and len(steps) > 1:
        raise PlanGenerationError(
            f"single_aggregate 但产出 {len(steps)} 步, 应为 multi_step"
        )

    return QueryPlan(
        strategy=strategy,
        steps=steps,
        post_process=(data.get("post_process") or "").strip(),
        raw_llm_output=raw,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════════════════════════════════════════

def _format_collections(
    collections: list[dict],
    capabilities_by_target: dict | None = None,
) -> str:
    """渲染本次涉及的 (db_type, database, collection) 列表, 对携带能力限制的集合
    在其下追加一个【能力限制】runtime-data block (resolved from datasource).

    capabilities_by_target: 每集合解析出的 db_profile caps 投影; key 为
    "[db_type] database.collection". system prompt 不含任何限制内容, 全部 runtime 注入.
    """
    if not collections:
        return "(无)"
    caps_map = capabilities_by_target or {}
    lines: list[str] = []
    for c in collections:
        db_type = c.get("db_type", "mongodb")
        database = c.get("database", "")
        collection = c.get("collection", "")
        key = f"[{db_type}] {database}.{collection}"
        lines.append(f"- {key}")
        caps = caps_map.get(key)
        if caps:
            block = _render_caps_block(caps, indent="  ")
            if block:
                lines.append(block)
    return "\n".join(lines)


def _render_caps_block(caps: dict, indent: str = "  ") -> str:
    """渲染单个集合的三类能力限制 + 对应等效改写提示, 作为 runtime-data block.

    Flavor-agnostic: 原样打印 resolved caps 内容, 不含任何 flavor/算子硬编码.

    R6: 无配置 hint 的限制 id 使用通用兜底建议 (GENERIC_RESTRICTION_HINT), 避免把
    内部 token (如 'project_no_dollar_fieldpath') 裸露给规划器.
    """
    hints = {
        h["restriction"]: h["suggestion"]
        for h in caps.get("equivalent_hints", [])
    }
    out: list[str] = [f"{indent}【能力限制】(数据源类型: {caps.get('flavor', '')})"]
    labels = (
        ("unsupported_ops", "不支持算子"),
        ("unsupported_stage_variants", "不支持 stage 形态"),
        ("syntax_constraints", "语法约束"),
    )
    any_item = False
    for field_name, label in labels:
        for item in caps.get(field_name, []):
            any_item = True
            sug = hints.get(item) or GENERIC_RESTRICTION_HINT
            out.append(f"{indent}  - [{label}] {item} → {sug}")
    if not any_item:
        return ""  # defensive: 调用方仅对携带限制的集合传入 caps
    return "\n".join(out)


def _format_filters(filters: list[dict]) -> str:
    """渲染过滤提示 (collection/field/op/value)."""
    if not filters:
        return "(无, 由规划器结合 schema/rules 自行推导)"
    return json.dumps(filters, ensure_ascii=False, indent=2)


def _format_schemas(schemas: dict, max_per_coll: int = 4000) -> str:
    """渲染各表/集合 schema. value 可能是字符串 (canonical 文本) 或 dict (结构化)."""
    blocks: list[str] = []
    for name, doc in schemas.items():
        if isinstance(doc, str):
            snippet = doc[:max_per_coll]
        else:
            snippet = json.dumps(doc, ensure_ascii=False)[:max_per_coll]
        blocks.append(f"### {name}\n{snippet}")
    return "\n\n".join(blocks) if blocks else "(无 schema, 可能未完成训练)"


def generate_plan_sync(
    question: str,
    collections: list[dict],
    filters: list[dict],
    schemas: dict,
    knowledge: list[str] | None = None,
    rules: list[str] | None = None,
    capabilities_by_target: dict | None = None,
    namespace_id: int | None = None,
) -> QueryPlan:
    """同步入口 — 供 asyncio.to_thread 包裹.

    capabilities_by_target: 每集合解析出的 db_profile caps 投影 (runtime data),
    仅用于 user-message 渲染; system prompt 保持稳定常量. (Task 4/5 接入渲染)
    """
    knowledge = knowledge or []
    rules = rules or []
    collections = collections or []
    filters = filters or []

    user_msg_parts: list[str] = []
    user_msg_parts.append(f"【用户问题】\n{question}")
    user_msg_parts.append(f"【涉及集合/表 (db_type database.collection)】\n{_format_collections(collections, capabilities_by_target)}")
    user_msg_parts.append(f"【过滤提示】\n{_format_filters(filters)}")
    user_msg_parts.append(f"【Schema】\n{_format_schemas(schemas)}")

    if knowledge:
        user_msg_parts.append(
            "【业务知识规则 (必须遵守)】\n" + "\n".join(f"- {k}" for k in knowledge[:10])
        )
    if rules:
        user_msg_parts.append(
            "【Namespace 强制规则】\n" + "\n".join(f"- {r}" for r in rules)
        )

    user_msg = "\n\n".join(user_msg_parts)

    log.info(
        "[plan_gen] 生成 plan collections=%d filters=%d schemas=%d knowledge=%d",
        len(collections), len(filters), len(schemas), len(knowledge),
    )

    raw = chat_completion(
        messages=[
            {"role": "system", "content": _PLANNER_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=8192,  # noqa: hardcode — 开思考后预留 CoT 预算
        namespace_id=namespace_id,
        thinking=None,   # None → 尊重 settings.llm_thinking_enabled 全局开关
    )
    if not raw or not raw.strip():
        raise PlanGenerationError("PlanGenerator LLM 返回空响应")

    plan = _parse_plan(raw)
    log.info(
        "[plan_gen] plan 完成 strategy=%s steps=%d databases=%s",
        plan.strategy, len(plan.steps), plan.databases,
    )
    return plan


@observe(name="plan_generator", as_type="span", capture_input=False, capture_output=False)
async def generate_plan(
    question: str,
    collections: list[dict],
    filters: list[dict],
    schemas: dict,
    knowledge: list[str] | None = None,
    rules: list[str] | None = None,
    capabilities_by_target: dict | None = None,
    namespace_id: int | None = None,
) -> QueryPlan:
    plan = await asyncio.to_thread(
        generate_plan_sync, question, collections, filters,
        schemas, knowledge, rules, capabilities_by_target,
        namespace_id=namespace_id,
    )
    lf = _lf_client()
    if lf is not None:
        try:
            lf.update_current_span(output={
                "strategy": plan.strategy,
                "steps": len(plan.steps),
                "databases": plan.databases,
                "cross_db": len(plan.databases) > 1,
            })
        except Exception as e:
            log.warning("[plan_generator] langfuse span update failed: %s", e, exc_info=True)
    return plan


__all__ = [
    "PlanGenerationError",
    "PlanStep",
    "QueryPlan",
    "generate_plan",
    "generate_plan_sync",
]
