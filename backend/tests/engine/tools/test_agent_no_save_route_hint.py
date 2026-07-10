"""agent 不能生产 route_hint (spec 2026-07-08 C9/G8).

读写不对称: lookup 侧留 route_hint (agent 可召回), save 侧删 (生产权归人).
"""
import pytest

from app.engine.tools.registry import _LOOKUP_ENTRY_TYPES, _SAVE_ENTRY_TYPES
from app.knowledge.intake import VALID_ENTRY_TYPES


def test_save_entry_types_excludes_route_hint():
    assert "route_hint" not in _SAVE_ENTRY_TYPES
    # 四类仍在
    assert set(_SAVE_ENTRY_TYPES) == {"terminology", "instance_alias", "example", "rule"}


def test_lookup_entry_types_keeps_route_hint():
    """读侧保留 — agent 仍可召回人写的 route_hint."""
    assert "route_hint" in _LOOKUP_ENTRY_TYPES


def test_valid_entry_types_keeps_route_hint():
    """6 类宪章保留 — 人工 create API 需 route_hint 在内."""
    assert "route_hint" in VALID_ENTRY_TYPES


@pytest.mark.asyncio
async def test_agent_save_route_hint_rejected():
    """agent 手搓 entry_type=route_hint (绕过 enum) → guard 显式拒, 不写库."""
    from app.engine.tools import knowledge_tools as kt

    class FakeDB:
        added = []
        def add(self, obj): self.added.append(obj)
        async def flush(self): pass
        async def commit(self): pass
    db = FakeDB()
    result = await kt.save_knowledge(
        db=db, namespace_id=1, ns_slug="ns", sse_emit=None,
        entry_type="route_hint",
        content="某问题模式",
        payload={"collection_path": ["a"], "navigation_note": "n"},
        evidence={},
    )
    assert result == {"success": False, "reason": "route_hint_manual_only"}
    assert db.added == []  # 未写库
