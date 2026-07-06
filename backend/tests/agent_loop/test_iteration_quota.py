"""3 桶配额 + 开关行为."""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.engine.agent_loop import run_agent_loop
from app.engine.llm import ToolCall, ToolUseResponse


def _tuc(*, calls=None, text="", stop="end_turn"):
    return ToolUseResponse(
        text=text, tool_calls=calls or [], stop_reason=stop,
        usage={"input_tokens": 1, "output_tokens": 1},
    )


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    async def __call__(self, *, messages, tools, stream_callback=None):
        await asyncio.sleep(0)
        return self._responses.pop(0)


def _sse_sink():
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)
    return events, emit


def _make_tool(name, output):
    async def fn(**_kw):
        return output
    return fn


@pytest.mark.asyncio
async def test_max_exploratory_calls_soft_limit(monkeypatch, caplog):
    """exploratory 超软上限只记 warning，不硬停；继续到 cost_exhausted."""
    from app.engine import agent_loop as al
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 3)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 100)
    # total_iterations 作真正的成本后墙
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 6)

    responses = [
        _tuc(calls=[ToolCall(id=f"c{i}", name="lookup_knowledge",
                              input={"query": f"q{i}"})], stop="tool_use")
        for i in range(10)
    ]
    llm = FakeLLM(responses=responses)
    events, emit = _sse_sink()

    with caplog.at_level(logging.WARNING, logger="app.engine.agent_loop"):
        result = await run_agent_loop(
            trace_id="t-explore-soft", question="q",
            tools_registry={"lookup_knowledge": _make_tool("lookup_knowledge", {"hit_count": 1})},
            tool_specs=[], sse_emit=emit,
            user_correction_queue=asyncio.Queue(),
            llm=llm, system_prompt="",
        )

    # 软上限：不硬停，由 cost_exhausted 成本后墙终止
    assert result.stop_reason == "cost_exhausted"
    # 超软上限时记了 warning
    assert any("超软上限" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_max_decisive_calls_soft_limit(monkeypatch, caplog):
    """decisive 超软上限只记 warning，不硬停；继续到 total_iterations."""
    from app.engine import agent_loop as al
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 2)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 5)

    responses = [
        _tuc(calls=[ToolCall(id=f"c{i}", name="execute_plan",
                              input={"plan_id": i})], stop="tool_use")
        for i in range(10)
    ]
    llm = FakeLLM(responses=responses)
    events, emit = _sse_sink()

    with caplog.at_level(logging.WARNING, logger="app.engine.agent_loop"):
        result = await run_agent_loop(
            trace_id="t-decisive-soft", question="q",
            tools_registry={"execute_plan": _make_tool("execute_plan", {"ok": True})},
            tool_specs=[], sse_emit=emit,
            user_correction_queue=asyncio.Queue(),
            llm=llm, system_prompt="",
        )

    assert result.stop_reason == "cost_exhausted"
    assert any("超软上限" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_max_total_iterations_triggers(monkeypatch):
    """总轮次到 cap 触发 cost_exhausted."""
    from app.engine import agent_loop as al
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 3)

    responses = [
        _tuc(calls=[
            ToolCall(id=f"e{i}", name="lookup_knowledge", input={"q": str(i)}),
        ], stop="tool_use")
        for i in range(10)
    ]
    llm = FakeLLM(responses=responses)
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t-total-cap", question="q",
        tools_registry={"lookup_knowledge": _make_tool("lookup_knowledge", {"hit_count": 1})},
        tool_specs=[], sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm, system_prompt="",
    )
    assert result.stop_reason == "cost_exhausted"
    assert result.iterations == 3


@pytest.mark.asyncio
async def test_clarify_does_not_count_towards_explore_decisive(monkeypatch):
    """clarify_with_user 不计入 exploratory/decisive 桶."""
    from app.engine import agent_loop as al
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 1)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 1)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 100)

    responses = [
        _tuc(calls=[ToolCall(id=f"c{i}", name="clarify_with_user",
                              input={"question": f"q{i}"})], stop="tool_use")
        for i in range(5)
    ] + [_tuc(text="done", stop="end_turn")]
    llm = FakeLLM(responses=responses)
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t-clarify-free", question="q",
        tools_registry={
            "clarify_with_user": _make_tool("clarify_with_user", "user said x"),
        },
        tool_specs=[], sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm, system_prompt="",
    )
    assert result.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_iteration_limit_disabled_bypasses_quotas(monkeypatch):
    """enabled=False 时, 即使超配额也不触发 max_*_calls。工具有进展则不触发 stagnation。"""
    from app.engine import agent_loop as al
    monkeypatch.setattr(al.settings, "agent_loop_iteration_limit_enabled", False)
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 1)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 1)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 1)

    responses = [
        _tuc(calls=[ToolCall(id=f"c{i}", name="lookup_knowledge",
                              input={"q": str(i)})], stop="tool_use")
        for i in range(10)
    ] + [_tuc(text="done", stop="end_turn")]
    llm = FakeLLM(responses=responses)
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t-disabled", question="q",
        tools_registry={"lookup_knowledge": _make_tool("lookup_knowledge", {"hit_count": 1})},
        tool_specs=[], sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm, system_prompt="",
    )
    assert result.stop_reason == "end_turn"
    assert result.iterations >= 10
