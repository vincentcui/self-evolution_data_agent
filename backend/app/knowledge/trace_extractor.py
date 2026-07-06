"""trace_json 机械字段抽取 — 不依赖 LLM, 纯结构化解析.

复用 engine/tools/registry.py 的 TOOL_TARGET_FIELD 映射.
与 agent end_turn 自动沉淀路径 (api/query.py) 共用,
保证 trace_refiner / async_extract 两条路径产出的 KE.payload 机械字段完全等价.
"""
from __future__ import annotations

from app.engine.tools.registry import TOOL_TARGET_FIELD


# ════════════════════════════════════════════
#  collection 抽取
# ════════════════════════════════════════════

def extract_collections(tool_trace: list[dict]) -> list[str]:
    """从 tool_trace 抽 collection 序列 (保序去重).

    覆盖 4 件套数据访问工具的 input.target. 字段映射来自
    engine/tools/registry.py::TOOL_TARGET_FIELD, 工具改名只改一处.
    """
    seen: dict[str, None] = {}
    for call in tool_trace or []:
        name = call.get("name", "")
        target_field = TOOL_TARGET_FIELD.get(name)
        if not target_field:
            continue
        target = (call.get("input") or {}).get(target_field)
        if isinstance(target, str) and target and target not in seen:
            seen[target] = None
    return list(seen.keys())


# ════════════════════════════════════════════
#  cost_strategy 推断
# ════════════════════════════════════════════

def derive_cost_strategy(tool_trace: list[dict]) -> str:
    """根据 tool_trace 中 execute_query 的 mode 维度推 cost_strategy.

    Returns: "default" | "count_only_first" | "batched_count_only"

    - 任一 execute_query mode=batched → batched_count_only
    - 否则任一 execute_query mode=count → count_only_first
    - 否则 → default
    """
    has_count = False
    for call in tool_trace or []:
        if call.get("name") != "execute_query":
            continue
        mode = (call.get("input") or {}).get("mode", "")
        if mode == "batched":
            return "batched_count_only"
        if mode == "count":
            has_count = True
    return "count_only_first" if has_count else "default"


# ════════════════════════════════════════════
#  join_fields 抽取
# ════════════════════════════════════════════

def extract_join_fields(final_pipeline: dict | None) -> list[dict]:
    """从 execute_plan 的 plan.steps 抽 $lookup join 字段.

    返回 [{"a": "<step_collection>.<localField>", "b": "<lookup.from>.<foreignField>"}].
    """
    if not final_pipeline or final_pipeline.get("type") != "execute_plan":
        return []
    out: list[dict] = []
    for step in final_pipeline.get("steps") or []:
        if not isinstance(step, dict):
            continue
        coll = step.get("collection")
        if not coll:
            continue
        for stage in step.get("pipeline") or []:
            if not isinstance(stage, dict):
                continue
            lookup = stage.get("$lookup")
            if not isinstance(lookup, dict):
                continue
            local_field = lookup.get("localField")
            foreign_field = lookup.get("foreignField")
            from_coll = lookup.get("from")
            if local_field and foreign_field and from_coll:
                out.append({
                    "a": f"{coll}.{local_field}",
                    "b": f"{from_coll}.{foreign_field}",
                })
    return out


def extract_final_pipeline(tool_trace: list[dict]) -> dict | None:
    """从后向前找最后一次 execute_plan 的 plan dict."""
    for call in reversed(tool_trace or []):
        if call.get("name") == "execute_plan":
            plan = (call.get("input") or {}).get("plan") or {}
            return {"type": "execute_plan", "steps": plan.get("steps", [])}
    return None


# ════════════════════════════════════════════
#  db_type / database 上下文抽取
# ════════════════════════════════════════════

_DB_CONTEXT_TOOLS = ("fetch_schema", "execute_query", "inspect_values", "execute_plan")


def extract_db_context(tool_trace: list[dict]) -> tuple[str | None, str | None]:
    """从 tool_trace 中第一个数据访问工具调用抽 (db_type, database).

    Returns: (db_type, database) — 任一缺失置 None.
    """
    for call in tool_trace or []:
        if call.get("name") not in _DB_CONTEXT_TOOLS:
            continue
        inp = call.get("input") or {}
        db_type = inp.get("db_type")
        database = inp.get("database")
        if db_type or database:
            return (
                db_type if isinstance(db_type, str) else None,
                database if isinstance(database, str) else None,
            )
    return (None, None)


# ════════════════════════════════════════════
#  query_plan / join_keys 抽取 (example payload 统一)
# ════════════════════════════════════════════

def normalize_query_plan(tool_trace: list[dict]) -> dict | None:
    """Unified query plan extractor covering both execute_plan and execute_query.

    Priority: execute_plan (multi-step).  Fallback: last successful execute_query (single-step).
    Returns None when neither is found in the trace.
    """
    from app.engine.db_types import SQL_DB_TYPES, DOCUMENT_DB_TYPES

    # ── Priority 1: execute_plan (multi-step) ──
    for call in reversed(tool_trace or []):
        if call.get("name") == "execute_plan":
            plan = (call.get("input") or {}).get("plan") or {}
            if plan.get("steps"):
                return {"steps": list(plan["steps"])}
            return None

    # ── Fallback: execute_query (single-step, MySQL/MongoDB direct) ──
    for call in reversed(tool_trace or []):
        if call.get("name") != "execute_query":
            continue
        inp = call.get("input") or {}
        query = inp.get("query")
        target = inp.get("target") or inp.get("collection") or ""
        database = inp.get("database") or ""
        db_type = str(inp.get("db_type", "mysql"))
        if not query or not isinstance(query, dict):
            continue
        if db_type in SQL_DB_TYPES:
            operation = "sql"
        elif db_type in DOCUMENT_DB_TYPES:
            operation = "aggregate"
        else:
            operation = "unknown"  # future paradigm: graph / time-series / etc.
        return {
            "steps": [{
                "db_type": db_type,
                "database": database,
                "collection": target,
                "operation": operation,
                "query": dict(query),
            }]
        }
    return None


def extract_join_keys(final_query_plan: dict | None) -> list[dict]:
    """Extract join keys — MySQL JOIN ON + MongoDB $lookup.

    Both sides of each join are collection.field — symmetric.
    MySQL JOINs are within one schema, MongoDB $lookups within one database.
    No db-prefix needed on either side.
    Returns [{"from": "orders.user_id", "to": "users.id"}, ...].
    """
    if not final_query_plan:
        return []
    out: list[dict] = []
    from app.engine.db_types import SQL_DB_TYPES, DOCUMENT_DB_TYPES

    for step in final_query_plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        db_type = str(step.get("db_type", ""))

        if db_type in SQL_DB_TYPES:
            sql = (step.get("query") or {}).get("sql", "")
            out.extend(_extract_mysql_joins(sql))
        elif db_type in DOCUMENT_DB_TYPES:
            out.extend(_extract_mongo_lookups(step))
        # else: unknown paradigm → skip join extraction (no known join syntax)
    return out


def _extract_mysql_joins(sql: str) -> list[dict]:
    """Parse JOIN (含 STRAIGHT_JOIN) ... ON a.col = b.col from SQL.

    Resolves table aliases: JOIN orders o ON o.uid = users.id → orders.uid, not o.uid.
    """
    import re
    result: list[dict] = []
    pattern = re.compile(
        r'\b(?:STRAIGHT_)?JOIN\s+(?:\w+\.)?(\w+)\s+(?:AS\s+)?(\w+)?\s*ON\s+'
        r'(\w+\.\w+)\s*=\s*(\w+\.\w+)',
        re.IGNORECASE,
    )
    for m in pattern.finditer(sql):
        table_name = m.group(1)
        alias = m.group(2)
        left = m.group(3)
        right = m.group(4)
        if alias:
            left = _resolve_alias(left, alias, table_name)
            right = _resolve_alias(right, alias, table_name)
        result.append({"from": left, "to": right})
    return result


def _resolve_alias(col_ref: str, alias: str, table_name: str) -> str:
    """If col_ref starts with alias., replace with table_name."""
    prefix = alias + "."
    if col_ref.startswith(prefix):
        return table_name + "." + col_ref[len(prefix):]
    return col_ref


def _extract_mongo_lookups(step: dict) -> list[dict]:
    """Extract $lookup join keys from a MongoDB plan step.

    Both sides are collection.field — symmetric.
    MongoDB $lookup operates within the same database, so no db prefix needed.
    """
    result: list[dict] = []
    pipeline = (step.get("query") or {}).get("pipeline") or step.get("pipeline") or []
    stages = pipeline if isinstance(pipeline, list) else []
    coll = step.get("collection", "")

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        lookup = stage.get("$lookup")
        if not isinstance(lookup, dict):
            continue
        local_field = lookup.get("localField")
        foreign_field = lookup.get("foreignField")
        from_coll = lookup.get("from")
        if local_field and foreign_field and from_coll:
            result.append({
                "from": f"{coll}.{local_field}",
                "to": f"{from_coll}.{foreign_field}",
            })
    return result
