"""Stage 4 Task 2 — agent_loop 主循环骨架测试.

FakeLLM 注入 (不走真 LLM, 编排纯逻辑测试), 真实 asyncio event loop.
验证:
- 无 tool_calls 直接给最终答案 → return AgentResult(final_answer=..., iterations=1)
- 多轮 tool_calls: LLM → tool_result 回喂 → LLM → final answer
- 并发执行 tool_calls (asyncio.gather 上限 max_tool_concurrency)
- 死循环检测 (window=3 同名+同参 tool_call → break + 升级 clarify 事件)
- max_iterations 兜底 (超限强制结束, final_answer 带"迭代上限"提示)
- user_correction_queue.put({"correction_type": "abort"}) → CancelledError
- user_correction_queue.put({"correction_type": "redirect"}) → 注入 messages
- asyncio.CancelledError 传播 → 清理 _active_agent_workers 注册表
- sse_emit 按设计推送 tool_use / tool_result / text 事件
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from app.engine.agent_loop import (
    AgentResult,
    _active_agent_workers,
    run_agent_loop,
)
from app.engine.llm import ToolCall, ToolUseResponse

# ── FakeLLM: 按脚本返回预设 ToolUseResponse 序列 ──

@dataclass
class FakeLLM:
    """脚本化 LLM, 支持捕获 messages 以验证 tool_result 回喂形态."""
    responses: list[ToolUseResponse]
    calls: list[list[dict]] = field(default_factory=list)

    async def __call__(self, messages, tools, stream_callback=None, **_kw):
        self.calls.append([dict(m) for m in messages])
        await asyncio.sleep(0)  # 让出, 允许外部 task 调度
        if not self.responses:
            return ToolUseResponse(text="(no script left)", tool_calls=[],
                                    stop_reason="end_turn", usage={})
        return self.responses.pop(0)


# ── FakeTools: 按 name → async callable ──

def _make_tool(name: str, out: object):
    async def _tool(**kwargs):
        return out
    _tool.__name__ = name
    return _tool


# ── Helpers ──

def _tuc(text: str = "", calls: list[ToolCall] | None = None,
         stop: str = "end_turn") -> ToolUseResponse:
    return ToolUseResponse(text=text, tool_calls=calls or [],
                            stop_reason=stop, usage={"input_tokens": 1, "output_tokens": 1})


def _sse_sink() -> tuple[list, object]:
    events: list[dict] = []
    async def _emit(evt):
        events.append(evt)
    return events, _emit


# ════════════════════════════════════════════
#  1. 最简路径 — 无 tool_calls 直接终止
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_no_tool_calls_returns_final_answer():
    llm = FakeLLM(responses=[_tuc(text="42", stop="end_turn")])
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t1",
        question="1+1?",
        tools_registry={},
        tool_specs=[],
        sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="test",
    )

    assert isinstance(result, AgentResult)
    assert result.final_answer == "42"
    assert result.iterations == 1
    assert result.stop_reason == "end_turn"
    assert "t1" not in _active_agent_workers  # finally 清理


# ════════════════════════════════════════════
#  2. 多轮 tool_call → 回喂 → 最终答案
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_multi_round_tool_result_feedback():
    """LLM 第一轮调 tool A, 结果喂回, 第二轮给最终答案."""
    llm = FakeLLM(responses=[
        _tuc(calls=[ToolCall(id="c1", name="get_x", input={"k": 1})], stop="tool_use"),
        _tuc(text="result is 10", stop="end_turn"),
    ])
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t2",
        question="fetch x",
        tools_registry={"get_x": _make_tool("get_x", 10)},
        tool_specs=[{"name": "get_x", "input_schema": {}}],
        sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="",
    )
    assert result.final_answer == "result is 10"
    assert result.iterations == 2
    # 第二轮 messages 必须包含 tool_result 回喂
    second_call = llm.calls[1]
    tool_msg = [m for m in second_call if m.get("role") == "tool"
                or (isinstance(m.get("content"), list)
                    and any(isinstance(c, dict) and c.get("type") == "tool_result"
                            for c in m["content"]))]
    assert tool_msg, "tool_result 必须回喂 LLM"


# ════════════════════════════════════════════
#  3. 并发执行 tool_calls (多 tool_call 一轮)
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_concurrent_tool_execution():
    """一轮返 2 tool_calls, 应并发执行 (asyncio.gather)."""
    exec_order: list[str] = []

    async def slow_a(**_):
        await asyncio.sleep(0.05)
        exec_order.append("a")
        return "A"

    async def slow_b(**_):
        await asyncio.sleep(0.01)
        exec_order.append("b")
        return "B"

    llm = FakeLLM(responses=[
        _tuc(calls=[ToolCall(id="c1", name="a", input={}),
                    ToolCall(id="c2", name="b", input={})], stop="tool_use"),
        _tuc(text="done", stop="end_turn"),
    ])
    events, emit = _sse_sink()

    await run_agent_loop(
        trace_id="t3",
        question="q",
        tools_registry={"a": slow_a, "b": slow_b},
        tool_specs=[],
        sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="",
    )
    # 并发: b(0.01) 应先完成, a(0.05) 后完成
    assert exec_order == ["b", "a"]


# ════════════════════════════════════════════
#  4. 死循环检测 — 连续 N 次同 tool+同参 break
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_dead_loop_detection_breaks_and_escalates():
    llm = FakeLLM(responses=[
        _tuc(calls=[ToolCall(id="c1", name="dup", input={"k": 1})], stop="tool_use"),
        _tuc(calls=[ToolCall(id="c2", name="dup", input={"k": 1})], stop="tool_use"),
        _tuc(calls=[ToolCall(id="c3", name="dup", input={"k": 1})], stop="tool_use"),
        _tuc(text="never reached", stop="end_turn"),
    ])
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t4",
        question="q",
        tools_registry={"dup": _make_tool("dup", "x")},
        tool_specs=[],
        sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="",
    )
    assert result.stop_reason == "dead_loop"
    assert any(e.get("event") == "warning" for e in events)


# ════════════════════════════════════════════
#  5. max_total_iterations 兜底
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_max_total_iterations_forces_break(monkeypatch):
    """总轮次兜底以 cost_exhausted 终止（成本后墙）."""
    from app.engine import agent_loop as al
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 2)

    llm = FakeLLM(responses=[
        _tuc(calls=[ToolCall(id="c1", name="t", input={"k": 1})], stop="tool_use"),
        _tuc(calls=[ToolCall(id="c2", name="t", input={"k": 2})], stop="tool_use"),
        _tuc(calls=[ToolCall(id="c3", name="t", input={"k": 3})], stop="tool_use"),
    ])
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t5",
        question="q",
        tools_registry={"t": _make_tool("t", "ok")},
        tool_specs=[],
        sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="",
    )
    assert result.iterations == 2
    assert result.stop_reason == "cost_exhausted"


# ════════════════════════════════════════════
#  6. user_correction abort → CancelledError
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_user_abort_raises_cancelled():
    q: asyncio.Queue = asyncio.Queue()
    await q.put({"correction_type": "abort"})

    llm = FakeLLM(responses=[_tuc(text="won't get here", stop="end_turn")])
    events, emit = _sse_sink()

    with pytest.raises(asyncio.CancelledError):
        await run_agent_loop(
            trace_id="t6",
            question="q",
            tools_registry={},
            tool_specs=[],
            sse_emit=emit,
            user_correction_queue=q,
            llm=llm,
            system_prompt="",
        )
    assert "t6" not in _active_agent_workers  # finally 清理
    assert any(e.get("event") == "cancelled" for e in events)


# ════════════════════════════════════════════
#  7. user_correction redirect → 注入 messages
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_user_redirect_injects_message():
    q: asyncio.Queue = asyncio.Queue()
    await q.put({"correction_type": "redirect", "instruction": "换个思路"})

    llm = FakeLLM(responses=[
        _tuc(text="done after redirect", stop="end_turn"),
    ])
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t7",
        question="q",
        tools_registry={},
        tool_specs=[],
        sse_emit=emit,
        user_correction_queue=q,
        llm=llm,
        system_prompt="",
    )
    assert result.final_answer == "done after redirect"
    # 首轮 LLM 调用的 messages 必须含 redirect 注入 (iter 头部 drain)
    first_call = llm.calls[0]
    assert any("换个思路" in str(m.get("content", "")) for m in first_call)


# ════════════════════════════════════════════
#  8. 外部 task.cancel() → 资源清理
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_external_cancel_cleans_registry():
    llm_ready = asyncio.Event()

    async def slow_llm(messages, tools, **_kw):
        llm_ready.set()
        await asyncio.sleep(10)  # 挂住
        return _tuc(text="never")

    events, emit = _sse_sink()

    async def runner():
        await run_agent_loop(
            trace_id="t8",
            question="q",
            tools_registry={},
            tool_specs=[],
            sse_emit=emit,
            user_correction_queue=asyncio.Queue(),
            llm=slow_llm,
            system_prompt="",
        )

    task = asyncio.create_task(runner())
    await llm_ready.wait()
    assert "t8" in _active_agent_workers
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "t8" not in _active_agent_workers


# ════════════════════════════════════════════
#  9. SSE emit 事件序列: tool_use + tool_result
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sse_emits_tool_use_and_result():
    llm = FakeLLM(responses=[
        _tuc(calls=[ToolCall(id="c1", name="t", input={"k": 1})], stop="tool_use"),
        _tuc(text="ok", stop="end_turn"),
    ])
    events, emit = _sse_sink()

    await run_agent_loop(
        trace_id="t9",
        question="q",
        tools_registry={"t": _make_tool("t", {"x": 1})},
        tool_specs=[],
        sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="",
    )
    evt_types = [e["event"] for e in events]
    assert "tool_use" in evt_types
    assert "tool_result" in evt_types


# ════════════════════════════════════════════
#  10. Tool 抛错 → 包装为 ToolResult.error 回喂
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tool_error_wrapped_and_fed_back():
    async def boom(**_):
        raise RuntimeError("db unavailable")

    llm = FakeLLM(responses=[
        _tuc(calls=[ToolCall(id="c1", name="boom", input={})], stop="tool_use"),
        _tuc(text="recovered", stop="end_turn"),
    ])
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t10",
        question="q",
        tools_registry={"boom": boom},
        tool_specs=[],
        sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="",
    )
    assert result.final_answer == "recovered"
    # tool_result 事件应标记 error 状态
    result_events = [e for e in events if e["event"] == "tool_result"]
    assert result_events
    assert result_events[0]["data"]["status"] == "error"


# ── Stage 5 G5 修正验证 ──

@pytest.mark.asyncio
async def test_text_event_is_text_delta_not_text(fake_llm_end_turn):
    """agent_loop 推的文本事件必须是 text_delta，不能是 text (G5 fix)."""
    events = []

    async def capture(evt):
        events.append(evt)

    await run_agent_loop(
        trace_id="t-g5",
        question="test",
        tools_registry={},
        tool_specs=[],
        sse_emit=capture,
        user_correction_queue=asyncio.Queue(),
        llm=fake_llm_end_turn,
    )

    assert not any(e["event"] == "text" for e in events), "old 'text' event still present"
    text_evts = [e for e in events if e["event"] == "text_delta"]
    assert text_evts, "no text_delta events emitted"
    assert text_evts[0]["data"]["delta"] == "hello"


@pytest.mark.asyncio
async def test_agent_started_and_finished_emitted(fake_llm_end_turn):
    events = []

    async def capture(evt):
        events.append(evt)

    await run_agent_loop(
        trace_id="t-lifecycle",
        question="test",
        tools_registry={},
        tool_specs=[],
        sse_emit=capture,
        user_correction_queue=asyncio.Queue(),
        llm=fake_llm_end_turn,
    )

    assert any(e["event"] == "agent_started" for e in events)
    finished = [e for e in events if e["event"] == "agent_finished"]
    assert finished, "no agent_finished event"
    assert "total_iterations" in finished[0]["data"]
