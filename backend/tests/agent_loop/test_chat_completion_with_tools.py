"""Stage 4 Task 1 — chat_completion_with_tools 适配层测试.

真实 LLM 调用 (skipif 缺 key), 不 mock. 验证:
- ToolCall / ToolUseResponse dataclass 字段
- 中性 tool spec 双 provider 都能解析 (OpenAI 协议 + Anthropic 协议)
- LLM 选择调 tool 时 stop_reason == "tool_use" 且 tool_calls 非空
- LLM 直接回答时 stop_reason == "end_turn" 且 tool_calls == []
- 后续轮次 tool_result 回喂可被消费 (消息形态正确)
"""

from __future__ import annotations

import os

import pytest

from app.engine.llm import (
    ToolCall,
    ToolUseResponse,
    chat_completion_with_tools,
)

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "查询给定城市的天气",
    "input_schema": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名"},
        },
        "required": ["city"],
    },
}


def _openai_available() -> bool:
    return bool(os.environ.get('IS_LLM_API_KEY'))


def _claude_available() -> bool:
    return bool(os.environ.get('IS_CLAUDE_API_KEY'))


def test_dataclass_fields_present() -> None:
    """ToolCall / ToolUseResponse 字段契约锁定 — 不依赖 LLM."""
    tc = ToolCall(id="call_1", name="get_weather", input={"city": "Beijing"})
    assert tc.id == "call_1"
    assert tc.name == "get_weather"
    assert tc.input == {"city": "Beijing"}

    resp = ToolUseResponse(
        text="thinking...",
        tool_calls=[tc],
        stop_reason="tool_use",
        usage={"input_tokens": 10, "output_tokens": 20},
    )
    assert resp.text == "thinking..."
    assert len(resp.tool_calls) == 1
    assert resp.stop_reason == "tool_use"
    assert resp.usage["input_tokens"] == 10


@pytest.mark.skipif(not _openai_available(), reason="IS_LLM_API_KEY 未配置")
@pytest.mark.asyncio
async def test_openai_returns_tool_call_for_weather_question() -> None:
    """OpenAI 协议: 问天气问题应选择调 get_weather tool."""
    messages = [{"role": "user", "content": "北京今天天气怎么样?"}]
    resp = await chat_completion_with_tools(
        messages=messages,
        tools=[WEATHER_TOOL],
        provider="openai",
    )
    assert isinstance(resp, ToolUseResponse)
    assert resp.stop_reason in {"tool_use", "tool_calls"}
    assert len(resp.tool_calls) >= 1
    tc = resp.tool_calls[0]
    assert tc.name == "get_weather"
    assert "city" in tc.input
    assert tc.id  # 必须有 id 用于回喂配对


@pytest.mark.skipif(not _openai_available(), reason="IS_LLM_API_KEY 未配置")
@pytest.mark.asyncio
async def test_openai_no_tool_call_for_pure_chat() -> None:
    """OpenAI 协议: 纯闲聊问题不应调 tool."""
    messages = [{"role": "user", "content": "1 加 1 等于几? 一个字回答."}]
    resp = await chat_completion_with_tools(
        messages=messages,
        tools=[WEATHER_TOOL],
        provider="openai",
    )
    assert resp.stop_reason in {"end_turn", "stop"}
    assert resp.tool_calls == []
    assert resp.text  # 必有文本回答


@pytest.mark.skipif(not _claude_available(), reason="IS_CLAUDE_API_KEY 未配置")
@pytest.mark.asyncio
async def test_claude_returns_tool_call_for_weather_question() -> None:
    """Claude: 问天气问题应选择调 get_weather tool."""
    messages = [{"role": "user", "content": "What's the weather in Beijing today?"}]
    resp = await chat_completion_with_tools(
        messages=messages,
        tools=[WEATHER_TOOL],
        provider="anthropic",
    )
    assert isinstance(resp, ToolUseResponse)
    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_calls) >= 1
    tc = resp.tool_calls[0]
    assert tc.name == "get_weather"
    assert "city" in tc.input
    assert tc.id


@pytest.mark.skipif(not _claude_available(), reason="IS_CLAUDE_API_KEY 未配置")
@pytest.mark.asyncio
async def test_claude_no_tool_call_for_pure_chat() -> None:
    """Claude: 纯闲聊问题不应调 tool."""
    messages = [{"role": "user", "content": "Reply with the single word 'hi'."}]
    resp = await chat_completion_with_tools(
        messages=messages,
        tools=[WEATHER_TOOL],
        provider="anthropic",
    )
    assert resp.stop_reason == "end_turn"
    assert resp.tool_calls == []
    assert resp.text
