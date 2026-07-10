"""end_turn 沉淀不再产 route_hint (spec 2026-07-08 C3)."""
import asyncio
from unittest.mock import patch

from app.api._async_extract_prompt import ASYNC_EXTRACT_PROMPT


def test_prompt_has_no_route_hint_reason():
    assert "route_hint_reason" not in ASYNC_EXTRACT_PROMPT


def test_prompt_output_contract_two_fields():
    # output 段只剩 question_pattern + result_summary
    assert '"question_pattern"' in ASYNC_EXTRACT_PROMPT
    assert '"result_summary"' in ASYNC_EXTRACT_PROMPT
    assert '"route_hint_reason"' not in ASYNC_EXTRACT_PROMPT


def test_async_extract_writes_no_route_hint(monkeypatch):
    """end_turn 沉淀只产 example, 不产 route_hint."""
    from app.api import query as q

    # chat_completion 是函数内 local import，需 patch 源模块
    def fake_chat(messages, **kw):
        return '{"question_pattern": "按状态分组统计订单数", "result_summary": "按 status 分组"}'

    captured = {}

    async def fake_write(*, db, ns_id, trace_id, question_pattern, example, evidence, **kwargs):
        captured["example"] = example
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr(q, "_should_extract", lambda r, s: (True, "test"))
    monkeypatch.setattr(q, "_write_extract_results", fake_write)

    class FakeResult:
        tool_trace = [{"name": "execute_query", "input": {
            "target": "shop.orders", "database": "shop", "query": {},
        }}]
        final_answer = "ok"

    with patch("app.engine.llm.chat_completion", side_effect=fake_chat):
        asyncio.run(q._async_extract_after_end_turn(
            ns_id=1, ns_slug="ns", question="各状态订单数",
            result=FakeResult(), trace_id="t1",
        ))

    assert captured["example"]["question_pattern"] == "按状态分组统计订单数"
    assert "route_hint" not in captured["kwargs"]
    assert "example" in captured
