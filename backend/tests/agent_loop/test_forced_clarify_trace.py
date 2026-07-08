"""Q3 修复回归: Forced_Clarify 的 clarify_with_user 调用必须落 tool_trace.

现象: trace fc17d25c 中 Forced_Clarify 触发了澄清 (用户答了 pending_id=166),
但聊天页 / trace 提炼页都看不到这次 clarify 调用.
根因: Forced_Clarify 直接调 clarify_fn, 绕过正常 tool 执行路径, 未 tool_trace.append.
渲染器 (AgentTracesPage / compact_tool_call) 早已支持 clarify_with_user, 缺的只是落库.

cd backend && python -m pytest tests/agent_loop/test_forced_clarify_trace.py \
  --timeout=120 --timeout-method=thread
"""
from __future__ import annotations

import asyncio

import pytest

from app.engine import agent_loop as al
from app.engine.agent_loop import run_agent_loop
from app.engine.llm import ToolUseResponse

# 复用主测试文件的 helpers (同包导入); relax_quota fixture 走 conftest 自动发现
from tests.agent_loop.test_forced_clarify import (
    FakeLLM,
    _clarify_capture,
    _err_tool,
    _noop_caps,
    _resp,
    _sse,
    _tc,
)

# ════════════════════════════════════════════
#  Q3: Forced_Clarify 调用必须出现在 tool_trace
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_forced_clarify_recorded_in_tool_trace(relax_quota, monkeypatch):
    """用户回应路径: clarify_with_user 调用落 tool_trace, 含 question + user_answer."""
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    clarify_calls = []
    clarify = _clarify_capture(clarify_calls)
    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"db_type": "mongodb", "database": "d", "q": 1})]),
        _resp([_tc("execute_query", {"db_type": "mongodb", "database": "d", "q": 2})]),
        ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={}),
    ])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert len(clarify_calls) == 1  # Forced_Clarify 触发了一次

    clarify_entries = [c for c in result.tool_trace if c.get("name") == "clarify_with_user"]
    assert len(clarify_entries) == 1, "clarify_with_user 必须落 tool_trace"
    entry = clarify_entries[0]
    assert entry["status"] == "ok"
    assert entry["input"]["question"], "question 非空"
    assert "304" in entry["input"]["reason"], "reason 含错误类标记"
    assert entry["output"]["user_answer"] == "换个思路"
    assert entry["output"]["timeout"] is False
    assert entry["output"]["pending_id"] == 1


@pytest.mark.asyncio
async def test_forced_clarify_timeout_recorded_in_tool_trace(relax_quota, monkeypatch):
    """超时路径也要落 tool_trace, 让提炼页看到澄清发起过但未被回应."""
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    # 真实 clarify_with_user 超时返 user_answer=None (stub 不区分, 本地造一个)
    async def _timeout_clarify(**kw):
        return {"user_answer": None, "timeout": True, "pending_id": 7}
    clarify = _timeout_clarify
    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"db_type": "mongodb", "database": "d", "q": 1})]),
        _resp([_tc("execute_query", {"db_type": "mongodb", "database": "d", "q": 2})]),
    ])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert result.stop_reason == "forced_clarify_timeout"

    clarify_entries = [c for c in result.tool_trace if c.get("name") == "clarify_with_user"]
    assert len(clarify_entries) == 1, "超时路径也必须落 tool_trace"
    assert clarify_entries[0]["output"]["timeout"] is True
    assert clarify_entries[0]["output"]["user_answer"] is None
    assert clarify_entries[0]["output"]["pending_id"] == 7


@pytest.mark.asyncio
async def test_forced_clarify_trace_id_unique(relax_quota, monkeypatch):
    """多次 Forced_Clarify (不同错误类) 各自落独立 tool_trace 项, id 不撞."""
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    monkeypatch.setattr(al.settings, "agent_loop_max_forced_clarify_per_class", 5)
    monkeypatch.setattr(al.settings, "agent_loop_error_class_window_size", 10)
    clarify_calls = []
    clarify = _clarify_capture(clarify_calls)
    # 4 次错误: 2 次 304 触发 clarify1, 2 次 16410 触发 clarify2
    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"q": 1})]),
        _resp([_tc("execute_query", {"q": 2})]),
        _resp([_tc("execute_query", {"q": 3})]),
        _resp([_tc("execute_query", {"q": 4})]),
        ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={}),
    ])
    seq = [304, 304, 16410, 16410]
    idx = {"i": 0}
    async def _alt_tool(**kw):
        code = seq[idx["i"] % len(seq)]
        idx["i"] += 1
        raise _FakeOpFailure(code)
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _alt_tool, "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert len(clarify_calls) == 2
    clarify_entries = [c for c in result.tool_trace if c.get("name") == "clarify_with_user"]
    assert len(clarify_entries) == 2
    ids = [c["id"] for c in clarify_entries]
    assert len(set(ids)) == 2, "多次 clarify 的 tool_trace id 必须唯一"


class _FakeOpFailure(Exception):
    def __init__(self, code):
        super().__init__(f"op failed code={code}")
        self.code = code
