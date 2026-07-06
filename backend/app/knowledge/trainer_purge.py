"""Phase 2 Task 2.1 — 全量解析前置清场.

# ════════════════════════════════════════════
#  When this runs
# ════════════════════════════════════════════
# trainer 在执行 git 仓库的"全量重建"模式前调用本模块, 用于清扫上轮残留:
#   1) 删除 source ∈ REPO_REBUILDABLE_SOURCES 的全部 KE (含 canonical — 重建必清)
#   2) 删除 namespace 内所有 open 状态的 Terminology 冲突 (重新解析后再产生)
#   3) 写一条 audit_log (action='purge_for_full_rebuild', to_status='purged')
#      含 cascade_audit_deleted / cascade_conflict_deleted 合规留痕
#   4) commit 后 best-effort 清理 ChromaDB 知识池中已删 KE 的向量
#
# # ════════════════════════════════════════════
# # Atomicity contract
# # ════════════════════════════════════════════
# 步骤 1-3 在 db.begin_nested() 单 savepoint 内, 任一步骤抛异常整体回滚.
# 步骤 4 (ChromaDB 清理) best-effort, 单条失败仅日志, 不抛出
# (避免污染数据库已 commit 的事实).
"""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.knowledge_retriever import delete_knowledge_entry
from app.logging_config import get_logger
from app.models import Namespace
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry
from app.models.terminology_conflict import TerminologyConflict

log = get_logger("trainer.purge")

# ── 常量: 抹去 magic string, 单一真相源 ───────────────────────
AUDIT_ACTION_PURGE = "purge_for_full_rebuild"
PURGED_STATUS = "purged"
OPEN_CONFLICT_STATUS = "open"
REPO_REBUILDABLE_SOURCES: frozenset[str] = frozenset({"code_extract"})
"""per-repo 重建时可清场的 source 集合 (source ∧ repo_id 全删, 不分 status).

KnowledgeSource 的子集 (当前仅 code_extract 属"repo 提取可重建"渠道);
新增可重建渠道时此处同步加, KnowledgeSource Literal 扩展时审视此闭集.
"""


async def _delete_repo_extracted_kes(
    db: AsyncSession, repo_id: int,
) -> list[tuple[int, int | None, str]]:
    """删 source ∈ REPO_REBUILDABLE_SOURCES ∧ repo_id 的全部 KE — 不分 status (G1).

    Returns: [(entry_id, namespace_id, entry_type), ...] 供 ChromaDB 清理.

    设计契约:
        code_extract 是 agentic 仓库提取的 example/rule/route_hint, 应随重建清除.
        canonical 状态由人审标记, 但人审的对象 (上一轮 LLM 产出) 已过时,
        保留 = 把旧解读冻结成永恒. 推翻旧 "canonical 永不动" 宪章.
    """
    rows = (await db.execute(
        select(
            KnowledgeEntry.id,
            KnowledgeEntry.namespace_id,
            KnowledgeEntry.entry_type,
        ).where(
            KnowledgeEntry.repo_id == repo_id,
            KnowledgeEntry.source.in_(REPO_REBUILDABLE_SOURCES),
        )
    )).all()
    if not rows:
        return []
    ids = [r[0] for r in rows]
    await db.execute(
        delete(KnowledgeEntry).where(KnowledgeEntry.id.in_(ids))
    )
    return [(r[0], r[1], r[2]) for r in rows]


async def _delete_schema_terminology(
    db: AsyncSession, ns_id: int
) -> list[tuple[int, int | None, str]]:
    """删 ns 下所有 source=schema 的 terminology KE（ns 级, 与 repo 无关）.

    Returns: [(entry_id, namespace_id, entry_type), ...] 供 ChromaDB 清理.

    设计契约 (spec terminology-schema-attribution 改动 4b):
        术语只归属 schema/namespace (repo_id=NULL, source=schema), 是 ns 级条目,
        per-repo 的 _delete_repo_extracted_kes 天然不命中. 全量重建需补一条 ns 维度术语清场,
        与 per-repo 非术语清场并存 (source 集合不相交: code_extract vs schema),
        无重叠删除; 纯 DELETE 幂等.
    """
    rows = (await db.execute(
        select(
            KnowledgeEntry.id,
            KnowledgeEntry.namespace_id,
            KnowledgeEntry.entry_type,
        ).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "terminology",
            KnowledgeEntry.source == "schema",
        )
    )).all()
    if not rows:
        return []
    ids = [r[0] for r in rows]
    await db.execute(
        delete(KnowledgeEntry).where(KnowledgeEntry.id.in_(ids))
    )
    return [(r[0], r[1], r[2]) for r in rows]


async def _delete_open_conflicts(db: AsyncSession, ns_id: int) -> int:
    """删 ns 内 status=open 的 TerminologyConflict, 返回删除行数."""
    tc = await db.execute(
        delete(TerminologyConflict).where(
            TerminologyConflict.namespace_id == ns_id,
            TerminologyConflict.status == OPEN_CONFLICT_STATUS,
        )
    )
    return tc.rowcount or 0  # type: ignore[union-attr]


async def _delete_legacy_candidates(db: AsyncSession, repo_id: int) -> int:
    """删 repo 的 pending 候选 — 全量重建起点 (I2).

    仅删 status='pending': promoted 后的候选状态为 'active' (人审确认),
    rejected/superseded/in_conflict 是人类决策痕迹, 全部保留审计轨迹。
    根因: schema_canonical_candidates 无 source 列, _delete_repo_extracted_kes 的
    source 过滤器对其无效; write_canonical_candidate 按 value_hash UPSERT,
    重提取产出"不同" schema 时旧 pending 候选不被覆盖 → 堆积, 需显式清。
    返回删除行数。
    """
    from app.models.schema_canonical_candidate import SchemaCanonicalCandidate
    rows = (await db.execute(
        select(SchemaCanonicalCandidate.id).where(
            SchemaCanonicalCandidate.repo_id == repo_id,
            SchemaCanonicalCandidate.status == "pending",
        )
    )).scalars().all()
    if rows:
        await db.execute(
            delete(SchemaCanonicalCandidate).where(
                SchemaCanonicalCandidate.id.in_(rows)
            )
        )
    return len(rows)


async def purge_legacy_for_full_rebuild(
    db: AsyncSession,
    repo_id: int,
    ns_id: int,
    target_identity_keys: set[tuple[str, str]] | None = None,
    repo_name: str = "",
) -> dict:
    """全量重建前的清场.

    Atomic semantics:
        步骤 1-3 在 db.begin_nested() 内, 任一抛异常整体回滚.
        步骤 4 (ChromaDB 清理) best-effort, 永不抛出.

    Args:
        db: 调用方持有的 AsyncSession (调用方负责最终 commit).
        repo_id: 当前重建目标仓库.
        ns_id: 当前命名空间.
        target_identity_keys: 保留参数兼容旧调用方签名, 不再使用.

    Returns:
        {ke_deleted, fragments_deleted, canonicals_deleted, mongo_conflicts, term_conflicts}
    """
    deleted_ke_ids: list[tuple[int, int | None, str]] = []
    tc_count = 0

    # ── 1-3 原子 savepoint ────────────────────────────────────
    async with db.begin_nested():
        # ── 删前快照: 数指向待删 KE 的 audit / conflict (合规留痕) ──
        preview_ke_ids = (await db.execute(
            select(KnowledgeEntry.id).where(
                KnowledgeEntry.repo_id == repo_id,
                KnowledgeEntry.source.in_(REPO_REBUILDABLE_SOURCES),
            )
        )).scalars().all()
        # 按 source 分别计数 (审计可追溯) — 单次 GROUP BY
        _count_rows = (await db.execute(
            select(
                KnowledgeEntry.source,
                func.count(KnowledgeEntry.id),
            ).where(
                KnowledgeEntry.repo_id == repo_id,
                KnowledgeEntry.source.in_(REPO_REBUILDABLE_SOURCES),
            ).group_by(KnowledgeEntry.source)
        )).all()
        source_counts: dict[str, int] = {row[0]: row[1] for row in _count_rows}
        code_extract_ke_count = source_counts.get("code_extract", 0)
        if preview_ke_ids:
            cascade_audit_n = (await db.execute(
                select(func.count(KnowledgeAuditLog.id)).where(
                    KnowledgeAuditLog.entry_id.in_(preview_ke_ids),
                )
            )).scalar_one()
            cascade_conflict_n = (await db.execute(
                select(func.count(TerminologyConflict.id)).where(
                    TerminologyConflict.existing_entry_id.in_(preview_ke_ids),
                )
            )).scalar_one()
        else:
            cascade_audit_n = 0
            cascade_conflict_n = 0

        # ── 删 KE (G1: source ∈ REPO_REBUILDABLE_SOURCES ∧ repo_id 全删, 不分 status) ──
        deleted_ke_ids = await _delete_repo_extracted_kes(db, repo_id)

        # ── 删 ns 级 schema 术语 (改动 4b: 全量重建需清并重抽术语) ──
        # 合并进 deleted_ke_ids, 使步骤 4 ChromaDB 向量清理一并覆盖术语向量.
        schema_term_rows = await _delete_schema_terminology(db, ns_id)
        deleted_ke_ids.extend(schema_term_rows)

        # ── 兜底清 open conflict (G5) ──
        tc_count = await _delete_open_conflicts(db, ns_id)

        # ── 删 pending 候选 (I2: candidate 层无 source 列, 需独立清) ──
        cand_count = await _delete_legacy_candidates(db, repo_id)

        reason = (
            f"trainer_full_rebuild repo={repo_id} ns={ns_id} "
            f"ke_deleted={len(deleted_ke_ids)} (code_extract={code_extract_ke_count}) "
            f"schema_terminology_deleted={len(schema_term_rows)} "
            f"candidates_deleted={cand_count} "
            f"cascade_audit_deleted={cascade_audit_n} "
            f"cascade_conflict_deleted={cascade_conflict_n} "
            f"open_tc_deleted={tc_count}"
        )
        db.add(KnowledgeAuditLog(
            entry_id=None,
            actor_id=None,
            action=AUDIT_ACTION_PURGE,
            from_status=None,
            to_status=PURGED_STATUS,
            reason=reason,
        ))
        await db.flush()

    # ── 4. ChromaDB best-effort 同步 ──────────────────────────
    if deleted_ke_ids:
        ns_slug: str | None = (await db.execute(
            select(Namespace.slug).where(Namespace.id == ns_id)
        )).scalar_one_or_none()
        if ns_slug:
            for eid, _ns_id, etype in deleted_ke_ids:
                try:
                    delete_knowledge_entry(
                        slug=ns_slug, entry_id=eid, namespace_id=_ns_id,
                        entry_type=etype,
                    )
                except Exception as e:  # noqa: BLE001 — best-effort, 永不阻业务
                    log.warning(
                        "[purge][%s] chroma delete failed repo=%d ns=%s entry_id=%d: %s",
                        repo_name, repo_id, ns_slug, eid, e,
                    )

    return {
        "ke_deleted": len(deleted_ke_ids),
        "fragments_deleted": 0,
        "canonicals_deleted": cand_count,
        "mongo_conflicts": 0,
        "term_conflicts": tc_count,
    }
