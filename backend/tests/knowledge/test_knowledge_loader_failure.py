"""Phase 4 Task 4.1 — load_all_knowledge 失败兜底验证.

3 case: critical SQL 错 / chromadb 错 / overall timeout — 任一情况返空 bundle 不抛.

设计: load_all_knowledge 是只增强非阻断, 失败必须降级而不是炸 pipeline.
"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.knowledge import knowledge_loader
from app.knowledge.knowledge_loader import KnowledgeBundle, load_all_knowledge
from app.models.namespace import Namespace


@pytest_asyncio.fixture
async def seeded_ns(async_session) -> tuple[int, str]:
    async with async_session() as db:
        ns = Namespace(name="fail_ns", slug="fail_ns", description="phase4-fail")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        return ns.id, ns.slug


@pytest.mark.asyncio
async def test_critical_sql_error_returns_empty_critical(
    async_session, seeded_ns, monkeypatch,
):
    """SQL 失败 (mock _load_layer1_knowledge 抛错) → bundle.critical=[] 但不抛."""
    ns_id, ns_slug = seeded_ns

    async def _broken(*args, **kwargs):
        raise RuntimeError("simulated SQL failure")

    monkeypatch.setattr(knowledge_loader, "_load_layer1_knowledge", _broken)

    async with async_session() as db:
        bundle = await load_all_knowledge(db, ns_id, ns_slug, "查询")
    assert isinstance(bundle, KnowledgeBundle)
    assert bundle.critical == []


@pytest.mark.asyncio
async def test_chromadb_error_returns_empty_vectors(
    async_session, seeded_ns, monkeypatch,
):
    """_retrieve_layer3 抛错 → bundle.vector_hits=[] 但不抛."""
    ns_id, ns_slug = seeded_ns

    def _broken(*args, **kwargs):
        raise RuntimeError("simulated ChromaDB failure")

    monkeypatch.setattr(knowledge_loader, "_retrieve_layer3", _broken)

    async with async_session() as db:
        bundle = await load_all_knowledge(db, ns_id, ns_slug, "查询")
    assert isinstance(bundle, KnowledgeBundle)
    assert bundle.vector_hits == []


@pytest.mark.asyncio
async def test_overall_timeout_returns_empty_bundle(
    async_session, seeded_ns, monkeypatch,
):
    """整体超时 → 空 bundle (不抛)."""
    ns_id, ns_slug = seeded_ns

    # 强制 _load_inner 卡住超过 timeout
    async def _slow_inner(*args, **kwargs):
        await asyncio.sleep(10)  # 远大于 timeout
        return knowledge_loader._empty_bundle()

    monkeypatch.setattr(knowledge_loader, "_load_inner", _slow_inner)
    monkeypatch.setattr(settings, "knowledge_loader_timeout_secs", 1)

    async with async_session() as db:
        bundle = await load_all_knowledge(db, ns_id, ns_slug, "查询")
    assert isinstance(bundle, KnowledgeBundle)
    assert bundle.critical == []
    assert bundle.vector_hits == []
