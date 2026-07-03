"""
业务词典刷新器 (Stage 1 Task 13 改写) — 把 extractor 抽出的 terms 写入 KnowledgeEntry.

Phase 1c Task 1.5: terminology 5 通道接闸 — 写入路径改调
upsert_terminology_with_validation 统一闸门 (schema/db_type/唯一键三重校验),
不再绕过闸门直插 KnowledgeEntry.

历史: 旧版写入 business_terms 表 + 复杂的乐观锁/源 repo 引用清理.
现状: 写入统一 KE[entry_type=terminology, source=schema, status=proposed],
       由 PATCH /api/knowledge/{id} (status=canonical) 走人审晋升进 RAG.

归属语义: 术语真相源是 SchemaCanonicalObject (SCO), 与 git 仓库无关.
       入口按 ns_id 读 SCO 抽词, 术语 KE 一律 source=schema, repo_id IS NULL.

简化点:
- 不再做"本 repo 上次贡献本次未抽到 → 移除 repo_id"的 multi-repo cleanup
  (术语零 repo_id, 由 status 状态机统一治理)
- 不再保护 confirmed/manual term (KE 状态机由 status 字段统一治理:
  canonical/superseded/rejected 永不被抽词覆盖)
- UNIQUE (namespace_id, entry_type, content_hash) 由 intake 路径处理重复

入口为 refresh_namespace_terminology(db, ns_id): ns 级抽词, 供手动刷新
(api/terminology_refresh) 与 trainer stage 调用.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import metadata as _metadata
from app.knowledge.terminology_extractor import (
    CanonicalLite,
    ExtractedTerm,
    RefreshReport,
    TerminologyExtractionFailedAll,
    extract_terms,
)
from app.models import DataSource, KnowledgeEntry, SchemaCanonicalObject

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  Canonical 读取
# ══════════════════════════════════════════════════════════════════════════════

async def _load_canonicals(db: AsyncSession, ns_id: int) -> list[CanonicalLite]:
    """读取 ns 下所有 canonical (统一从 SchemaCanonicalObject 读取, 覆盖 MySQL + MongoDB)."""
    out: list[CanonicalLite] = []

    stmt = (
        select(SchemaCanonicalObject)
        .where(SchemaCanonicalObject.namespace_id == ns_id)
        .order_by(SchemaCanonicalObject.db_type, SchemaCanonicalObject.target)
    )
    rows = list((await db.scalars(stmt)).all())
    for r in rows:
        desc = r.description or ""
        if not desc:
            continue  # 无 description 跳过, 不用字段名拼凑低质量输入

        out.append(CanonicalLite(
            canonical_id=r.id,
            collection=r.target,
            database=r.database,
            identity_key=f"{r.database}.{r.target}",
            description=desc,
            purpose_detail=r.purpose_detail or "",
        ))

    return out


# ══════════════════════════════════════════════════════════════════════════════
#  db_type 反查 — 经 ns DataSource 程序化解析 primary_database → db_type
# ══════════════════════════════════════════════════════════════════════════════

async def _resolve_db_type(
    s: AsyncSession, ns_id: int, primary_database: str,
) -> str | None:
    """根据 (ns_id, primary_database) 查 DataSource.db_type. 缺则 None."""
    ds = (await s.execute(
        select(DataSource)
        .where(
            DataSource.namespace_id == ns_id,
            DataSource.database == primary_database,
        )
        .limit(1)
    )).scalar_one_or_none()
    return ds.db_type if ds else None


# ══════════════════════════════════════════════════════════════════════════════
#  KE 写入 — 单 term → 一条 KnowledgeEntry[type=terminology] (走闸门)
# ══════════════════════════════════════════════════════════════════════════════

async def _upsert_terminology_ke(
    ns_id: int, t: ExtractedTerm,
) -> str:
    """单 term 走闸门写入 KE. 返回 'inserted' | 'skipped' | 'failed'.

    路由真相源:
        - extractor 已通过 source_canonical_ids 反查 canonical 填好
          primary_collection / primary_database / source_collections
        - refresher 只负责把 primary_database 经 DataSource 反查推 db_type
        - 若 extractor 没填 primary_collection/primary_database (上游异常),
          直接 'failed' 不再做 ns 级 fallback (旧版蒙错根因)
    """
    from app.knowledge.terminology_intake import upsert_terminology_with_validation

    if not t.primary_collection or not t.primary_database:
        log.warning(
            "[refresh_terms] term=%r 缺 primary_collection/primary_database (extractor 未填), 跳过",
            t.term,
        )
        return "failed"

    try:
        async with _metadata.async_session() as s:
            db_type = await _resolve_db_type(s, ns_id, t.primary_database)
            if db_type is None:
                log.warning(
                    "[refresh_terms] term=%r ns=%d database=%s 无对应 DataSource, 跳过",
                    t.term, ns_id, t.primary_database,
                )
                return "failed"

            payload = {
                "term": t.term,
                "primary_collection": t.primary_collection,
                "primary_database": t.primary_database,
                "db_type": db_type,
                "synonyms": list(t.synonyms),
                "source_collections": list(t.source_collections),
            }

            # 通过对比闸门返回的 KE 是否新 (无既有 PK) 区分 inserted vs skipped.
            existing_ids_before = set((await s.execute(
                select(KnowledgeEntry.id).where(
                    KnowledgeEntry.namespace_id == ns_id,
                    KnowledgeEntry.entry_type == "terminology",
                    KnowledgeEntry.is_superseded == False,  # noqa: E712
                )
            )).scalars().all())

            ke = await upsert_terminology_with_validation(
                s, ns_id=ns_id, payload_dict=payload,
                source="schema",  # 术语零 repo_id: 不传 repo_id (默认 None)
            )
            await s.commit()

            if ke is None:
                # 具体子因已由闸门内各步骤各自记录（validation/db_type/conflict），此处不重复
                log.debug("[refresh_terms] term=%r 闸门 reject，见上游日志", t.term)
                return "failed"
            if ke.id in existing_ids_before:
                # 命中既有活跃 KE — 闸门内合并 synonyms, 但本次未新建
                return "skipped"
            return "inserted"
    except Exception as e:  # pragma: no cover - defensive
        log.error("[refresh_terms] term=%r 闸门写入异常: %s", t.term, e, exc_info=True)
        return "failed"


# ══════════════════════════════════════════════════════════════════════════════
#  公共入口
# ══════════════════════════════════════════════════════════════════════════════

async def refresh_namespace_terminology(
    db: AsyncSession, ns_id: int,
) -> RefreshReport:
    """为 namespace 抽词并写入 KE[entry_type=terminology, source=schema, status=proposed].

    术语真相源是 SCO, 按 ns_id 读所有带 description 的 canonical 抽词,
    与 git 仓库无关. 术语 KE 一律 repo_id IS NULL, source=schema.

    简化语义 (相对旧版):
    - 不再做 per-repo cleanup (术语零 repo_id, 由 status 状态机统一治理)
    - 同名 term 已存在 → 跳过 (含 proposed/canonical/superseded/rejected)
    - extractor 全失败 → TerminologyExtractionFailedAll 抛给调用方
    """
    canonicals = await _load_canonicals(db, ns_id)
    if not canonicals:
        log.warning("[refresh_terms] ns=%d 无 canonical, 跳过", ns_id)
        return RefreshReport(skipped=True, reason="no_canonicals")

    log.info("[refresh_terms] ns=%d 开始抽词 canonicals=%d",
             ns_id, len(canonicals))

    terms, failed_batches = await extract_terms(canonicals)

    merged: list[str] = []
    failed: list[tuple[str, str]] = []
    for t in terms:
        status = await _upsert_terminology_ke(ns_id, t)
        if status == "inserted":
            merged.append(t.term)
        elif status == "failed":
            failed.append((t.term, "ke_write_failed"))

    report = RefreshReport(
        canonicals_seen=len(canonicals),
        merged=merged,
        deleted=[],  # KE 统一治理, 不再做 per-repo cleanup
        failed=failed,
        failed_batches=failed_batches,
    )
    log.info(
        "[refresh_terms] ns=%d 完成 canonicals=%d inserted=%d failed=%d failed_batches=%d",
        ns_id,
        report.canonicals_seen, len(merged),
        len(failed), len(failed_batches),
    )
    return report


__all__ = [
    "RefreshReport",
    "TerminologyExtractionFailedAll",
    "refresh_namespace_terminology",
]
