"""Task 5 契约测试 — query.py 异步抽取路径 example KE 含 question_pattern + final_query_plan.

锁定 _async_extract_after_end_turn 产出的 example dict 形状:
  - question_pattern: 非空字符串 (LLM 产)
  - final_query_plan: {"steps": [...]} (_normalize_query_plan_impl 代码补)
  - collections: list[str] (代码侧抽)
  - join_keys: list[dict] (从 plan 抽)

防 T7 收紧 schema 后回归破坏.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest


# ── 含 execute_query 的 tool_trace, 让 normalize_query_plan 能产出 final_query_plan ──
TOOL_TRACE_WITH_EXECUTE_QUERY = [
    {"name": "lookup_knowledge", "input": {"query": "订单统计"}},
    {
        "name": "execute_query",
        "input": {
            "target": "orders",
            "database": "shop_db",
            "db_type": "mongodb",
            "query": {"pipeline": [
                {"$match": {"status": "paid"}},
                {"$group": {"_id": "$category", "count": {"$sum": 1}}},
            ]},
        },
    },
]


class FakeResult:
    """模拟 AgentResult."""
    tool_trace = TOOL_TRACE_WITH_EXECUTE_QUERY
    final_answer = "各品类已付款订单数统计完成"
    stop_reason = "end_turn"


def test_async_extract_example_has_question_pattern_and_final_query_plan(monkeypatch):
    """query.py 异步沉淀的 example 含 question_pattern + final_query_plan."""
    from app.api import query as q

    # mock LLM 返回 question_pattern + result_summary
    def fake_chat(messages, **kw):
        return json.dumps({
            "question_pattern": "按品类统计已付款订单数量",
            "result_summary": "按 category 分组统计 status=paid 的订单数",
        })

    captured: dict = {}

    async def fake_write(*, db, ns_id, trace_id, question_pattern, example, evidence, **kwargs):
        captured["example"] = example
        captured["question_pattern"] = question_pattern
        captured["evidence"] = evidence
        return None

    monkeypatch.setattr(q, "_should_extract", lambda r, s: (True, "test"))
    monkeypatch.setattr(q, "_write_extract_results", fake_write)

    with patch("app.engine.llm.chat_completion", side_effect=fake_chat):
        asyncio.run(q._async_extract_after_end_turn(
            ns_id=1, ns_slug="test_ns", question="各品类已付款订单数",
            result=FakeResult(), trace_id="t-contract-1",
        ))

    example = captured["example"]

    # ── 核心契约断言 ──
    assert "question_pattern" in example and example["question_pattern"], (
        f"example 缺 question_pattern: {example.keys()}"
    )
    assert example["question_pattern"] == "按品类统计已付款订单数量"

    assert "final_query_plan" in example and example["final_query_plan"], (
        f"example 缺 final_query_plan: {example.keys()}"
    )
    assert "steps" in example["final_query_plan"], (
        f"final_query_plan 缺 steps: {example['final_query_plan']}"
    )
    assert len(example["final_query_plan"]["steps"]) > 0, "steps 不应为空"

    # collections 应非空
    assert "collections" in example
    assert isinstance(example["collections"], list)
    assert len(example["collections"]) > 0, "collections 不应为空"

    # join_keys 应存在 (可为空 list)
    assert "join_keys" in example
    assert isinstance(example["join_keys"], list)

    # result_summary 应存在
    assert "result_summary" in example


def test_async_extract_final_query_plan_step_shape(monkeypatch):
    """final_query_plan.steps[0] 含 db_type/database/collection/operation/query."""
    from app.api import query as q

    def fake_chat(messages, **kw):
        return json.dumps({
            "question_pattern": "按品类统计已付款订单数量",
            "result_summary": "统计结果",
        })

    captured: dict = {}

    async def fake_write(*, db, ns_id, trace_id, question_pattern, example, evidence, **kwargs):
        captured["example"] = example
        return None

    monkeypatch.setattr(q, "_should_extract", lambda r, s: (True, "test"))
    monkeypatch.setattr(q, "_write_extract_results", fake_write)

    with patch("app.engine.llm.chat_completion", side_effect=fake_chat):
        asyncio.run(q._async_extract_after_end_turn(
            ns_id=1, ns_slug="test_ns", question="各品类已付款订单数",
            result=FakeResult(), trace_id="t-contract-2",
        ))

    step = captured["example"]["final_query_plan"]["steps"][0]
    # step 应含标准字段
    assert "db_type" in step
    assert "database" in step
    assert "collection" in step
    assert "operation" in step
    assert "query" in step
    assert step["db_type"] == "mongodb"
    assert step["database"] == "shop_db"
    assert step["collection"] == "orders"
    assert step["operation"] == "aggregate"


def test_async_extract_content_is_question_pattern(monkeypatch):
    """_write_extract_results 的 content 参数 = question_pattern (NL 语义骨架)."""
    from app.api import query as q

    def fake_chat(messages, **kw):
        return json.dumps({
            "question_pattern": "查询本月新增用户数",
            "result_summary": "统计结果",
        })

    captured: dict = {}

    async def fake_write(*, db, ns_id, trace_id, question_pattern, example, evidence, **kwargs):
        captured["question_pattern"] = question_pattern
        captured["example"] = example
        return None

    monkeypatch.setattr(q, "_should_extract", lambda r, s: (True, "test"))
    monkeypatch.setattr(q, "_write_extract_results", fake_write)

    with patch("app.engine.llm.chat_completion", side_effect=fake_chat):
        asyncio.run(q._async_extract_after_end_turn(
            ns_id=1, ns_slug="test_ns", question="本月新增用户",
            result=FakeResult(), trace_id="t-contract-3",
        ))

    # content (question_pattern 参数) 与 example.question_pattern 一致
    assert captured["question_pattern"] == "查询本月新增用户数"
    assert captured["example"]["question_pattern"] == "查询本月新增用户数"
