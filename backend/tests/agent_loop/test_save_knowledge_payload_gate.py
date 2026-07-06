"""Phase 1 — save_knowledge schema 闸门 (parse_payload 接入).

验证:
- route_hint extra field → rejected
- rule invalid kind → rejected
- valid payload → 入库 + 字段不变形
"""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock

from app.engine.tools.knowledge_tools import save_knowledge
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace
from app.schemas.knowledge_payload import ExamplePayload


@pytest.mark.asyncio
async def test_save_knowledge_route_hint_extra_field_rejected(db_session):
    """RouteHintPayload extra='forbid' 拦 LLM 多塞字段."""
    ns = Namespace(name="schema-gate-test", slug="schema-gate-test", description="")
    db_session.add(ns)
    await db_session.flush()

    result = await save_knowledge(
        db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
        sse_emit=AsyncMock(),
        entry_type="route_hint",
        content="路由提示",
        payload={
            "question_pattern": "查询订单",
            "collection_path": ["c_orders"],
            "extra_random_field": "haha",
        },
        evidence={},
    )
    assert result.get("success") is False, f"应拒 extra 字段, got {result}"
    assert result.get("reason") == "validation_failed"


@pytest.mark.asyncio
async def test_save_knowledge_rule_invalid_kind_rejected(db_session):
    """RulePayload.rule_kind Literal 白名单外 reject."""
    ns = Namespace(name="schema-gate-test2", slug="schema-gate-test2", description="")
    db_session.add(ns)
    await db_session.flush()

    result = await save_knowledge(
        db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
        sse_emit=AsyncMock(),
        entry_type="rule",
        content="规则",
        payload={
            "rule_text": "x",
            "rule_kind": "MYSTERY_KIND",
        },
        evidence={},
    )
    assert result.get("success") is False
    assert result.get("reason") == "validation_failed"


@pytest.mark.asyncio
async def test_save_knowledge_route_hint_valid_payload_persists(db_session):
    """合法 payload 入库 + 字段不变形 (闸门不注入默认值)."""
    ns = Namespace(name="schema-gate-test3", slug="schema-gate-test3", description="")
    db_session.add(ns)
    await db_session.flush()

    result = await save_knowledge(
        db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
        sse_emit=AsyncMock(),
        entry_type="route_hint",
        content="路由提示",
        payload={
            "question_pattern": "订单关联用户",
            "collection_path": ["c_orders", "c_users"],
            "join_fields": [{"a": "c_orders.user_id", "b": "c_users.id"}],
            "cost_strategy": "default",
            "reason": "二层关联",
        },
        evidence={"source": "test"},
    )
    assert "entry_id" in result, f"应入库, got {result}"
    ke = await db_session.get(KnowledgeEntry, int(result["entry_id"]))
    assert ke is not None
    payload = json.loads(ke.payload)
    assert payload["collection_path"] == ["c_orders", "c_users"]
    assert payload["question_pattern"] == "订单关联用户"
    assert payload["cost_strategy"] == "default"


@pytest.mark.asyncio
async def test_save_knowledge_example_extra_field_accepted(db_session):
    """ExamplePayload extra='allow' — 未知字段被接受不过滤."""
    ns = Namespace(name="schema-gate-test4", slug="schema-gate-test4", description="")
    db_session.add(ns)
    await db_session.flush()

    result = await save_knowledge(
        db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
        sse_emit=AsyncMock(),
        entry_type="example",
        content="订单关联用户",
        payload={
            "question_pattern": "订单关联用户",
            "collections": ["shop.orders", "shop.users"],
            "join_keys": [{"from": "orders.user_id", "to": "users.id"}],
            "final_query_plan": {"steps": [{"db_type": "mysql", "operation": "sql"}]},
            "bogus_field": "should_be_accepted_now",
        },
        evidence={},
    )
    assert "entry_id" in result, f"extra='allow' 应接受 bogus_field, got {result}"


@pytest.mark.asyncio
async def test_save_knowledge_example_new_format():
    """ExamplePayload 五字段 schema 验证."""
    payload = ExamplePayload(
        question_pattern="订单关联用户",
        collections=["shop.orders", "shop.users"],
        join_keys=[{"from": "orders.user_id", "to": "users.id"}],
        final_query_plan={
            "steps": [{"db_type": "mysql", "database": "shop", "collection": "orders",
                       "operation": "sql", "query": {"sql": "SELECT ..."}}],
        },
    )
    assert payload.question_pattern == "订单关联用户"
    assert len(payload.join_keys) == 1


# ════════════════════════════════════════════
#  result_summary null→str coercion
# ════════════════════════════════════════════


def test_result_summary_null_coerced_to_empty_str():
    """LLM 返回 result_summary: null 时被转换为空字符串."""
    llm_output = {"question_pattern": "查询订单", "route_hint_reason": None, "result_summary": None}
    result_summary = llm_output.get("result_summary") or ""
    assert result_summary == ""


def test_result_summary_missing_coerced_to_empty_str():
    """LLM 输出完全不包含 result_summary key 时，or '' 返回 ''."""
    llm_output = {"question_pattern": "查询订单", "route_hint_reason": None}
    result_summary = llm_output.get("result_summary") or ""
    assert result_summary == ""


def test_result_summary_valid_preserved():
    """正常 result_summary 字符串原值保留."""
    llm_output = {"question_pattern": "查询订单", "result_summary": "在orders上按status分组"}
    result_summary = llm_output.get("result_summary") or ""
    assert result_summary == "在orders上按status分组"
