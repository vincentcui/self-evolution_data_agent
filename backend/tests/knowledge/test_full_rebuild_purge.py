"""Phase 2 Task 2.1 — purge_legacy_for_full_rebuild.

# ════════════════════════════════════════════
#  测试范围
# ════════════════════════════════════════════
# 4 场景 — TDD spec, 全部 PASS 才算 GREEN.
# 1. KE proposed/superseded/rejected 删除, canonical 留存.
# 2. open TerminologyConflict 删除, resolved 状态留存.
# 3. audit_log 写入 action='purge_for_full_rebuild' + reason 含 'trainer_full_rebuild'.
# 4. 内部步骤抛错 → begin_nested 回滚, KE 等数据原封未动.
#
# Fixtures (seeded_repo_with_*) 已上提到 conftest.py — Phase 2 Task 2.2-2.4 共用同一 seed 形态.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry
from app.models.terminology_conflict import TerminologyConflict


# ════════════════════════════════════════════
#  Tests
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_purge_deletes_all_code_extract_kes_including_canonical(
    seeded_repo_with_mixed_kes, async_session, chroma_isolated,
):
    """G1: source=code_extract ∧ repo_id 全删, 含 canonical."""
    from app.knowledge.trainer import purge_legacy_for_full_rebuild

    ns_id, repo_id = seeded_repo_with_mixed_kes
    async with async_session() as db:
        report = await purge_legacy_for_full_rebuild(db, repo_id, ns_id)
        await db.commit()

    assert report["ke_deleted"] == 8  # 2 canonical + 2 proposed + 2 superseded + 2 rejected

    async with async_session() as db:
        rows = (await db.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.repo_id == repo_id)
        )).scalars().all()

    assert len(rows) == 0








@pytest.mark.asyncio
async def test_purge_deletes_only_open_term_conflicts(
    seeded_repo_with_open_conflicts, async_session, chroma_isolated,
):
    """status='open' TerminologyConflict 删除, resolved 留存."""
    from app.knowledge.trainer import purge_legacy_for_full_rebuild

    ns_id, repo_id = seeded_repo_with_open_conflicts
    async with async_session() as db:
        report = await purge_legacy_for_full_rebuild(db, repo_id, ns_id)
        await db.commit()

    assert report["mongo_conflicts"] == 0
    assert report["term_conflicts"] == 1

    async with async_session() as db:
        term_remaining = (await db.execute(
            select(TerminologyConflict).where(TerminologyConflict.namespace_id == ns_id)
        )).scalars().all()

    assert len(term_remaining) == 1 and term_remaining[0].status == "resolved"


@pytest.mark.asyncio
async def test_purge_writes_audit_log(
    seeded_repo_with_mixed_kes, async_session, chroma_isolated,
):
    """G6: audit_log action='purge_for_full_rebuild' + reason 含 cascade counts."""
    from app.knowledge.trainer import purge_legacy_for_full_rebuild

    ns_id, repo_id = seeded_repo_with_mixed_kes
    async with async_session() as db:
        await purge_legacy_for_full_rebuild(db, repo_id, ns_id)
        await db.commit()

    async with async_session() as db:
        rows = (await db.execute(
            select(KnowledgeAuditLog).where(
                KnowledgeAuditLog.action == "purge_for_full_rebuild",
            )
        )).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.entry_id is None
    assert row.actor_id is None
    assert row.from_status is None
    assert row.to_status == "purged"
    assert "trainer_full_rebuild" in row.reason
    assert f"repo={repo_id}" in row.reason
    assert f"ns={ns_id}" in row.reason
    assert "ke_deleted=8" in row.reason
    assert "cascade_audit_deleted=" in row.reason
    assert "cascade_conflict_deleted=" in row.reason


@pytest.mark.asyncio
async def test_purge_failure_aborts_atomically(
    seeded_repo_with_mixed_kes, async_session, chroma_isolated, monkeypatch,
):
    """conflict 删除步骤抛异常 → 整体回滚, KE 数据原封未动."""
    from app.knowledge import trainer_purge

    ns_id, repo_id = seeded_repo_with_mixed_kes

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated conflict deletion failure")

    monkeypatch.setattr(trainer_purge, "_delete_open_conflicts", _boom)

    async with async_session() as db:
        with pytest.raises(RuntimeError, match="simulated conflict"):
            await trainer_purge.purge_legacy_for_full_rebuild(db, repo_id, ns_id)
        # Outer session 显式回滚保险 — savepoint 的回滚应已发生
        await db.rollback()

    # 全新 session 验证: 8 个 KE 一个不少
    async with async_session() as db:
        rows = (await db.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.repo_id == repo_id)
        )).scalars().all()

    assert len(rows) == 8


# ════════════════════════════════════════════
#  G2 / G4 / G5 / G7 — spec code_extract-source-full-purge
# ════════════════════════════════════════════


def _mk_ke(ns_id, repo_id, source, status, entry_type="rule"):
    return KnowledgeEntry(
        namespace_id=ns_id,
        entry_type=entry_type,
        source=source,
        status=status,
        is_superseded=False,
        payload="{}",
        content="x",
        raw_input="",
        evidence_json="{}",
        repo_id=repo_id,
    )


@pytest.mark.asyncio
async def test_purge_preserves_non_rebuildable_canonical_4_sources(async_session, chroma_isolated):
    """G2: 非 code_extract (REPO_REBUILDABLE_SOURCES) canonical 4 个来源全不删."""
    from app.knowledge.trainer_purge import purge_legacy_for_full_rebuild
    from app.models import Namespace
    from app.models.git_repo import GitRepo

    async with async_session() as db:
        ns = Namespace(name="g2", slug="g2", description="")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        repo = GitRepo(namespace_id=ns.id, url="https://e.com/r.git")
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

        seeded_ids = []
        for src in ("manual", "agent_learn", "schema", "agent_learn"):
            ke = _mk_ke(ns.id, None, src, "canonical")
            db.add(ke)
            await db.flush()
            seeded_ids.append(ke.id)
        # 种 1 个 code_extract canonical 应被删
        code_extract_ke = _mk_ke(ns.id, repo.id, "code_extract", "canonical")
        db.add(code_extract_ke)
        await db.commit()
        await db.refresh(code_extract_ke)
        code_extract_ke_id = code_extract_ke.id

        await purge_legacy_for_full_rebuild(db, repo.id, ns.id)
        await db.commit()

    async with async_session() as db:
        for kid in seeded_ids:
            ke = await db.get(KnowledgeEntry, kid)
            assert ke is not None, f"非 code_extract canonical id={kid} 被误删"
        assert await db.get(KnowledgeEntry, code_extract_ke_id) is None, (
            "code_extract canonical 未删"
        )


@pytest.mark.asyncio
async def test_purge_cascade_deletes_resolved_conflicts(async_session, chroma_isolated):
    """G5: resolved TerminologyConflict 随 KE CASCADE 自然清."""
    from app.knowledge.trainer_purge import purge_legacy_for_full_rebuild
    from app.models import Namespace
    from app.models.git_repo import GitRepo

    async with async_session() as db:
        ns = Namespace(name="g5", slug="g5", description="")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        repo = GitRepo(namespace_id=ns.id, url="https://e.com/r.git")
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

        canonical = _mk_ke(ns.id, repo.id, "code_extract", "canonical", "terminology")
        db.add(canonical)
        await db.commit()
        await db.refresh(canonical)

        resolved = TerminologyConflict(
            namespace_id=ns.id,
            existing_entry_id=canonical.id,
            candidate_payload="{}",
            candidate_source="code_extract",
            status="resolved",
            resolution_choice="merge_both",
        )
        db.add(resolved)
        await db.commit()
        await db.refresh(resolved)
        resolved_id = resolved.id

        await purge_legacy_for_full_rebuild(db, repo.id, ns.id)
        await db.commit()

    async with async_session() as db:
        # 用 select 查 DB 真实状态 (db.get 会命中 identity-map 缓存的旧对象;
        # DB 级 CASCADE 删除不经 ORM, 缓存对象不会失效 → 必须走 SQL 查询)
        still = (await db.execute(
            select(TerminologyConflict).where(TerminologyConflict.id == resolved_id)
        )).scalar_one_or_none()
        assert still is None, "resolved 应随 canonical KE CASCADE 删除"


@pytest.mark.asyncio
async def test_purge_chromadb_failure_does_not_block_sql_commit(
    async_session, chroma_isolated, monkeypatch,
):
    """G7: ChromaDB delete 抛异常, SQL 已 commit, 不回滚."""
    from app.knowledge import trainer_purge
    from app.knowledge.trainer_purge import purge_legacy_for_full_rebuild
    from app.models import Namespace
    from app.models.git_repo import GitRepo

    async with async_session() as db:
        ns = Namespace(name="g7", slug="g7", description="")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        repo = GitRepo(namespace_id=ns.id, url="https://e.com/r.git")
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

        for st in ("proposed", "canonical"):
            db.add(_mk_ke(ns.id, repo.id, "code_extract", st))
        await db.commit()

        def boom(**kwargs):
            raise RuntimeError("chromadb down")

        monkeypatch.setattr(trainer_purge, "delete_knowledge_entry", boom)
        # 期望不抛 — best-effort
        await purge_legacy_for_full_rebuild(db, repo.id, ns.id)
        await db.commit()

    async with async_session() as db:
        remaining = (await db.execute(
            select(KnowledgeEntry).where(
                KnowledgeEntry.repo_id == repo.id,
                KnowledgeEntry.source == "code_extract",
            )
        )).scalars().all()
        assert len(remaining) == 0, "SQL 已 commit 不回滚, KE 应删干净"


@pytest.mark.asyncio
async def test_purge_deletes_pending_candidates_keeps_decided(
    seeded_repo_with_mixed_kes, async_session, chroma_isolated,
):
    """I2: 全量重建删 status='pending' 候选, 保留人审决策态 (active/rejected).

    注: promoted 候选的真实状态是 'active' (见 canonical_promote.py _finalize_losers /
    单候选 promote), 非 review 文档草案误写的 'promoted'。本测用真实状态值。
    """
    from app.knowledge.trainer import purge_legacy_for_full_rebuild
    from app.models.schema_canonical_candidate import SchemaCanonicalCandidate

    ns_id, repo_id = seeded_repo_with_mixed_kes

    # 建 3 候选 (同 repo, 不同 value_hash): pending / active(promoted后) / rejected
    async with async_session() as db:
        for i, status in enumerate(("pending", "active", "rejected")):
            db.add(SchemaCanonicalCandidate(
                namespace_id=ns_id,
                db_type="mysql",
                database="test_db",
                target="orders",
                field_path=f"f{i}",
                candidate_kind="field_description",
                candidate_value_json='{"description": "x"}',
                value_hash=f"hash_{status}_{i}",
                evidence_sources_json="[]",
                status=status,
                confidence_status="confirmed_by_code",
                repo_id=repo_id,
                generation=0,
            ))
        await db.commit()

    async with async_session() as db:
        await purge_legacy_for_full_rebuild(db, repo_id, ns_id)
        await db.commit()

    async with async_session() as db:
        remaining = (await db.execute(
            select(SchemaCanonicalCandidate.status).where(
                SchemaCanonicalCandidate.repo_id == repo_id
            )
        )).scalars().all()

    assert "pending" not in remaining, "pending 候选应被全量重建清除"
    assert set(remaining) == {"active", "rejected"}, \
        f"人审决策态 (active/rejected) 应保留, 实测 {remaining}"
