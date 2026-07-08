"""knowledge_loader.py 错误处理分支 + 边界条件覆盖.

覆盖目标 (coverage report missing lines):
- KnowledgeBundle.to_prompt_sections / _render_critical / _render_route_hints (空分支)
- batch_load_terminology (正常 / JSON 解析失败 / 缺字段)
- _batch_load_route_hints (JSON 解析失败 / entry 不存在)
- _load_inner rh_k=0 分支
- load_all_knowledge timeout / exception 降级
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from app.knowledge.knowledge_loader import (
    KnowledgeBundle,
    _batch_load_route_hints,
    _empty_bundle,
    batch_load_terminology,
    load_all_knowledge,
)
from app.models.knowledge_entry import KnowledgeEntry

# ════════════════════════════════════════════
#  KnowledgeBundle 渲染
# ════════════════════════════════════════════


def test_empty_bundle_renders_empty_sections():
    """空 bundle → 所有 section 为空字符串."""
    b = _empty_bundle()
    sections = b.to_prompt_sections()
    assert sections["critical_section"] == ""
    assert sections["anchors_section"] == ""
    assert sections["route_hints_section"] == ""


def test_bundle_renders_critical_section():
    """有 critical → 渲染 markdown 列表."""
    b = KnowledgeBundle(
        critical=["规则 A", "规则 B"],
        vector_hits=[],
        route_hints_for_prompt=[],
    )
    sections = b.to_prompt_sections()
    assert "## 关键规则 (critical)" in sections["critical_section"]
    assert "- 规则 A" in sections["critical_section"]
    assert "- 规则 B" in sections["critical_section"]


# ════════════════════════════════════════════
#  batch_load_terminology
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_batch_load_terminology_empty_ids(db):
    """空 entry_ids → 直接返空列表, 不查 DB."""
    result = await batch_load_terminology(db, [])
    assert result == []


@pytest.mark.asyncio
async def test_batch_load_terminology_normal(db):
    """正常 payload → 返 TerminologyAnchor."""
    from app.models import Namespace
    ns = Namespace(name="t", slug="term-test")
    db.add(ns)
    await db.commit()

    ke = KnowledgeEntry(
        namespace_id=ns.id, entry_type="terminology",
        content="订单", tier="normal", status="canonical",
        source="manual",
        payload=json.dumps({
            "term": "订单", "primary_collection": "orders",
            "primary_database": "ecom", "db_type": "mongodb",
            "synonyms": ["单子"],
        }, ensure_ascii=False),
        evidence_json="{}",
    )
    db.add(ke)
    await db.commit()

    result = await batch_load_terminology(db, [ke.id])
    assert len(result) == 1
    assert result[0].term == "订单"
    assert result[0].primary_collection == "orders"
    assert "单子" in result[0].synonyms


@pytest.mark.asyncio
async def test_batch_load_terminology_json_decode_error(db, caplog):
    """payload 非法 JSON → 跳过该条, log warning."""
    from app.models import Namespace
    ns = Namespace(name="t", slug="term-json-err")
    db.add(ns)
    await db.commit()

    ke = KnowledgeEntry(
        namespace_id=ns.id, entry_type="terminology",
        content="x", tier="normal", status="canonical",
        source="manual",
        payload="not-json{{{",
        evidence_json="{}",
    )
    db.add(ke)
    await db.commit()

    with caplog.at_level("WARNING"):
        result = await batch_load_terminology(db, [ke.id])
    assert result == []
    assert "payload not JSON" in caplog.text


@pytest.mark.asyncio
async def test_batch_load_terminology_missing_field(db, caplog):
    """payload 缺必填字段 (如 term) → 跳过, log warning."""
    from app.models import Namespace
    ns = Namespace(name="t", slug="term-miss")
    db.add(ns)
    await db.commit()

    ke = KnowledgeEntry(
        namespace_id=ns.id, entry_type="terminology",
        content="x", tier="normal", status="canonical",
        source="manual",
        payload=json.dumps({"primary_collection": "c", "primary_database": "d", "db_type": "mongodb"}),
        evidence_json="{}",
    )
    db.add(ke)
    await db.commit()

    with caplog.at_level("WARNING"):
        result = await batch_load_terminology(db, [ke.id])
    assert result == []
    assert "missing field" in caplog.text


# ════════════════════════════════════════════
#  _batch_load_route_hints
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_batch_load_route_hints_json_error(db, caplog):
    """route_hint payload 非法 JSON → 跳过."""
    from app.models import Namespace
    ns = Namespace(name="t", slug="rh-json-err")
    db.add(ns)
    await db.commit()

    ke = KnowledgeEntry(
        namespace_id=ns.id, entry_type="route_hint",
        content="x", tier="normal", status="canonical",
        source="manual",
        payload="broken-json",
        evidence_json="{}",
    )
    db.add(ke)
    await db.commit()

    with caplog.at_level("WARNING"):
        result = await _batch_load_route_hints(db, [ke.id])
    assert result == []
    assert "payload not JSON" in caplog.text


@pytest.mark.asyncio
async def test_batch_load_route_hints_missing_entry(db):
    """entry_id 不存在 → 跳过 (不报错)."""
    result = await _batch_load_route_hints(db, [99999])
    assert result == []


# ════════════════════════════════════════════
#  load_all_knowledge 降级
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_load_all_knowledge_timeout_returns_empty(db, caplog):
    """_load_inner 超时 → 返空 bundle, log warning."""
    async def _slow_inner(*args, **kwargs):
        await asyncio.sleep(10)

    with patch("app.knowledge.knowledge_loader._load_inner", side_effect=_slow_inner):
        with patch("app.knowledge.knowledge_loader.settings") as mock_settings:
            mock_settings.knowledge_loader_timeout_secs = 0.01
            with caplog.at_level("WARNING"):
                result = await load_all_knowledge(db, 1, "test", "q")
    assert result.critical == []
    assert result.vector_hits == []
    assert "timeout" in caplog.text


@pytest.mark.asyncio
async def test_load_all_knowledge_exception_returns_empty(db, caplog):
    """_load_inner 抛异常 → 返空 bundle, log exception."""
    async def _broken_inner(*args, **kwargs):
        raise RuntimeError("DB connection lost")

    with patch("app.knowledge.knowledge_loader._load_inner", side_effect=_broken_inner):
        with caplog.at_level("ERROR"):
            result = await load_all_knowledge(db, 1, "test", "q")
    assert result.critical == []
    assert "unexpected failure" in caplog.text
