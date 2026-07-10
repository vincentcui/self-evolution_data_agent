"""references 读时投影 (spec 2026-07-08 C1/D1/D2).

门控: rule/route_hint/无 plan 的 example APPLY; terminology/instance_alias/有 plan 的 example SKIP.
复用 match_terminology + batch_load_terminology, 无新缓存.
"""
from unittest.mock import AsyncMock

import pytest

from app.engine.tools.knowledge_tools import _needs_references, _prose_for_scan


def test_needs_references_rule():
    assert _needs_references("rule", {"rule_text": "x"}) is True


def test_needs_references_route_hint():
    assert _needs_references("route_hint", {"navigation_note": "x"}) is True


def test_needs_references_example_without_plan():
    assert _needs_references("example", {"result_summary": "x"}) is True


def test_needs_references_example_with_plan():
    assert _needs_references("example", {"final_query_plan": {"steps": []}}) is False


def test_needs_references_terminology():
    assert _needs_references("terminology", {"term": "x"}) is False


def test_needs_references_instance_alias():
    assert _needs_references("instance_alias", {"alias": "x"}) is False


def test_prose_for_scan_rule():
    s = _prose_for_scan("rule", "rule-content", {"rule_text": "订单明细从 order_items 查"})
    assert "rule-content" in s and "订单明细从 order_items 查" in s


def test_prose_for_scan_route_hint():
    s = _prose_for_scan("route_hint", "q-pattern", {"navigation_note": "sku 关联"})
    assert "q-pattern" in s and "sku 关联" in s


def test_prose_for_scan_example_no_plan():
    s = _prose_for_scan("example", "q", {"result_summary": "按 status 分组"})
    assert "按 status 分组" in s


@pytest.mark.asyncio
async def test_lookup_attaches_references_on_rule(monkeypatch):
    """rule 召回, 文本含已知术语 → 附 references."""
    from app.engine.tools import knowledge_tools as kt

    # 构造一条 rule hit
    class FakeHit:
        entry_id = 101
        content = "订单明细从 order_items 查"
        entry_type = "rule"
        status = "canonical"
        distance = 0.1
        tier = "normal"
    hits = [FakeHit()]

    # _retrieve_layer3 生产是同步函数 (asyncio.to_thread 包装调用), 用同步 lambda 而非 AsyncMock
    monkeypatch.setattr(kt, "_retrieve_layer3", lambda *a, **kw: hits)
    monkeypatch.setattr(kt, "get_embedding_function", lambda: (lambda texts: [[0.0]]))
    monkeypatch.setattr(kt, "match_terminology", lambda ns, text: [555])  # 命中术语 entry_id=555
    # batch_load_terminology 返带 entry_id 的 anchor
    from app.knowledge.knowledge_loader import TerminologyAnchor
    monkeypatch.setattr(kt, "batch_load_terminology", AsyncMock(return_value=[
        TerminologyAnchor(entry_id=555, term="订单明细", target="order_items",
                          database="shop", db_type="mongodb")
    ]))
    # payload 回查
    class FakeRow:
        id = 101
        payload = '{"rule_text": "订单明细从 order_items 查"}'
    class FakeDB:
        async def execute(self, stmt):
            class R:
                def all(self): return [FakeRow()]
            return R()
        async def commit(self): pass
    result = await kt.lookup_knowledge(db=FakeDB(), namespace_id=1, ns_slug="ns",
                                        query="订单", types=["rule"], k=5)
    assert len(result) == 1
    refs = result[0].get("references")
    assert refs is not None
    assert refs[0]["term"] == "订单明细"
    assert refs[0]["target"] == "order_items"
    assert refs[0]["db_type"] == "mongodb"


@pytest.mark.asyncio
async def test_lookup_no_references_on_terminology(monkeypatch):
    """terminology 召回 → 无 references 键."""
    from app.engine.tools import knowledge_tools as kt

    class FakeHit:
        entry_id = 1
        content = "活跃用户"
        entry_type = "terminology"
        status = "canonical"
        distance = 0.1
        tier = "normal"
    monkeypatch.setattr(kt, "_retrieve_layer3", lambda *a, **kw: [FakeHit()])
    monkeypatch.setattr(kt, "get_embedding_function", lambda: (lambda texts: [[0.0]]))
    monkeypatch.setattr(kt, "match_terminology", lambda ns, text: [])
    class FakeRow:
        id = 1
        payload = (
            '{"term":"活跃用户","primary_collection":"users",'
            '"primary_database":"shop","db_type":"mongodb"}'
        )
    class FakeDB:
        async def execute(self, stmt):
            class R:
                def all(self): return [FakeRow()]
            return R()
        async def commit(self): pass
    result = await kt.lookup_knowledge(
        db=FakeDB(), namespace_id=1, ns_slug="ns",
        query="用户", types=["terminology"], k=5,
    )
    assert "references" not in result[0]


@pytest.mark.asyncio
async def test_lookup_graceful_when_match_returns_empty(monkeypatch):
    """rule 召回但 ns 无术语命中 (match_terminology 返空) → 条目仍返回, 无 references 键, 不抛异常.

    覆盖 spec §G1 第 4 条: 缺结构化路由条目扫 prose 过 AC 自动机无命中时优雅降级.
    """
    from app.engine.tools import knowledge_tools as kt

    class FakeHit:
        entry_id = 202
        content = "按 status 分组统计订单"
        entry_type = "rule"
        status = "canonical"
        distance = 0.1
        tier = "normal"
    monkeypatch.setattr(kt, "_retrieve_layer3", lambda *a, **kw: [FakeHit()])
    monkeypatch.setattr(kt, "get_embedding_function", lambda: (lambda texts: [[0.0]]))
    # ns 无自动机缓存 / 无命中 → 返空, 投影块应跳过 batch_load, 不抛异常
    monkeypatch.setattr(kt, "match_terminology", lambda ns, text: [])
    monkeypatch.setattr(kt, "batch_load_terminology", AsyncMock(return_value=[]))
    class FakeRow:
        id = 202
        payload = '{"rule_text": "按 status 分组统计订单"}'
    class FakeDB:
        async def execute(self, stmt):
            class R:
                def all(self): return [FakeRow()]
            return R()
        async def commit(self): pass
    result = await kt.lookup_knowledge(
        db=FakeDB(), namespace_id=1, ns_slug="ns",
        query="订单", types=["rule"], k=5,
    )
    assert len(result) == 1
    assert "references" not in result[0]
