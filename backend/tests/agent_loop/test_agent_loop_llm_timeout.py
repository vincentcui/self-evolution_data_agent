"""agent_loop LLM 超时降级: 持久化已收集 tool_trace, stop_reason=llm_timeout, 不整链炸."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import anthropic
import openai
import pytest

from app.engine.agent_loop import AgentResult, run_agent_loop
from app.engine.llm import ToolCall


@dataclass
class _TimeoutLLM:
    """可注入任意超时异常的 LLM, 默认 openai.APITimeoutError (复刻 trace 7d330d83)."""
    exc: BaseException = field(
        default_factory=lambda: openai.APITimeoutError(request=asyncio.Future()))
    calls: list = field(default_factory=list)

    async def __call__(self, messages, tools, stream_callback=None, **_kw):
        self.calls.append(messages)
        raise self.exc


def _sse_sink():
    events: list[dict] = []
    async def _emit(evt):
        events.append(evt)
    return events, _emit


# ── 四异常参数化: 覆盖 LLM_TIMEOUT_EXCEPTIONS 全部成员 ──
# anthropic.APITimeoutError 既非 httpx.TimeoutException 也非 openai.APITimeoutError
# 子类, 独立一例锚定 anthropic-protocol namespace 降级路径 (漏则整链炸).
@pytest.mark.asyncio
@pytest.mark.parametrize("exc_maker", [
    lambda: openai.APITimeoutError(request=asyncio.Future()),
    lambda: anthropic.APITimeoutError(request=asyncio.Future()),
    lambda: __import__("httpx").ReadTimeout("mock"),
    lambda: asyncio.TimeoutError(),
])
async def test_llm_timeout_degrades_with_stop_reason(exc_maker):
    """首轮 LLM 超时 (四异常类型) → AgentResult(stop_reason=llm_timeout), 不抛异常."""
    llm = _TimeoutLLM(exc=exc_maker())
    events, emit = _sse_sink()

    result = await run_agent_loop(
        trace_id="t-timeout-1",
        question="复杂 JOIN 查询",
        tools_registry={},
        tool_specs=[],
        sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="test",
    )

    assert isinstance(result, AgentResult)
    assert result.stop_reason == "llm_timeout"
    # iterations=0 锚定: agent_loop.py:238 iteration 从 0 起, while 内 +1 → 首轮 iteration=1,
    # 降级 return iterations=iteration-1=0. 若循环改从 1 计数需同步改此断言.
    assert result.iterations == 0
    # emit agent_finished (非 error), 与 dead_loop / max_total_iterations 同模式
    finished = [e for e in events if e.get("event") == "agent_finished"]
    assert len(finished) == 1
    assert finished[0]["data"]["stop_reason"] == "llm_timeout"
    # 不发 error 事件 (避契约破裂 + 双终态)
    assert not any(e.get("event") == "error" for e in events)


@pytest.mark.asyncio
async def test_llm_timeout_after_tools_preserves_tool_trace():
    """跑过 N 轮工具后 LLM 超时 → tool_trace 保留已收集结果.

    脚本: iter1 lookup_knowledge (tool_call) → iter2 LLM 超时.
    """
    from tests.agent_loop.test_agent_loop import _make_tool, _tuc
    from tests.agent_loop.test_agent_loop import _sse_sink as _sink

    tool_call = ToolCall(id="tc_1", name="lookup_knowledge", input={"query": "order"})
    ok_response = _tuc(text="", calls=[tool_call], stop="tool_use")
    # _ScriptedLLM 返回 1 个 tool_call 后, 下次调用抛超时 — 用包装器串起来
    @dataclass
    class _ScriptedLLM:
        responses: list
        calls: list = field(default_factory=list)
        async def __call__(self, messages, tools, stream_callback=None, **_kw):
            self.calls.append(messages)
            if not self.responses:
                raise openai.APITimeoutError(request=asyncio.Future())
            return self.responses.pop(0)

    llm = _ScriptedLLM(responses=[ok_response])
    events, emit = _sink()

    result = await run_agent_loop(
        trace_id="t-timeout-2",
        question="查订单状态",
        tools_registry={"lookup_knowledge": _make_tool("lookup_knowledge", {"hits": []})},
        tool_specs=[],
        sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="test",
    )

    assert result.stop_reason == "llm_timeout"
    assert len(result.tool_trace) == 1                    # iter1 的 tool 结果保留
    assert result.tool_trace[0]["name"] == "lookup_knowledge"
    assert result.iterations == 1                         # 完成 1 轮, 第 2 轮超时


@pytest.mark.asyncio
async def test_llm_timeout_does_not_swallow_non_timeout_exception():
    """非超时异常 (如 ValueError) 不被降级捕获, 仍冒泡 (走顶层 except Exception → failed)."""
    @dataclass
    class _ValueErrorLLM:
        async def __call__(self, messages, tools, stream_callback=None, **_kw):
            raise ValueError("not a timeout")

    events, emit = _sse_sink()
    with pytest.raises(ValueError):
        await run_agent_loop(
            trace_id="t-timeout-3",
            question="x",
            tools_registry={},
            tool_specs=[],
            sse_emit=emit,
            user_correction_queue=asyncio.Queue(),
            llm=_ValueErrorLLM(),
            system_prompt="test",
        )
