"""Task 3: run_agent_loop 新增 history_messages 参数 — 注入 system 与当前问题之间.

复用 tests/agent_loop/test_agent_loop.py 的 llm= 注入桩范式 (FakeLLM 变体), 不
monkeypatch chat_completion_with_tools (其返回 ToolUseResponse dataclass, 非
dict; 直接走 llm= 注入点更贴合现有测试基础设施, 也规避构造 dataclass 的额外样板).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from app.engine.agent_loop import run_agent_loop
from app.engine.llm import ToolUseResponse


@dataclass
class _CapturingLLM:
    """捕获每次调用收到的完整 messages 列表, 用于断言拼装顺序."""

    calls: list[list[dict]] = field(default_factory=list)

    async def __call__(self, messages, tools, stream_callback=None, **_kw):
        self.calls.append([dict(m) for m in messages])
        return ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={})


async def _sse_noop(_evt):
    pass


@pytest.mark.asyncio
async def test_history_inserted_between_system_and_current():
    llm = _CapturingLLM()

    await run_agent_loop(
        trace_id="t-h-1",
        question="当前问题",
        tools_registry={},
        tool_specs=[],
        sse_emit=_sse_noop,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="SYS",
        history_messages=[
            {"role": "user", "content": "旧问"},
            {"role": "assistant", "content": "旧答"},
        ],
    )

    captured = llm.calls[0]
    roles = [m["role"] for m in captured]
    assert roles[0] == "system"
    assert roles[-1] == "user" and captured[-1]["content"] == "当前问题"
    assert roles[1:3] == ["user", "assistant"]
    assert captured[1]["content"] == "旧问"


@pytest.mark.asyncio
async def test_no_history_unchanged():
    llm = _CapturingLLM()

    await run_agent_loop(
        trace_id="t-h-2",
        question="Q",
        tools_registry={},
        tool_specs=[],
        sse_emit=_sse_noop,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="SYS",
    )

    assert [m["role"] for m in llm.calls[0]] == ["system", "user"]


@pytest.mark.asyncio
async def test_empty_history_equals_none():
    llm = _CapturingLLM()

    await run_agent_loop(
        trace_id="t-h-3",
        question="Q",
        tools_registry={},
        tool_specs=[],
        sse_emit=_sse_noop,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="SYS",
        history_messages=[],
    )

    assert [m["role"] for m in llm.calls[0]] == ["system", "user"]
