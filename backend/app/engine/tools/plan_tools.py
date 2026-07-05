"""Stage 4 Task 8 — plan_tools wrappers (generate / execute / chart).

Thin wrappers around plan_generator / plan_executor / visualizer for agent loop.
Agent passes targets/conditions/schemas as dicts; we rebuild Target/Condition/QueryPlan
dataclasses internally — never make agent LLM construct dataclasses.

Stage 4 Task 12 完成: dataclass 已迁出至 app.engine.plan_models, decomposer 全栈已删.
本模块是 agent loop 重��水合 Target/Condition/QueryPlan 的唯一合法入口.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langfuse import observe
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.plan_executor import execute_plan
from app.engine.plan_generator import generate_plan
from app.engine.plan_models import (
    PlanStep,
    QueryPlan,
)
from app.engine.tools._resolve_ds import resolve_ds
from app.engine.visualizer import _VALID_CHART_TYPES

from ._mongo_helpers import record_span_io

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  dict → dataclass 重建器
# ════════════════════════════════════════════

def _dict_to_plan_step(d: dict) -> PlanStep:
    return PlanStep(
        step_idx=d.get("step_idx", 0),
        database=d.get("database", ""),
        collection=d.get("collection", ""),
        operation=d.get("operation", "aggregate"),
        pipeline=list(d.get("pipeline") or []),
        query=dict(d.get("query") or {}),
        projection=dict(d.get("projection") or {}),
        sort=list(d.get("sort") or []),
        limit=d.get("limit", 1000),
        exports=list(d.get("exports") or []),
        db_type=d.get("db_type", "mongodb"),
    )


def _dict_to_query_plan(plan_dict: dict) -> QueryPlan:
    """从 dict 重建 QueryPlan (无 from_dict, 手工拼). 保持与 to_dict 对称."""
    return QueryPlan(
        strategy=plan_dict.get("strategy", "single_aggregate"),
        steps=[_dict_to_plan_step(s) for s in plan_dict.get("steps") or []],
        post_process=plan_dict.get("post_process", ""),
    )


# ════════════════════════════════════════════
#  Tool 1: generate_query_plan
# ════════════════════════════════════════════

@observe(name="tool.generate_query_plan")
async def generate_query_plan(
    *, db: AsyncSession, question: str, namespace_id: int,
    collections: list[dict], schemas: dict,
    filters: list[dict] | None = None,
    knowledge: list[str] | None = None,
    rules: list[str] | None = None,
) -> dict:
    """生成跨引擎多步查询 Plan. agent 友好接口 (dict in / dict out).

    collections 含每个 (db_type, database, collection); plan steps 据此按 db_type
    多态生成 (mysql=sql / mongodb=pipeline). plan_executor 按 (ns, db_type, database)
    反查 ds 执行.

    db / namespace_id 是 dispatcher 按签名注入的 runtime context (LLM 不可见, 不在
    TOOL_SPECS); 用于内部按集合反查 datasource 并解析 db_profile caps 投影.
    """
    # Resolve per-collection capabilities INTERNALLY (LLM never passes these).
    capabilities_by_target = await _resolve_caps_by_target(db, namespace_id, collections)
    plan = await generate_plan(
        question, collections, filters or [], schemas, knowledge, rules,
        capabilities_by_target=capabilities_by_target,
    )
    plan_dict = plan.to_dict()
    out: dict[str, Any] = {"plan": plan_dict}
    record_span_io(
        input={
            "question": question[:200],
            "collection_count": len(collections),
            "filter_count": len(filters or []),
            "schema_count": len(schemas),
            "caps_targets": sorted(capabilities_by_target.keys()),  # observability only
        },
        output={
            "plan_strategy": plan_dict.get("strategy"),
            "step_count": len(plan_dict.get("steps") or []),
        },
    )
    return out


# ────────────────────────────────────────────
#  内部: per-collection capability 解析 (LLM 不可见)
#  从 ds.db_profile_json 投影 caps (冷启动已探, 失败安全).
# ────────────────────────────────────────────

def _caps_target_key(db_type: str, database: str, collection: str) -> str:
    """Stable key joining a collection to its resolved capabilities.
    Must match the key used by _format_collections and the executor."""
    return f"[{db_type}] {database}.{collection}"


def _has_restrictions(caps: Mapping[str, Any]) -> bool:
    """True if caps declares any of the three restriction categories."""
    return bool(
        caps.get("unsupported_ops")
        or caps.get("unsupported_stage_variants")
        or caps.get("syntax_constraints")
    )


async def _resolve_caps_by_target(
    db: AsyncSession, namespace_id: int, collections: list[dict],
) -> dict[str, dict]:
    """Per-collection capability resolution at the DATASOURCE dimension.

    For each mongodb collection: db_profile caps projection (cold-start probed,
    failure-safe). Native/empty-restriction caps are omitted (no noise). mysql
    collections are skipped (no mongo capability concept).
    """
    import json as _json

    from app.engine.tools._db_profile_projector import _project_db_profile

    out: dict[str, dict] = {}
    for c in collections:
        db_type = (c.get("db_type") or "mongodb").strip()
        if db_type != "mongodb":
            continue
        database = (c.get("database") or "").strip()
        collection = (c.get("collection") or "").strip()
        if not database or not collection:
            continue
        ds = await resolve_ds(db, namespace_id, db_type, database)
        if ds is None:
            continue
        profile = _json.loads(ds.db_profile_json or "{}")
        caps = _project_db_profile(profile, "caps")
        if _has_restrictions(caps):
            out[_caps_target_key(db_type, database, collection)] = dict(caps)
    return out


# ════════════════════════════════════════════
#  Tool 2: execute_plan_tool
#  (名称避让 plan_executor.execute_plan)
# ════════════════════════════════════════════

@observe(name="tool.execute_plan")
async def execute_plan_tool(
    *, namespace_id: int, ns_slug: str, plan: dict,
    sse_emit=None,
) -> dict:
    """串行执行 multi-step Plan, 返回最终行 + 列名 + 步骤 trace."""
    qp = _dict_to_query_plan(plan)
    result = await execute_plan(qp, slug=ns_slug, ns_id=namespace_id, sse_emit=sse_emit)

    rows: list[dict] = list(getattr(result, "final", []) or [])
    columns: list[str] = []
    if rows and isinstance(rows[0], dict):
        # 用首行 key 顺序作为 columns; dict 默认 insertion order (3.7+ 稳定)
        columns = list(rows[0].keys())

    out = {
        "rows": rows,
        "columns": columns,
        "last_step_idx": getattr(result, "last_step_idx", 0),
        "truncated": getattr(result, "final_truncated", False),
        "rendered_row_count": len(rows),
        "total_row_count": getattr(result, "final_total_row_count", len(rows)),
    }
    if out["truncated"]:
        out["suggestion"] = (
            f"结果共 {out['total_row_count']} 行, 超渲染上限 {settings.render_row_limit} 行已截断; "
            "若用于图表请改用更粗聚合粒度 (如按日→按月) 或缩小范围后重试, 不要分多次查询拼接."
        )
    record_span_io(
        input={
            "namespace_id": namespace_id,
            "ns_slug": ns_slug,
            "plan_steps": len(plan.get("steps") or []),
        },
        output={
            "row_count": len(rows),
            "column_count": len(columns),
            "last_step_idx": out["last_step_idx"],
        },
    )
    return out


# ════════════════════════════════════════════
#  Tool 3: present_result_tool (声明最终结果集 + 图表列角色)
#  反转自 recommend_chart: 不收 rows 数组, 只收 ref(指针) + chart_spec(列角色).
#  工具内 tool_trace 不可见 → 只校验+回显; 渲染在 api finalization 按 ref 取全量数据.
# ════════════════════════════════════════════

@observe(name="tool.present_result")
def present_result_tool(*, ref: str, chart_spec: dict) -> dict:
    """声明哪次执行(ref)是最终结果集 + 图表列角色(chart_spec). 不收数据行."""
    if not ref or not isinstance(ref, str):
        return {
            "status": "error", "error_type": "BadRef",
            "error_message": "ref 必须是指向某次 execute_query/execute_plan 的 tool_call_id",
            "suggested_next_step": (
                "检查 ref 是否指向一次成功的 execute_query/execute_plan 调用; "
                "无成功执行则用文字向用户说明查询未完成"
            ),
        }
    spec = dict(chart_spec or {})
    ct = spec.get("chart_type")
    if ct not in _VALID_CHART_TYPES:
        spec["chart_type"] = "table"  # 归一化, 与渲染端 fail-safe 一致
    out = {"status": "ok", "ref": ref, "chart_spec": spec}
    record_span_io(
        input={"ref": ref, "chart_type": spec.get("chart_type"),
               "x": spec.get("x", ""), "series_by": spec.get("series_by", ""),
               "value": spec.get("value", "")},
        output={"status": "ok"},
    )
    return out
