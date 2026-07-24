"""Task 9 — HyQE example 端到端触发测试.

验证:
1. example approve → rewrite_hq_for_entry 触发 → ChromaDB 写入 hq_* 子向量
2. example delete → delete_knowledge_entry 触发 → hq_* 子向量全部清除 (无幽灵条目)

Mock 策略:
- LLM (_call_llm_for_hq_items): 返回可预测的 HQ 条目
- Embedding function: 使用确定性假向量 (无需真实 API key)
- ChromaDB: 使用真实 PersistentClient (chroma_isolated 临时目录)
- model_registry.resolve_chat_config: 返回 fake config
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import pytest_asyncio

from app.knowledge.hypothetical_queries import HQItem
from app.knowledge.knowledge_retriever import (
    delete_knowledge_entry,
    rewrite_hq_subvectors,
)
from app.knowledge.hq_writer import rewrite_hq_for_entry
from app.models.knowledge_entry import KnowledgeEntry


# ════════════════════════════════════════════
#  Fake Embedding Function
# ════════════════════════════════════════════

class FakeEmbeddingFunction:
    """确定性假 embedding — 基于文本 hash 生成 384 维向量, 无需 API key."""

    def __call__(self, input: list[str]) -> list[np.ndarray]:
        return [self._embed(t) for t in input]

    @staticmethod
    def _embed(text: str) -> np.ndarray:
        # 用 hash 做 seed 保证同文本 → 同向量
        seed = hash(text) % (2**31)
        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(384).astype(np.float32)
        return vec / np.linalg.norm(vec)

    @staticmethod
    def name() -> str:
        return "fake-embedding-test"

    def get_config(self) -> dict:
        return {}

    def default_space(self):
        return "cosine"


@pytest.fixture
def fake_embedding(monkeypatch):
    """Patch get_embedding_function 返回 fake, 同时重置 embedding 单例."""
    import app.engine.embedding as emb_mod

    fake = FakeEmbeddingFunction()
    monkeypatch.setattr(emb_mod, "_instance", fake)
    monkeypatch.setattr(emb_mod, "get_embedding_function", lambda: fake)
    return fake


# ════════════════════════════════════════════
#  Test 1: approve → HQ write
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_approve_example_writes_hq_subvectors(
    db_session, chroma_isolated, fake_embedding, monkeypatch,
):
    """example approve 触发 rewrite_hq_for_entry → ChromaDB 写入主 doc + hq_* 子向量."""
    from app.models.namespace import Namespace

    # ── seed: namespace + example KE (proposed → canonical 模拟 approve) ──
    ns = Namespace(name="hq_e2e_ns", slug="hq_e2e_ns", description="task9-e2e")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="example",
        content="按状态分组统计订单数\n统计各状态的订单数量",
        source="manual",
        status="canonical",
        tier="normal",
        is_superseded=False,
        payload=json.dumps({
            "question": "按状态分组统计订单数",
            "nl_paraphrases": ["统计各状态的订单数量"],
        }),
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    # ── mock: LLM 返回 3 条可预测 HQ ──
    fake_hq_items = [
        HQItem(q="如何按状态统计订单", covered_path=[]),
        HQItem(q="订单状态分组查询怎么写", covered_path=[]),
        HQItem(q="各状态订单数量聚合", covered_path=[]),
    ]

    # ── mock: model_registry.resolve_chat_config ──
    fake_registry = MagicMock()
    fake_registry.resolve_chat_config.return_value = {"model_name": "test-model"}

    with (
        patch(
            "app.knowledge.hypothetical_queries._call_llm_for_hq_items",
            return_value=fake_hq_items,
        ),
        patch(
            "app.engine.model_registry.registry",
            fake_registry,
        ),
    ):
        hqs = await rewrite_hq_for_entry(db_session, "hq_e2e_ns", entry)

    # ── assert: 返回的 HQ 列表 ──
    assert len(hqs) == 3
    assert hqs == ["如何按状态统计订单", "订单状态分组查询怎么写", "各状态订单数量聚合"]

    # ── assert: SQL hypothetical_queries_json 已写入 ──
    await db_session.refresh(entry)
    hq_json = json.loads(entry.hypothetical_queries_json)
    assert len(hq_json) == 3
    assert all(item["q"] for item in hq_json)
    assert all(item["model"] == "test-model" for item in hq_json)

    # ── assert: ChromaDB 包含主 doc + 3 个 hq_* 子向量 ──
    from app.engine.registry import get_chroma_client

    client = get_chroma_client()
    coll = client.get_collection(
        "ns_hq_e2e_ns_knowledge",
        embedding_function=fake_embedding,
    )
    # 查询该 entry_id 的所有文档
    results = coll.get(where={"entry_id": entry.id})
    assert len(results["ids"]) == 4, f"Expected 4 docs (main + 3 hq), got {results['ids']}"

    # 验证 doc_id 命名约定
    expected_ids = {
        f"ke_{entry.id}",
        f"ke_{entry.id}_hq_0",
        f"ke_{entry.id}_hq_1",
        f"ke_{entry.id}_hq_2",
    }
    assert set(results["ids"]) == expected_ids

    # 验证 metadata: hq_* 标记 is_hypothetical=True
    for i, meta in enumerate(results["metadatas"]):
        doc_id = results["ids"][i]
        if "_hq_" in doc_id:
            assert meta["is_hypothetical"] is True
            assert meta["hq_index"] >= 0
        else:
            assert meta["is_hypothetical"] is False
            assert meta["hq_index"] == -1
        assert meta["entry_type"] == "example"
        assert meta["entry_id"] == entry.id


# ════════════════════════════════════════════
#  Test 2: delete → HQ cleanup
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_example_clears_hq_subvectors(
    db_session, chroma_isolated, fake_embedding, monkeypatch,
):
    """删除 example 后, ChromaDB 中 hq_* 子向量全部清除, 无幽灵条目."""
    from app.models.namespace import Namespace

    # ── seed: namespace + example KE ──
    ns = Namespace(name="hq_del_ns", slug="hq_del_ns", description="task9-del")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="example",
        content="查询最近7天的活跃用户\n本周活跃用户数",
        source="manual",
        status="canonical",
        tier="normal",
        is_superseded=False,
        payload=json.dumps({
            "question": "查询最近7天的活跃用户",
            "nl_paraphrases": ["本周活跃用户数"],
        }),
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    # ── 先写入 ChromaDB: 主 doc + hq_* (模拟 approve 后的状态) ──
    rewrite_hq_subvectors(
        slug="hq_del_ns",
        entry_id=entry.id,
        entry_type="example",
        tier="normal",
        namespace_id=ns.id,
        content=entry.content,
        hq_list=["最近7天活跃用户怎么查", "本周活跃用户数查询"],
    )

    # ── 验证写入成功 (前置条件) ──
    from app.engine.registry import get_chroma_client

    client = get_chroma_client()
    coll = client.get_collection(
        "ns_hq_del_ns_knowledge",
        embedding_function=fake_embedding,
    )
    pre_delete = coll.get(where={"entry_id": entry.id})
    assert len(pre_delete["ids"]) == 3  # main + 2 hq

    # ── 执行删除 (模拟 delete API 调用) ──
    delete_knowledge_entry(
        slug="hq_del_ns",
        entry_id=entry.id,
        namespace_id=ns.id,
        entry_type="example",
    )

    # ── assert: ChromaDB 中该 entry_id 无任何残留 (无幽灵条目) ──
    post_delete = coll.get(where={"entry_id": entry.id})
    assert len(post_delete["ids"]) == 0, (
        f"Ghost entries remain after delete: {post_delete['ids']}"
    )
