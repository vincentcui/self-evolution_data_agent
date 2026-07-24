"""Stage 3 多态数据访问工具 — 4 个统一抽象工具.

fetch_schema / inspect_values / estimate_cost / execute_query

设计: 02-tool-layer-contract.md § 2.2-2.4
- 所有 4 个工具必传 (db_type, database, target) 三件套
- 内部走 resolve_ds → get_driver(db_type) → driver method
- 错误统一转结构化 dict (不抛给 agent_loop)
- LLM 不感知 datasource_id

execute_query 是唯一多态数据访问工具 (db_type+database+target 三件套, 走 driver
注册表); fetch_schema / inspect_values / estimate_cost 配套。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langfuse import observe
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.drivers import get_driver
from app.engine.drivers._exceptions import DriverError
from app.engine.tools._db_profile_projector import _project_schema_caps
from app.engine.tools._resolve_ds import resolve_ds

log = logging.getLogger(__name__)


def _ds_not_found_error(namespace_id: int, db_type: str, database: str) -> dict:
    return {
        "error": "datasource_not_found",
        "message": f"namespace {namespace_id} 下没有 {db_type}+{database} 的数据源",
        "suggestion": "调 lookup_knowledge(types=['terminology']) 看锚点是否过期",
    }


def _error_from_driver(e: DriverError) -> dict:
    return e.to_dict()


# ════════════════════════════════════════════
#  fetch_schema — 拉取目标表/集合的 schema
# ════════════════════════════════════════════

_INTERNAL_ENUM_KEYS = frozenset({
    "enum_ref_id",
    "enum_source",
    "enum_match_status",
    "enum_class_hint",
    "sample_values",
    "sample_metadata",
    "description_confidence",
    "indexed",
    "user_locked",
})


def _project_field_for_llm(field: dict[str, Any]) -> dict[str, Any]:
    """递归投影: 只保留 LLM 需要的字段, 过滤 UI-only 元数据.

    - 黑名单 key 全部剥离 (含递归 sub_fields)
    - enum_values: pending/conflict 时不给 LLM
    """
    out: dict[str, Any] = {}
    for k, v in field.items():
        if k in _INTERNAL_ENUM_KEYS:
            continue
        if k == "sub_fields" and isinstance(v, list):
            out[k] = [_project_field_for_llm(sf) for sf in v]
        elif k == "enum_values":
            status = field.get("enum_match_status")
            if status in ("pending", "conflict"):
                continue
            out[k] = v
        else:
            out[k] = v
    return out


_INTERNAL_RELATIONSHIP_KEYS = frozenset({"sources"})


def _project_relationship_for_llm(rel: dict) -> dict:
    """剥 sources — 内部元数据不泄 LLM."""
    return {k: v for k, v in rel.items() if k not in _INTERNAL_RELATIONSHIP_KEYS}


# 向后兼容别名 (测试文件引用)
_filter_enum_fields_for_llm = _project_field_for_llm

@observe(name="tool.fetch_schema")
async def fetch_schema(
    *,
    db: AsyncSession,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
) -> dict:
    """拉取目标表/集合的 schema (字段定义 + 索引).

    优先读 SchemaCanonicalObject (用户校对版), fallback driver introspect.
    """
    ds = await resolve_ds(db, namespace_id, db_type, database)
    if ds is None:
        return _ds_not_found_error(namespace_id, db_type, database)

    # Stage 2: 优先读 SchemaCanonicalObject (用户校对版)
    from app.knowledge.schema_canonical import get_schema_canonical

    canonical = await get_schema_canonical(db, namespace_id, db_type, database, target)
    if canonical:
        try:
            fields = json.loads(canonical.fields_json or "[]")
            indexes = json.loads(canonical.indexes_json or "[]")
        except json.JSONDecodeError:
            fields, indexes = [], []
        fields = [_project_field_for_llm(f) for f in fields]
        result = {
            "db_type": db_type,
            "database": database,
            "target": target,
            "description": canonical.description,
            "fields": fields,
            "indexes": indexes,
            "relationships": [
                _project_relationship_for_llm(r)
                for r in json.loads(canonical.relationships_json or "[]")
            ],
            "sample_count": canonical.sample_count,
            "source": "canonical",
        }
        result["timezone"] = ds.timezone
        result["db_profile"] = _project_schema_caps(
            json.loads(ds.db_profile_json or "{}")
        )
        return result

    # Fallback: driver 实时 introspect
    try:
        driver = get_driver(db_type)
        schema = await driver.fetch_schema(ds, target)
        if isinstance(schema, list):
            return {"error": "invalid_target", "message": "target 不能为空"}
        result: dict[str, Any] = {**schema, "relationships": [], "source": "introspect"}
        result["fields"] = [_project_field_for_llm(f) for f in result.get("fields", [])]
        result["timezone"] = ds.timezone
        result["db_profile"] = _project_schema_caps(
            json.loads(ds.db_profile_json or "{}")
        )
        return result
    except DriverError as e:
        return _error_from_driver(e)


# ════════════════════════════════════════════
#  inspect_values — 字段值分布探查
# ════════════════════════════════════════════

@observe(name="tool.inspect_values")
async def inspect_values(
    *,
    db: AsyncSession,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
    field: str,
    limit: int = 10,
) -> dict:
    """探目标字段的 distinct 值分布 (默认 top 10)."""
    ds = await resolve_ds(db, namespace_id, db_type, database)
    if ds is None:
        return _ds_not_found_error(namespace_id, db_type, database)

    try:
        driver = get_driver(db_type)
        values = await driver.inspect_values(ds, target, field, limit)
        return {"values": values, "field": field, "target": target}
    except DriverError as e:
        return _error_from_driver(e)


# ════════════════════════════════════════════
#  estimate_cost — 预估查询代价
# ════════════════════════════════════════════

@observe(name="tool.estimate_cost")
async def estimate_cost(
    *,
    db: AsyncSession,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
    query: dict,
) -> dict:
    """预估查询扫描行数与风险等级."""
    ds = await resolve_ds(db, namespace_id, db_type, database)
    if ds is None:
        return _ds_not_found_error(namespace_id, db_type, database)

    try:
        driver = get_driver(db_type)
        cost = await driver.estimate_cost(ds, target, query)
        result = dict(cost)
        result["timezone"] = ds.timezone
        result["db_profile"] = _project_schema_caps(
            json.loads(ds.db_profile_json or "{}")
        )
        return result
    except DriverError as e:
        return _error_from_driver(e)


# ════════════════════════════════════════════
#  execute_query — 执行查询 (4 mode)
# ════════════════════════════════════════════

@observe(name="tool.execute_query")
async def execute_query(
    *,
    db: AsyncSession,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
    query: dict,
) -> dict:
    """执行查询. 行数控制由 LLM 在 query 内表达 (LIMIT/FETCH/$limit), 要总数写计数查询."""
    ds = await resolve_ds(db, namespace_id, db_type, database)
    if ds is None:
        return _ds_not_found_error(namespace_id, db_type, database)
    try:
        driver = get_driver(db_type)
        result = await driver.execute_query(ds, target, query)
        return dict(result)
    except DriverError as e:
        return _error_from_driver(e)
