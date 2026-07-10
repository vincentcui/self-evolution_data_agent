"""Stage 4 Task 3 — lookup_knowledge / save_knowledge tool 回归.

覆盖:
- lookup: entry_types 过滤隔离, 返 content/entry_type/status/tier/distance (无 entry_id/namespace_id)
- save:   source=agent_learn / status=proposed 默认, 写 KnowledgeEntry
- save:   entry_type 走 6 类宪章, 拼错拦截
"""

import pytest
from sqlalchemy import select
from unittest.mock import AsyncMock

from app.engine.tools.knowledge_tools import lookup_knowledge, save_knowledge
from app.knowledge.knowledge_retriever import upsert_knowledge_entry
from app.models import KnowledgeEntry
from app.models.namespace import Namespace


@pytest.mark.asyncio
async def test_lookup_knowledge_filters_by_entry_type(db_session, chroma_isolated):
    """lookup_knowledge 应按 types 过滤, 仅返指定类型."""
    ns = Namespace(slug="t3_ns", name="t3")
    db_session.add(ns)
    await db_session.flush()

    # terminology 多向量入库需要合法 TerminologyPayload (term + 路由字段),
    # 否则 upsert_knowledge_entry 会跳过多向量写入, 召回空.
    TERM_PAYLOAD = {
        "term": "订单",
        "synonyms": [],
        "primary_collection": "c_product",
        "primary_database": "db_t3",
        "db_type": "mongodb",
        "source_collections": ["c_product"],
    }
    for et, content, payload in [
        ("terminology", "订单 = c_product", TERM_PAYLOAD),
        ("rule", "只查 finalized", None),
    ]:
        import json as _json
        ke = KnowledgeEntry(namespace_id=ns.id, entry_type=et, content=content,
                             tier="normal", status="canonical", source="manual",
                             payload=_json.dumps(payload) if payload else "{}",
                             evidence_json="{}")
        db_session.add(ke)
        await db_session.flush()
        upsert_knowledge_entry(slug=ns.slug, entry_id=ke.id, content=content,
                                tier="normal", namespace_id=ns.id,
                                entry_type=et, status="canonical",
                                payload=payload)
    await db_session.commit()

    hits = await lookup_knowledge(
        db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
        query="订单", types=["terminology"], k=5,
    )
    assert len(hits) >= 1
    assert all(h["entry_type"] == "terminology" for h in hits)
    assert "entry_id" not in hits[0]
    assert "namespace_id" not in hits[0]
    assert "content" in hits[0]


@pytest.mark.asyncio
async def test_save_knowledge_writes_proposed_with_agent_learn_source(db_session):
    """save_knowledge 写入必须 status=proposed, source=agent_learn.

    entry_type 用 rule (非 route_hint) — C9 后 agent 侧 route_hint 一律拒 (manual-only),
    此测试验证的是通用写入路径, 与 entry_type 语义无关.
    """
    ns = Namespace(slug="t3b_ns", name="t3b")
    db_session.add(ns)
    await db_session.flush()

    out = await save_knowledge(
        db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
        sse_emit=AsyncMock(),
        entry_type="rule",
        content="商品→订单→条目 走 categoryId.productId 链",
        payload={"rule_text": "查询商品关联订单条目时优先走 categoryId 索引"},
        evidence={"trace_id": "trace-42", "success": True},
        tier="normal",
    )
    assert out["status"] == "proposed"
    assert out["entry_id"] > 0
    ke = (await db_session.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.id == out["entry_id"])
    )).scalar_one()
    assert ke.source == "agent_learn"
    assert ke.status == "proposed"
    assert ke.entry_type == "rule"


@pytest.mark.asyncio
async def test_save_knowledge_rejects_unknown_entry_type(db_session):
    """entry_type 走 6 类宪章 (intake.VALID_ENTRY_TYPES), 拼错抛 ValueError."""
    with pytest.raises(ValueError, match="entry_type"):
        await save_knowledge(
            db=db_session, namespace_id=None, ns_slug="__global__",
            sse_emit=AsyncMock(),
            entry_type="oops_wrong_type",
            content="x", payload={}, evidence={}, tier="normal",
        )
