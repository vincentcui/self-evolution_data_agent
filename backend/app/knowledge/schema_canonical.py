"""SchemaCanonicalObject CRUD — 跨数据库 schema 真相源读写.

公开函数:
- get_schema_canonical: 按 (ns_id, db_type, database, target) 查单条
- upsert_schema_canonical: 幂等写入 (存在则更新, 不存在则插入)
- list_schema_canonicals: 按 namespace 列出全部
- refresh_driver_canonicals: SQL 型数据源通用 introspect → candidate (MySQL / Oracle 共用)
- refresh_mysql_canonicals: refresh_driver_canonicals 的 MySQL wrapper (向后兼容)
- refresh_oracle_canonicals: refresh_driver_canonicals 的 Oracle wrapper
- backfill_indexes_from_driver: 从 driver introspect 补充 SCO 的 indexes_json + field indexed
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchemaCanonicalObject
from app.models.base import local_now

log = logging.getLogger(__name__)


def _is_referenced_target(
    db_type: str,
    target: str,
    referenced_targets: set[str],
    referenced_target_keys: set[str] | None = None,
) -> bool:
    """表名过滤: exact 优先，Oracle 额外做大小写无关匹配."""
    if target in referenced_targets:
        return True
    if db_type != "oracle":
        return False
    keys = referenced_target_keys or {item.casefold() for item in referenced_targets}
    return target.casefold() in keys


async def get_schema_canonical(
    db: AsyncSession,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
) -> SchemaCanonicalObject | None:
    """按四元组查单条 canonical. 无匹配返 None."""
    stmt = select(SchemaCanonicalObject).where(
        SchemaCanonicalObject.namespace_id == namespace_id,
        SchemaCanonicalObject.db_type == db_type,
        SchemaCanonicalObject.database == database,
        SchemaCanonicalObject.target == target,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_schema_canonical(
    db: AsyncSession,
    *,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
    fields_json: str = "[]",
    indexes_json: str = "[]",
    description: str = "",
    purpose_detail: str = "",
    sample_count: int = 0,
    source: str = "introspect",
) -> SchemaCanonicalObject:
    """幂等写入 — 存在则更新, 不存在则插入."""
    existing = await get_schema_canonical(db, namespace_id, db_type, database, target)

    if existing:
        existing.fields_json = fields_json
        existing.indexes_json = indexes_json
        existing.description = description
        existing.purpose_detail = purpose_detail
        existing.sample_count = sample_count
        existing.source = source
        existing.updated_at = datetime.now()
        await db.commit()
        return existing

    obj = SchemaCanonicalObject(
        namespace_id=namespace_id,
        db_type=db_type,
        database=database,
        target=target,
        fields_json=fields_json,
        indexes_json=indexes_json,
        description=description,
        purpose_detail=purpose_detail,
        sample_count=sample_count,
        source=source,
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def list_schema_canonicals(
    db: AsyncSession,
    namespace_id: int,
    db_type: str | None = None,
    database: str | None = None,
) -> list[SchemaCanonicalObject]:
    """列出 namespace 下全部 canonical, 可选按 db_type / database 过滤."""
    stmt = select(SchemaCanonicalObject).where(
        SchemaCanonicalObject.namespace_id == namespace_id,
    )
    if db_type:
        stmt = stmt.where(SchemaCanonicalObject.db_type == db_type)
    if database:
        stmt = stmt.where(SchemaCanonicalObject.database == database)
    stmt = stmt.order_by(SchemaCanonicalObject.db_type, SchemaCanonicalObject.target)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def refresh_driver_canonicals(
    db: AsyncSession,
    namespace_id: int,
    ns_slug: str,
    db_type: str,
    referenced_targets: set[str] | None = None,
    repo_name: str = "",
    trigger_promote: bool = True,
    datasource_id: int | None = None,
) -> int:
    """SQL 型数据源通用 introspect → candidate 写入入口 (MySQL / Oracle 共用).

    Args:
        db_type: 数据源类型 (mysql | oracle), 决定查哪批 DataSource + 用哪个 driver.
        referenced_targets: 仅 introspect 集合内的表名 (per-repo 范围收窄).
            None → 全表 introspect (手动按钮路径保留旧语义).
            空 set → 早返 0 (本 repo 无该类型引用, noop).
        datasource_id: 仅刷新指定数据源 (per-row 按钮路径收窄).
            None → namespace 下全部该类型数据源.
        trigger_promote: 写完 candidate 后是否内部触发 promote + 索引补充.
            True (默认, 手动刷新端点用).
            False (trainer 用) → 只写候选, 汇聚交给外层统一处理.

    返回处理的表数量. 调用方负责 commit.
    """
    from sqlalchemy import select as sa_select

    from app.engine.drivers import get_driver
    from app.knowledge.canonical_introspect import write_introspect_candidates_for_target
    from app.knowledge.canonical_promote import promote_candidates_to_canonical
    from app.models import DataSource

    if referenced_targets is not None and not referenced_targets:
        return 0
    referenced_target_keys = (
        {target.casefold() for target in referenced_targets}
        if db_type == "oracle" and referenced_targets is not None
        else None
    )

    ds_stmt = sa_select(DataSource).where(
        DataSource.namespace_id == namespace_id,
        DataSource.db_type == db_type,
    )
    if datasource_id is not None:
        ds_stmt = ds_stmt.where(DataSource.id == datasource_id)
    ds_rows = (await db.execute(ds_stmt)).scalars().all()

    if not ds_rows:
        return 0

    driver = get_driver(db_type)
    table_count = 0

    for ds in ds_rows:
        try:
            schemas = await driver.fetch_schema(ds, target=None)
            if not isinstance(schemas, list):
                continue

            for table_stub in schemas:
                target = table_stub["target"]
                if referenced_targets is not None and not _is_referenced_target(
                    db_type,
                    target,
                    referenced_targets,
                    referenced_target_keys,
                ):
                    continue
                detail = await driver.fetch_schema(ds, target=target)
                if isinstance(detail, list):
                    continue

                await write_introspect_candidates_for_target(
                    db,
                    namespace_id=namespace_id,
                    db_type=db_type,
                    database=ds.database,
                    target=detail["target"],
                    detail=cast("dict[str, Any]", detail),
                    datasource_id=ds.id,
                )
                table_count += 1

        except Exception as e:
            log.warning(
                "[%s] %s introspect failed ds=%d: %s",
                repo_name, db_type.upper(), ds.id, e,
            )

    # ── FK writer: 每 ds 查外键 → relationship candidate ──
    from app.knowledge.canonical_relationship_writer import (
        write_relationship_candidates_from_foreign_keys,
    )
    for ds in ds_rows:
        try:
            driver = get_driver(db_type)
            fks = await driver.fetch_foreign_keys(ds, target=None)
            if fks:
                await write_relationship_candidates_from_foreign_keys(
                    db, namespace_id=namespace_id, datasource=ds,
                    foreign_keys=fks, ds_id=ds.id,
                    referenced_targets=referenced_targets,
                )
        except Exception as e:
            log.warning("[%s] fetch FK failed ds=%d: %s", repo_name, ds.id, e)

    # 写完所有 candidate 后触发 promote (仅 trigger_promote=True)
    if table_count > 0 and trigger_promote:
        await promote_candidates_to_canonical(db, namespace_id)
        # promote 后补充索引信息 (indexes_json + field indexed)
        # datasource_id 收窄时只补该数据源的 database, 不碰其他数据源
        backfill_database = ds_rows[0].database if datasource_id is not None else None
        await backfill_indexes_from_driver(
            db, namespace_id, db_type=db_type, database=backfill_database,
        )
        await cleanup_stale_fk_relationships(db, namespace_id)

    log.info(
        "[%s] refreshed %d %s tables (via candidate) for ns=%d",
        repo_name, table_count, db_type.upper(), namespace_id,
    )
    return table_count


async def refresh_mysql_canonicals(
    db: AsyncSession,
    namespace_id: int,
    ns_slug: str,
    referenced_tables: set[str] | None = None,
    repo_name: str = "",
    trigger_promote: bool = True,
    datasource_id: int | None = None,
) -> int:
    """MySQL 专用 wrapper — 向后兼容, 内部委托给 refresh_driver_canonicals."""
    return await refresh_driver_canonicals(
        db, namespace_id, ns_slug, db_type="mysql",
        referenced_targets=referenced_tables,
        repo_name=repo_name,
        trigger_promote=trigger_promote,
        datasource_id=datasource_id,
    )


async def refresh_oracle_canonicals(
    db: AsyncSession,
    namespace_id: int,
    ns_slug: str,
    referenced_tables: set[str] | None = None,
    repo_name: str = "",
    trigger_promote: bool = True,
    datasource_id: int | None = None,
) -> int:
    """Oracle 专用 wrapper — 内部委托给 refresh_driver_canonicals."""
    return await refresh_driver_canonicals(
        db, namespace_id, ns_slug, db_type="oracle",
        referenced_targets=referenced_tables,
        repo_name=repo_name,
        trigger_promote=trigger_promote,
        datasource_id=datasource_id,
    )


async def backfill_indexes_from_driver(
    db: AsyncSession,
    namespace_id: int,
    db_type: str | None = None,
    database: str | None = None,
) -> int:
    """从 driver introspect 补充 SCO 的 indexes_json + field 级 indexed 标记.

    遍历 namespace 下所有 SCO, 对每个 target 调 driver.fetch_schema 拿索引,
    写入 sco.indexes_json 并标记 fields_json 中对应字段的 indexed=True.

    Args:
        db_type: 可选, 仅处理指定 db_type 的 SCO. None → 全部.
        database: 可选, 仅处理指定 database 的 SCO (per-datasource 收窄). None → 全部.

    Returns:
        更新的 SCO 数量.
    """
    import json

    from app.engine.drivers import get_driver
    from app.models import DataSource

    scos = await list_schema_canonicals(db, namespace_id, db_type=db_type)
    if database is not None:
        scos = [s for s in scos if s.database == database]
    if not scos:
        return 0

    # 按 (db_type, database) 分组查 DataSource
    ds_cache: dict[tuple[str, str], DataSource | None] = {}

    async def _get_ds(sco_db_type: str, sco_database: str) -> DataSource | None:
        key = (sco_db_type, sco_database)
        if key not in ds_cache:
            from sqlalchemy import select as sa_select
            row = (await db.execute(
                sa_select(DataSource).where(
                    DataSource.namespace_id == namespace_id,
                    DataSource.db_type == sco_db_type,
                    DataSource.database == sco_database,
                )
            )).scalar_one_or_none()
            ds_cache[key] = row
        return ds_cache[key]

    updated = 0
    for sco in scos:
        if sco.user_locked:
            continue
        ds = await _get_ds(sco.db_type, sco.database)
        if ds is None:
            continue

        try:
            driver = get_driver(sco.db_type)
            schema = await driver.fetch_schema(ds, sco.target)
            if isinstance(schema, list):
                continue

            indexes = schema.get("indexes", [])
            if not indexes:
                continue

            # 写入 indexes_json
            sco.indexes_json = json.dumps(indexes, ensure_ascii=False)

            # 从 indexes 提取有索引的字段名集合
            indexed_fields: set[str] = set()
            for idx in indexes:
                # MySQL: {"columns": ["col1", "col2"]}
                # MongoDB: {"keys": {"field1": 1, "field2": -1}}
                cols = idx.get("columns") or []
                keys = idx.get("keys") or {}
                for col in cols:
                    indexed_fields.add(col)
                for k in keys:
                    indexed_fields.add(k)

            # 标记 fields_json 中对应字段的 indexed
            fields = json.loads(sco.fields_json or "[]")
            changed = False
            for f in fields:
                name = f.get("name", "")
                should_be_indexed = name in indexed_fields
                if f.get("indexed") != should_be_indexed and should_be_indexed:
                    f["indexed"] = True
                    changed = True
            if changed:
                sco.fields_json = json.dumps(fields, ensure_ascii=False)

            sco.updated_at = local_now()
            updated += 1

        except Exception as e:
            log.warning(
                "backfill_indexes failed sco=%d target=%s: %s",
                sco.id, sco.target, e,
            )

    if updated:
        await db.flush()

    log.info(
        "backfill_indexes ns=%d: updated %d/%d SCOs",
        namespace_id, updated, len(scos),
    )
    return updated


async def cleanup_stale_fk_relationships(
    db: AsyncSession, namespace_id: int,
) -> int:
    """清理已删 FK 对应的 relationship 条目.

    仅清理 sources 含 "introspect_fk" 且 from_field 不在当前 FK 集合的条目.
    降级安全: FK 查询失败的 ds 其 SCO 全部跳过 (不当作 "FK 全删").
    幂等: 可重复调用.
    """
    import json

    from app.engine.drivers import get_driver
    from app.models import DataSource, SchemaCanonicalCandidate

    ds_rows = list((await db.execute(
        select(DataSource).where(DataSource.namespace_id == namespace_id),
    )).scalars().all())

    # per-ds FK 集合
    fk_by_target: dict[tuple[str, str], dict[str, set[str]]] = {}
    degraded_ds: set[tuple[str, str]] = set()

    for ds in ds_rows:
        try:
            fks = await get_driver(ds.db_type).fetch_foreign_keys(ds, None)
            m: dict[str, set[str]] = {}
            for fk in fks:
                m.setdefault(fk["from_target"], set()).add(fk["from_field"])
            fk_by_target[(ds.db_type, ds.database)] = m
        except Exception:
            degraded_ds.add((ds.db_type, ds.database))
            continue

    scos = await list_schema_canonicals(db, namespace_id)
    removed = 0

    # 预取 ns 下全部 active confirmed_by_code relationship candidate → (target, field_path) 集合
    # 避免逐条关系查库 (N+1)
    protected_rows = (await db.execute(
        select(
            SchemaCanonicalCandidate.target,
            SchemaCanonicalCandidate.field_path,
        ).where(
            SchemaCanonicalCandidate.namespace_id == namespace_id,
            SchemaCanonicalCandidate.candidate_kind == "relationship",
            SchemaCanonicalCandidate.status == "active",
            SchemaCanonicalCandidate.confidence_status == "confirmed_by_code",
        ),
    )).all()
    protected: set[tuple[str, str]] = {(t, f) for t, f in protected_rows}

    for sco in scos:
        if (sco.db_type, sco.database) in degraded_ds:
            continue
        current = fk_by_target.get(
            (sco.db_type, sco.database), {},
        ).get(sco.target, set())
        rels = json.loads(sco.relationships_json or "[]")
        cleaned = []

        for r in rels:
            if "introspect_fk" not in r.get("sources", []):
                cleaned.append(r)
                continue
            if r.get("from_field") in current:
                cleaned.append(r)
                continue
            # 有 active confirmed_by_code 的 candidate 保护则不清
            if (sco.target, r.get("from_field")) in protected:
                cleaned.append(r)

        if len(cleaned) != len(rels):
            sco.relationships_json = json.dumps(cleaned, ensure_ascii=False)
            removed += 1

    if removed:
        await db.flush()
    return removed
