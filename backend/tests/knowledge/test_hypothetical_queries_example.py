# tests/knowledge/test_hypothetical_queries_example.py
import pytest

def test_generate_hq_for_example_returns_items(monkeypatch):
    """example 类型进 HyQE, 返 N 条假设触发问题 (list[str], 每条即 q 文本).

    C2 修正: generate_hq_with_validation 是同步函数 (hypothetical_queries.py:109
    def 非 async, 返 list[str] via [item.q for item in raw_items] line 132), 不可 await;
    monkeypatch 目标是同步的 _call_llm_for_hq_items (返 list[HQItem]), 非 async fake_call;
    断言 items 是 list[str] 非 list[dict].
    """
    from app.knowledge import hypothetical_queries as hq
    from app.knowledge.hypothetical_queries import HQItem

    def fake_call(content, entry_type, n, namespace_id=None):
        # _call_llm_for_hq_items 真实签名: 同步, 返 list[HQItem]
        return [HQItem(q=f"问法{i}") for i in range(3)]
    monkeypatch.setattr(hq, "_call_llm_for_hq_items", fake_call)

    items = hq.generate_hq_with_validation(
        content="按状态分组统计订单数", entry_type="example",
        route_collection_path=None, n=3, namespace_id=1,
    )
    assert len(items) == 3
    assert all(isinstance(i, str) for i in items)
    assert all(i for i in items)

def test_example_in_enabled_entry_types():
    from app.knowledge.hypothetical_queries import ENABLED_ENTRY_TYPES
    assert "example" in ENABLED_ENTRY_TYPES
