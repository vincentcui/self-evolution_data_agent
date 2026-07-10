"""Phase 4 Task 4.1 — KnowledgeBundle + load_all_knowledge happy-path 验证.

真实 SQLite + ChromaDB (按 §06 规则不 mock).

4 case:
- basic_load_returns_bundle
- critical_loaded
- terminology_full_inject_when_k_zero
- to_prompt_sections_renders_3_blocks
"""

import pytest
import pytest_asyncio

from app.config import settings
from app.knowledge.knowledge_loader import (
    KnowledgeBundle,
    load_all_knowledge,
)
from app.knowledge.knowledge_retriever import upsert_knowledge_entry
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace

# ════════════════════════════════════════════
#  fixtures
# ════════════════════════════════════════════

@pytest_asyncio.fixture
async def seeded_ns(async_session) -> tuple[int, str]:
    """光秃 namespace + slug, 不挂任何 KE."""
    async with async_session() as db:
        ns = Namespace(name="loader_ns", slug="loader_ns", description="phase4-loader")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        return ns.id, ns.slug


@pytest_asyncio.fixture
async def seeded_critical_kes(async_session) -> tuple[int, str]:
    """ns + 2 critical canonical KE (走 SQL 直加载, 不入 ChromaDB)."""
    async with async_session() as db:
        ns = Namespace(name="crit_ns", slug="crit_ns", description="phase4-crit")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        for i, content in enumerate(("critical-rule-A", "critical-rule-B"), start=1):
            db.add(KnowledgeEntry(
                namespace_id=ns.id,
                entry_type="rule",
                status="canonical",
                tier="critical",
                content=content,
                source="manual",
            ))
        await db.commit()
        return ns.id, ns.slug


@pytest_asyncio.fixture
async def seeded_ns_with_canonical_terminology(
    async_session, real_chromadb,
) -> tuple[int, str, int]:
    """ns + 1 canonical terminology KE (向量化入库)."""
    async with async_session() as db:
        ns = Namespace(name="term_ns", slug="term_ns", description="phase4-term")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        ke = KnowledgeEntry(
            namespace_id=ns.id,
            entry_type="terminology",
            status="canonical",
            tier="normal",
            content="customer 对应 客户 集合",
            source="manual",
            payload='{"term":"customer","primary_collection":"customer",'
                    '"primary_database":"db_q","db_type":"mongodb",'
                    '"synonyms":["客户"],"source_collections":["customer"]}',
        )
        db.add(ke)
        await db.commit()
        await db.refresh(ke)

        upsert_knowledge_entry(
            slug=ns.slug,
            entry_id=ke.id,
            content=ke.content,
            tier="normal",
            namespace_id=ns.id,
            entry_type="terminology",
            status="canonical",
            payload={
                "term": "customer",
                "primary_collection": "customer",
                "primary_database": "db_q",
                "db_type": "mongodb",
                "synonyms": ["客户"],
                "source_collections": ["customer"],
            },
        )
        return ns.id, ns.slug, ke.id


@pytest_asyncio.fixture
async def seeded_ns_many_terminologies(
    async_session, real_chromadb,
) -> tuple[int, str, list[int]]:
    """ns + 6 canonical terminology KE (用于 inject_k=0 全量验证)."""
    async with async_session() as db:
        ns = Namespace(name="term6_ns", slug="term6_ns", description="phase4-term6")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        ids: list[int] = []
        terms = ["customer", "order", "product", "invoice", "payment", "shipment"]
        for term in terms:
            payload_dict = {
                "term": term,
                "primary_collection": term,
                "primary_database": "db_q",
                "db_type": "mongodb",
                "synonyms": [],
                "source_collections": [term],
            }
            import json
            ke = KnowledgeEntry(
                namespace_id=ns.id,
                entry_type="terminology",
                status="canonical",
                tier="normal",
                content=f"{term} 业务术语",
                source="manual",
                payload=json.dumps(payload_dict),
            )
            db.add(ke)
            await db.commit()
            await db.refresh(ke)
            ids.append(ke.id)
            upsert_knowledge_entry(
                slug=ns.slug,
                entry_id=ke.id,
                content=ke.content,
                tier="normal",
                namespace_id=ns.id,
                entry_type="terminology",
                status="canonical",
                payload=payload_dict,
            )
        return ns.id, ns.slug, ids


@pytest_asyncio.fixture
async def seeded_full_bundle(
    async_session, real_chromadb,
) -> tuple[int, str]:
    """ns + 1 critical rule + 1 terminology vectorized + 1 route_hint vectorized."""
    import json
    async with async_session() as db:
        ns = Namespace(name="full_ns", slug="full_ns", description="phase4-full")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        # critical
        crit = KnowledgeEntry(
            namespace_id=ns.id,
            entry_type="rule",
            status="canonical",
            tier="critical",
            content="critical-rule: 必须按 namespace 隔离",
            source="manual",
        )
        db.add(crit)

        # terminology (vectorized)
        t_payload = {
            "term": "customer",
            "primary_collection": "customer",
            "primary_database": "db_q",
            "db_type": "mongodb",
            "synonyms": ["客户"],
            "source_collections": ["customer"],
        }
        term_ke = KnowledgeEntry(
            namespace_id=ns.id,
            entry_type="terminology",
            status="canonical",
            tier="normal",
            content="customer 是客户集合",
            source="manual",
            payload=json.dumps(t_payload),
        )
        db.add(term_ke)

        # route_hint (vectorized)
        rh_payload = {
            "collection_path": ["customer", "orders"],
            "navigation_note": "customer.id ↔ orders.customer_id",
        }
        rh_ke = KnowledgeEntry(
            namespace_id=ns.id,
            entry_type="route_hint",
            status="canonical",
            tier="normal",
            content="客户订单关联查询路径",
            source="manual",
            payload=json.dumps(rh_payload),
        )
        db.add(rh_ke)
        await db.commit()
        await db.refresh(term_ke)
        await db.refresh(rh_ke)

        upsert_knowledge_entry(
            slug=ns.slug, entry_id=term_ke.id, content=term_ke.content,
            tier="normal", namespace_id=ns.id, entry_type="terminology",
            status="canonical", payload=t_payload,
        )
        upsert_knowledge_entry(
            slug=ns.slug, entry_id=rh_ke.id, content=rh_ke.content,
            tier="normal", namespace_id=ns.id, entry_type="route_hint",
            status="canonical", payload=rh_payload,
        )
        return ns.id, ns.slug


# ════════════════════════════════════════════
#  tests
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_basic_load_returns_bundle(async_session, seeded_ns):
    """空 ns: load 返 KnowledgeBundle 2 个空字段, 不抛."""
    ns_id, ns_slug = seeded_ns
    async with async_session() as db:
        bundle = await load_all_knowledge(db, ns_id, ns_slug, "查询客户")
    assert isinstance(bundle, KnowledgeBundle)
    assert bundle.critical == []
    assert bundle.vector_hits == []


@pytest.mark.asyncio
async def test_critical_loaded(async_session, seeded_critical_kes, real_chromadb):
    """critical tier 走 SQL 直加载, 不依赖 ChromaDB."""
    ns_id, ns_slug = seeded_critical_kes
    async with async_session() as db:
        bundle = await load_all_knowledge(db, ns_id, ns_slug, "随便问")
    assert "critical-rule-A" in bundle.critical
    assert "critical-rule-B" in bundle.critical
    assert len(bundle.critical) == 2


@pytest.mark.asyncio
async def test_terminology_full_inject_when_k_zero(
    async_session, seeded_ns_many_terminologies, monkeypatch,
):
    """knowledge_terminology_inject_k=0 — terminology 已独立走 AC 自动机, load_all_knowledge 不再加载."""
    ns_id, ns_slug, ids = seeded_ns_many_terminologies
    monkeypatch.setattr(settings, "knowledge_terminology_inject_k", 0)
    monkeypatch.setattr(settings, "knowledge_retrieve_normal_n", 20)

    async with async_session() as db:
        bundle = await load_all_knowledge(db, ns_id, ns_slug, "业务术语")
    # terminology 不再经 bundle 加载, bundle 只含 critical + vector_hits + route_hints
    assert not hasattr(bundle, "terminology_for_prompt")


@pytest.mark.asyncio
async def test_to_prompt_sections_renders_2_blocks(
    async_session, seeded_full_bundle,
):
    """to_prompt_sections() 返 2 key dict (critical_section / anchors_section).

    route_hint 已随 C5/C1 从 system prompt 摘除, 不再有 route_hints_section.
    """
    ns_id, ns_slug = seeded_full_bundle
    async with async_session() as db:
        bundle = await load_all_knowledge(db, ns_id, ns_slug, "客户订单")
    sections = bundle.to_prompt_sections()
    assert set(sections.keys()) == {"critical_section", "anchors_section"}
    assert "critical-rule" in sections["critical_section"]
    # anchors 至少有渲染体 (内容由 LLM 召回决定)
    assert isinstance(sections["anchors_section"], str)


