"""停滞检测 + cost_exhausted 测试."""
from __future__ import annotations
import asyncio
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


@pytest.mark.asyncio
async def test_no_progress_stagnates_before_total(monkeypatch):
    """连续无结果提前 stagnation，不跑满 total_iterations。"""
    from app.engine import agent_loop as al
    monkeypatch.setattr(al.settings, "agent_loop_dead_loop_window", 3)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 40)
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 100)

    responses = [
        _tuc(calls=[ToolCall(id=f"c{i}", name="lookup_knowledge",
                              input={"query": f"q{i}"})], stop="tool_use")
        for i in range(10)
    ]
    llm = FakeLLM(responses=responses)
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t-stagnation", question="q",
        tools_registry={"lookup_knowledge": lambda **kw: []},  # 返回空列表 = 无进展
        tool_specs=[], sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm, system_prompt="",
    )
    assert result.stop_reason == "stagnation"
    assert result.iterations == 3
    assert any("停滞检测" in e.get("data", {}).get("message", "") for e in events)


@pytest.mark.asyncio
async def test_same_tool_same_input_still_dead_loop(monkeypatch):
    """同 tool + 同参数仍然 dead_loop。"""
    from app.engine import agent_loop as al
    monkeypatch.setattr(al.settings, "agent_loop_dead_loop_window", 3)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 40)
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 100)

    responses = [
        _tuc(calls=[ToolCall(id=f"c{i}", name="lookup_knowledge",
                              input={"query": "same"})], stop="tool_use")
        for i in range(10)
    ]
    llm = FakeLLM(responses=responses)
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t-dead-loop", question="q",
        tools_registry={"lookup_knowledge": lambda **kw: {"hit_count": 1}},
        tool_specs=[], sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm, system_prompt="",
    )
    assert result.stop_reason == "dead_loop"


@pytest.mark.asyncio
async def test_progress_outputs_continue_to_cost_exhausted(monkeypatch):
    """有进展不被 stagnation 误杀，跑到 cost_exhausted。"""
    from app.engine import agent_loop as al
    monkeypatch.setattr(al.settings, "agent_loop_dead_loop_window", 3)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 5)
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 100)

    responses = [
        _tuc(calls=[ToolCall(id=f"c{i}", name="lookup_knowledge",
                              input={"query": f"q{i}"})], stop="tool_use")
        for i in range(10)
    ]
    llm = FakeLLM(responses=responses)
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t-cost", question="q",
        tools_registry={"lookup_knowledge": lambda **kw: {"hit_count": 1}},
        tool_specs=[], sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm, system_prompt="",
    )
    assert result.stop_reason == "cost_exhausted"
    assert result.iterations == 5
