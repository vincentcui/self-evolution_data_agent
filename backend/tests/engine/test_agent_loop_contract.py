"""_exec_or_reject 返回 shape 契约: output 字段始终是 dict (对齐 _exec_tool 不变量)."""
from __future__ import annotations

import asyncio

import pytest

from app.engine.agent_loop import _exec_or_reject
from app.engine.llm import ToolCall


@pytest.mark.asyncio
async def test_parse_error_branch_returns_output_dict():
    """parse_error 分支必须返回 output 键 + dict, 与 _exec_tool error 分支同构."""
    tc = ToolCall(
        id="tc_bad",
        name="save_knowledge",
        input={},
        parse_error="truncated JSON: ...supplementary_",
    )
    res = await _exec_or_reject(tc, {}, asyncio.Semaphore(1))
    assert res["status"] == "error"
    # 不变量: output 始终是 dict
    assert isinstance(res["output"], dict)
    assert res["output"]["error_type"] == "JSON_PARSE_FAILED"
    assert res["output"]["error_message"] == "truncated JSON: ...supplementary_"
    # 顶层 message 键已废弃 (无调用方消费)
    assert "message" not in res


@pytest.mark.asyncio
async def test_parse_error_branch_output_safe_for_summarize():
    """385 行 _summarize(res['output']) 不再 KeyError: output 是 dict 可安全取."""
    tc = ToolCall(id="tc_bad2", name="execute_query", input={}, parse_error="bad")
    res = await _exec_or_reject(tc, {}, asyncio.Semaphore(1))
    # 模拟 385 行的取值行为
    _output_for_log = res.get("output", {})
    assert _output_for_log == res["output"]  # .get 与直取结果一致
