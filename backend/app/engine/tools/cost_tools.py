"""Stage 4 Task 6 — cost-aware tools (estimate_query_cost).

帮 agent 决策"这查询能不能跑、要不要收窄 filter / abort":

- estimate_query_cost: read-only explain('executionStats') 估扫描行数 + 命中索引

事务契约: read-only, 不 commit, 不动 SQLite, 仅 mongo 直查.

实际数据访问统一走 execute_query (支持聚合); 本工具仅做只读代价预估.
"""
from __future__ import annotations

import logging
from typing import Any

from langfuse import observe

from app.config import settings

from ._mongo_helpers import close_db, get_mongo_db, record_span_io

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  estimate_query_cost — explain 估算
# ════════════════════════════════════════════

# explain 输出剪枝白名单 — LLM 用不到的元信息 / 长尾 plan 历史
_EXPLAIN_DROP_KEYS = frozenset({
    "$clusterTime", "operationTime", "serverInfo", "command",
    "rejectedPlans", "allPlansExecution",
})
_EXPLAIN_MAX_ARRAY_ITEMS = 20


def _prune_explain(node: Any) -> Any:
    """递归剪除元信息 + 截断长数组, 控制 explain 输出体积喂给 LLM."""
    if isinstance(node, dict):
        return {
            k: _prune_explain(v)
            for k, v in node.items()
            if k not in _EXPLAIN_DROP_KEYS
        }
    if isinstance(node, list):
        if len(node) > _EXPLAIN_MAX_ARRAY_ITEMS:
            return [_prune_explain(v) for v in node[:_EXPLAIN_MAX_ARRAY_ITEMS]] + [
                {"_truncated": len(node) - _EXPLAIN_MAX_ARRAY_ITEMS}
            ]
        return [_prune_explain(v) for v in node]
    return node


@observe(name="tool.estimate_query_cost")
async def estimate_query_cost(
    *, namespace_id: int, collection: str, filter: dict, database: str,
    sse_emit,
    pipeline_stages: list | None = None,
) -> dict:
    """走 explain 估代价, 完全 read-only — 双路径分发.

    - pipeline_stages 为空 / None → find filter 路径, 返结构化
      `{estimated_docs, hit_indexes, warning}`
    - pipeline_stages 非空        → aggregate explain 路径, 返
      `{mongo_version, explain_raw, hint}` 由 LLM 自决 (跨 mongo 版本零代码维护)
    """
    db_ = await get_mongo_db(namespace_id=namespace_id, database=database)
    try:
        if pipeline_stages:
            return await _estimate_aggregate(db_, collection, filter, pipeline_stages)
        result = await _estimate_find(db_, collection, filter)
        # ── P0-3 emit cost_warning — 超阈时推送 (find 路径专属) ──
        if (
            result.get("warning")
            and result.get("estimated_docs", 0) > settings.query_cost_single_layer_limit
        ):
            await sse_emit({"event": "cost_warning", "data": {
                "estimated_docs": result["estimated_docs"],
                "threshold": settings.query_cost_single_layer_limit,
                "advice": "考虑收窄 filter 或 abort; 若需精确 count 用 execute_query 带聚合",
            }})
        return result
    finally:
        close_db(db_)


async def _estimate_find(db_, collection: str, filter: dict) -> dict:
    """find filter 路径 — 结构化输出, 与历史调用方契约不变.

    DocumentDB 兼容: explain() 不返回 executionStats (仅 queryPlanner),
    此时 fallback 到 count_documents 获取真实匹配数.
    """
    explain = await db_[collection].find(filter).explain()
    stats = explain.get("executionStats", {}) if isinstance(explain, dict) else {}
    planner = explain.get("queryPlanner", {}) if isinstance(explain, dict) else {}
    hit_indexes = _collect_indexes(planner.get("winningPlan", {}))

    if stats:
        # 原生 MongoDB: executionStats 可用
        est = stats.get("totalDocsExamined", stats.get("nReturned", 0))
    else:
        # DocumentDB: executionStats 缺失, fallback count_documents
        est = await db_[collection].count_documents(filter)

    warning: str | None = None
    if est > settings.query_cost_single_layer_limit:
        warning = (
            f"single_layer_overflow (>{settings.query_cost_single_layer_limit:,})"
        )

    out = {"estimated_docs": est, "hit_indexes": hit_indexes, "warning": warning}
    record_span_io(
        input={"path": "find", "collection": collection, "filter_keys": list(filter.keys())},
        output={
            "estimated_docs": est,
            "index_count": len(hit_indexes),
            "has_warning": warning is not None,
            "fallback_count": not bool(stats),
        },
    )
    return out


async def _estimate_aggregate(
    db_, collection: str, filter: dict, pipeline_stages: list,
) -> dict:
    """aggregate explain 路径 — pruned raw + mongo_version 喂 LLM 自决."""
    server_info = await db_.client.server_info()
    mongo_version = server_info.get("version", "unknown")

    full_pipeline = ([{"$match": filter}] if filter else []) + pipeline_stages
    explain = await db_.command({
        "explain": {
            "aggregate": collection,
            "pipeline": full_pipeline,
            "cursor": {},
        },
        "verbosity": "executionStats",
    })
    pruned = _prune_explain(explain)

    out = {
        "mongo_version": mongo_version,
        "explain_raw": pruned,
        "hint": (
            "Read explain_raw to find: totalDocsExamined per stage, COLLSCAN signals, "
            "indexName usage, SORT without index, $lookup amplification. Decide whether "
            "to abort or narrow filter. mongo_version tells you which explain shape to expect."
        ),
    }
    record_span_io(
        input={
            "path": "aggregate",
            "collection": collection,
            "mongo_version": mongo_version,
            "stage_count": len(pipeline_stages),
        },
        output={"explain_bytes": len(str(pruned))},
    )
    return out


def _collect_indexes(plan: Any) -> list[str]:
    """DFS 抽 winningPlan 各级 indexName (winningPlan 嵌套不规则, 必须递归)."""
    names: list[str] = []
    if isinstance(plan, dict):
        idx = plan.get("indexName")
        if idx:
            names.append(idx)
        for v in plan.values():
            names.extend(_collect_indexes(v))
    elif isinstance(plan, list):
        for v in plan:
            names.extend(_collect_indexes(v))
    return names
