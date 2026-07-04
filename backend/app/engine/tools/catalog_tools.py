"""库表目录工具 — list_databases / list_tables.

纯 PG 读, 不连真实库 (与 data_access_tools 的连库工具区分, 故独立文件).
- list_databases: 读 DataSource 表, 冷启动打底永不空.
- list_tables: 读 schema_canonical_objects, 空时返 status + hint 引导.

闭包注入: db / namespace_id 由 agent_loop_dispatcher 注入, LLM 不可见.
"""
from __future__ import annotations

import json

from langfuse import observe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.tools._db_profile_projector import _project_db_profile
from app.knowledge.schema_canonical import list_schema_canonicals
from app.models import DataSource


@observe(name="tool.list_databases")
async def list_databases(*, db: AsyncSession, namespace_id: int) -> dict:
    """列出当前 namespace 下所有数据源 (db_type/库名/描述/库画像). 纯 PG 读."""
    result = await db.execute(
        select(DataSource).where(DataSource.namespace_id == namespace_id)
    )
    sources = result.scalars().all()
    databases = []
    for ds in sources:
        try:
            profile = json.loads(ds.db_profile_json or "{}")
        except (json.JSONDecodeError, TypeError):
            profile = {}
        databases.append({
            "db_type": ds.db_type,
            "database": ds.database,
            "description": ds.description or "",
            "timezone": ds.timezone,
            "db_profile": _project_db_profile(profile, "overview"),
        })
    return {"databases": databases, "count": len(databases)}


@observe(name="tool.list_tables")
async def list_tables(*, db: AsyncSession, namespace_id: int, database: str) -> dict:
    """列出指定库下已提取的表/集合 (表名/描述/字段数). 纯 PG 读 canonical.

    空时区分两种 status:
    - unknown_database: database 不在本 ns 的数据源列表 (记错/拼错)
    - no_schema_extracted: 库存在但未提取表结构 (冷启动典型) → 带 hint 引导
    """
    canonicals = await list_schema_canonicals(db, namespace_id, database=database)
    if canonicals:
        tables = []
        for sco in canonicals:
            try:
                fields = json.loads(sco.fields_json or "[]")
            except (json.JSONDecodeError, TypeError):
                fields = []
            tables.append({
                "target": sco.target,
                "description": sco.description or "",
                "field_count": len(fields),
                "reviewed": bool(sco.reviewed),
            })
        return {"database": database, "tables": tables, "count": len(tables)}

    # 空: 先判断库名是否属于本 ns 的数据源
    ds_exists = (await db.execute(
        select(DataSource.id).where(
            DataSource.namespace_id == namespace_id,
            DataSource.database == database,
        ).limit(1)
    )).scalar_one_or_none()

    if ds_exists is None:
        return {
            "database": database, "tables": [], "count": 0,
            "status": "unknown_database",
        }

    return {
        "database": database, "tables": [], "count": 0,
        "status": "no_schema_extracted",
        "hint": (
            f"库 {database} 暂无已提取的表结构。"
            "(1) 若当前 namespace 下存在与用户问题语义相关的其他数据源, "
            "可对其调用 list_tables 继续探索; "
            "(2) 若与问题相关的数据库均无表结构, 请告知用户: "
            "对该数据源执行「刷新schema」或解析其 Git 仓库后, 可显著提升查询准确性; "
            "(3) 若各数据源均无描述、无法判断哪个库相关, 不要臆测选库 —— "
            "直接 clarify_with_user 询问用户应查哪个库。"
        ),
    }
