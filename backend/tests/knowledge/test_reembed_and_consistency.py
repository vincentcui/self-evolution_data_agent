"""Stage 2 Task 7 — reembed + verify_db_chromadb_consistency e2e.

真实 DB + ChromaDB (按 §06 规则不 mock).
覆盖:
    - reembed dry_run 仅统计候选数
    - reembed 真重灌后 ChromaDB doc 数量正确
    - verify 检测 DB 有 KE 但 ChromaDB 无向量的孤岛
"""
import pytest

from app.knowledge.knowledge_retriever import upsert_knowledge_entry
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace
from scripts.reembed_after_model_change import reembed
from scripts.verify_db_chromadb_consistency import verify


@pytest.mark.asyncio
async def test_reembed_dry_run_counts_candidates(db_session, chroma_isolated):
    """dry_run=True 仅返候选数, 不动 ChromaDB."""
    ns = Namespace(name="t", slug="t", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    db_session.add_all([
        KnowledgeEntry(
            namespace_id=ns.id, entry_type="terminology", content=f"x{i}",
            source="code_extract", status="canonical", tier="normal",
        )
        for i in range(3)
    ])
    await db_session.commit()

    report = await reembed(db_session, dry_run=True)

    assert report.candidates == 3
    assert report.reembedded == 0
    assert report.failed_ids == []


@pytest.mark.asyncio
async def test_reembed_real_replaces_chromadb_docs(db_session, chroma_isolated):
    """真重灌: 每条 delete + upsert, ChromaDB 最终有等量 doc."""
    from app.engine.registry import get_knowledge_collection

    ns = Namespace(name="t2", slug="t2", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    for i in range(3):
        ke = KnowledgeEntry(
            namespace_id=ns.id, entry_type="rule", content=f"y{i}",
            source="code_extract", status="canonical", tier="normal",
        )
        db_session.add(ke)
        await db_session.commit()
        await db_session.refresh(ke)
        upsert_knowledge_entry(
            slug=ns.slug, entry_id=ke.id, content=ke.content,
            tier=ke.tier, namespace_id=ke.namespace_id,
            entry_type=ke.entry_type, status=ke.status,
        )

    report = await reembed(db_session, dry_run=False)

    assert report.reembedded == 3
    assert report.failed_ids == []
    coll = get_knowledge_collection(ns.slug)
    assert coll.count() == 3


@pytest.mark.asyncio
async def test_verify_detects_db_only_inconsistency(db_session, chroma_isolated):
    """DB 有 KE 但 ChromaDB 没向量 → db_only 含其 id."""
    ns = Namespace(name="t3", slug="t3", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    ke = KnowledgeEntry(
        namespace_id=ns.id, entry_type="terminology", content="z",
        source="code_extract", status="canonical", tier="normal",
    )
    db_session.add(ke)
    await db_session.commit()
    await db_session.refresh(ke)
    # 故意不 upsert 到 ChromaDB

    report = await verify(db_session)

    assert ke.id in report.db_only
    assert report.checked_canonical_ke == 1
