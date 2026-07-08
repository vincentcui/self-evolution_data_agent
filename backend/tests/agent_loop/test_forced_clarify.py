"""Forced_Clarify 主循环集成测试 (Properties 18-29, 31; tasks 13.2/13.3/15.x/18.1).

FakeLLM + 假工具 + mock clarify, 不走真 LLM/DB.
cd backend && python -m pytest tests/agent_loop/test_forced_clarify.py --timeout=120 --timeout-method=thread
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from app.engine import agent_loop as al
from app.engine.agent_loop import AgentResult, run_agent_loop
from app.engine.llm import ToolCall, ToolUseResponse


@dataclass
class FakeLLM:
    responses: list[ToolUseResponse]
    calls: list = field(default_factory=list)

    async def __call__(self, messages, tools, stream_callback=None, **_kw):
        self.calls.append(len(messages))
        await asyncio.sleep(0)
        if not self.responses:
            return ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={})
        return self.responses.pop(0)


def _tc(name: str, inp: dict, cid: str = "c1") -> ToolCall:
    return ToolCall(id=cid, name=name, input=inp)


def _resp(calls: list[ToolCall]) -> ToolUseResponse:
    return ToolUseResponse(text="", tool_calls=calls, stop_reason="tool_use",
                           usage={"input_tokens": 1, "output_tokens": 1})


def _sse():
    events = []
    async def emit(e):
        events.append(e)
    return events, emit


def _err_tool(code: int):
    async def _t(**kw):
        # 模拟 _exec_tool 包装后的错误形态 (status=error, 数值码)
        raise _FakeOpFailure(code)
    return _t


class _FakeOpFailure(Exception):
    def __init__(self, code):
        super().__init__(f"op failed code={code}")
        self.code = code


def _clarify_stub(answer="换个思路", timeout=False):
    async def _clarify(**kw):
        return {"user_answer": answer, "timeout": timeout, "pending_id": 1}
    return _clarify


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 18: 达阈值必触发 Forced_Clarify
@pytest.mark.asyncio
async def test_property_18_threshold_triggers_clarify(relax_quota, monkeypatch):
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    clarify_calls = []
    clarify = _clarify_capture(clarify_calls)
    # 2 次同 execute_query 错误 → 第 2 次尾部触发 clarify → 注入回应 → 第 3 次 LLM 结束
    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"db_type": "mongodb", "database": "d", "q": 1})]),
        _resp([_tc("execute_query", {"db_type": "mongodb", "database": "d", "q": 2})]),
        ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={}),
    ])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(),
        llm=llm,
    )
    assert len(clarify_calls) == 1  # 暂停而非终止
    assert result.stop_reason == "end_turn"


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 19: 用户回应后重置该错误类计数
@pytest.mark.asyncio
async def test_property_19_reset_after_answer(relax_quota, monkeypatch):
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    monkeypatch.setattr(al.settings, "agent_loop_max_forced_clarify_per_class", 5)
    clarify_calls = []
    clarify = _clarify_capture(clarify_calls)
    # 4 次错误: clarify 在第2次触发并重置, 第3/4次再累积到阈值 → 第二次 clarify
    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"q": i})]) for i in range(4)
    ] + [ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={})])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert len(clarify_calls) == 2  # 重置后重新累积触发第二次


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 20: 各类均未达阈值则不触发
@pytest.mark.asyncio
async def test_property_20_alternating_no_trigger(relax_quota, monkeypatch):
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    clarify_calls = []
    clarify = _clarify_capture(clarify_calls)
    # 交替 304 / 16410, window=5, threshold=2: 末尾窗口内每类最多 ... 需保证 <2
    # A B A B A → A=3? 用更短: A B (各1) 然后结束
    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"q": 1}, "a")]),
        _resp([_tc("execute_query", {"q": 2}, "b")]),
        ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={}),
    ])

    # 两次不同错误码
    seq = [304, 16410]
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
    assert len(clarify_calls) == 0
    assert result.stop_reason == "end_turn"


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 21: 保留 dead_loop 终止语义
@pytest.mark.asyncio
async def test_property_21_dead_loop_preserved(relax_quota, monkeypatch):
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    # 相同 tool+input 且结果非错误 (成功) → dead_loop, 不触发 clarify
    async def _ok_tool(**kw):
        return {"rows": [], "row_count": 0}
    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"q": 1}, "same")]) for _ in range(5)
    ])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _ok_tool, "clarify_with_user": _clarify_stub()},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert result.stop_reason == "dead_loop"
    # M4: warning emit 保留
    assert any(e.get("event") == "warning" for e in events)


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 22: Forced_Clarify 结构性优先于死循环
@pytest.mark.asyncio
async def test_property_22_clarify_precedes_dead_loop(relax_quota, monkeypatch):
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    clarify_calls = []
    clarify = _clarify_capture(clarify_calls)
    # 相同 tool+input 且相同错误类: threshold=2 < window=3 → clarify 先触发
    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"q": 1}, "same")]) for _ in range(5)
    ] + [ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={})])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert len(clarify_calls) >= 1
    assert result.stop_reason != "dead_loop"


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 28: 单轮批量相同调用不击穿澄清优先
@pytest.mark.asyncio
async def test_property_28_batch_burst(relax_quota, monkeypatch):
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    clarify_calls = []
    clarify = _clarify_capture(clarify_calls)
    # 单个响应含 3 个 (>=window) 相同 tool_call, 均同错误类
    batch = [_tc("execute_query", {"q": 1}, f"c{i}") for i in range(3)]
    llm = FakeLLM(responses=[
        _resp(batch),
        ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={}),
    ])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert len(clarify_calls) == 1  # 先 clarify
    assert result.stop_reason != "dead_loop"


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 29: caps 解析失败不阻断 Forced_Clarify
@pytest.mark.asyncio
async def test_property_29_caps_none_still_clarifies(relax_quota, monkeypatch):
    async def _none_caps(*a, **k):
        return None
    monkeypatch.setattr(al, "_resolve_caps_for_error", _none_caps)
    clarify_calls = []
    clarify = _clarify_capture(clarify_calls)
    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"q": 1})]),
        _resp([_tc("execute_query", {"q": 2})]),
        ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={}),
    ])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert len(clarify_calls) == 1
    # 退化文案: 不含能力详情
    assert "不支持" not in clarify_calls[0]["question"]


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 31: total 桶为不可绕过硬上限
@pytest.mark.asyncio
async def test_property_31_total_hard_ceiling(monkeypatch):
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 2)
    monkeypatch.setattr(al.settings, "agent_loop_dead_loop_window", 100)
    monkeypatch.setattr(al.settings, "agent_loop_error_class_window_size", 5)
    monkeypatch.setattr(al.settings, "agent_loop_error_class_threshold", 2)
    clarify_calls = []
    clarify = _clarify_capture(clarify_calls)
    llm = FakeLLM(responses=[_resp([_tc("execute_query", {"q": i})]) for i in range(10)])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert result.stop_reason == "cost_exhausted"


# task 15.14 / R5.9: cap=1 第二次同类 → forced_clarify_exhausted
@pytest.mark.asyncio
async def test_forced_clarify_exhausted(relax_quota, monkeypatch):
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    monkeypatch.setattr(al.settings, "agent_loop_max_forced_clarify_per_class", 1)
    clarify = _clarify_stub()
    # 6 次同类错误: 第2次 clarify(count=1), reset, 第3/4次再到阈值 → exhausted
    llm = FakeLLM(responses=[_resp([_tc("execute_query", {"q": i})]) for i in range(6)])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert result.stop_reason == "forced_clarify_exhausted"


# task 15.14 / R5.10: clarify timeout → forced_clarify_timeout
@pytest.mark.asyncio
async def test_forced_clarify_timeout(relax_quota, monkeypatch):
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    async def _abandon(tid):
        return None
    monkeypatch.setattr(al, "_abandon_pending", _abandon)
    clarify = _clarify_stub(timeout=True)
    llm = FakeLLM(responses=[_resp([_tc("execute_query", {"q": i})]) for i in range(3)])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert result.stop_reason == "forced_clarify_timeout"


# ── helpers for caps mock + clarify capture ──
async def _noop_caps(*a, **k):
    return {"flavor": "documentdb", "unsupported_ops": ["$getField"],
            "unsupported_stage_variants": [], "syntax_constraints": [], "equivalent_hints": []}


def _clarify_capture(sink: list):
    async def _clarify(**kw):
        sink.append(kw)
        return {"user_answer": "换个思路", "timeout": False, "pending_id": 1}
    return _clarify


# Property 23: 桶配额降级为软告警，成本后墙终止
@pytest.mark.asyncio
async def test_property_23_quota_soft_limit(monkeypatch):
    """decisive 软上限超后不硬停，继续到 total_iterations 成本后墙."""
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 1)
    # total_iterations 作唯一计数型后墙
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 4)
    monkeypatch.setattr(al.settings, "agent_loop_dead_loop_window", 100)
    monkeypatch.setattr(al.settings, "agent_loop_error_class_window_size", 5)
    monkeypatch.setattr(al.settings, "agent_loop_error_class_threshold", 2)

    async def _ok(**kw):
        return {"rows": [{"x": 1}], "row_count": 1}

    # decisive cap=1，但软上限不硬停；由 total_iterations=4 终止
    llm = FakeLLM(responses=[_resp([_tc("execute_query", {"q": i})]) for i in range(10)])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _ok, "clarify_with_user": _clarify_stub()},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert result.stop_reason == "cost_exhausted"


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 24: 澄清为 interactive 且不计任何配额桶
def test_property_24_clarify_is_interactive():
    from app.engine.tools.classification import classify_tool
    assert classify_tool("clarify_with_user") == "interactive"


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 25: explore/decisive 配额耗尽不禁用澄清
@pytest.mark.asyncio
async def test_property_25_clarify_under_exhausted_decisive(monkeypatch):
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    # decisive 配额极小, 但错误类阈值在执行尾部先触发 clarify (clarify 不入桶)
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 100)
    monkeypatch.setattr(al.settings, "agent_loop_dead_loop_window", 100)
    monkeypatch.setattr(al.settings, "agent_loop_error_class_window_size", 5)
    monkeypatch.setattr(al.settings, "agent_loop_error_class_threshold", 2)
    monkeypatch.setattr(al.settings, "agent_loop_max_forced_clarify_per_class", 5)
    clarify_calls = []
    clarify = _clarify_capture(clarify_calls)
    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"q": 1})]),
        _resp([_tc("execute_query", {"q": 2})]),
        ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={}),
    ])
    events, emit = _sse()
    await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    assert len(clarify_calls) == 1


# task 18.1: 端到端回归 — DocumentDB 三错误类 + 强制澄清先于决策配额耗尽
@pytest.mark.asyncio
async def test_e2e_documentdb_clarify_before_decisive_burn(monkeypatch):
    # 用真实 documentdb caps 解析 (不 mock _resolve_caps_for_error)
    async def _docdb_caps(*a, **k):
        from app.engine.drivers.mongo_flavor import compute_capabilities
        return compute_capabilities("documentdb", "5.0.0")
    monkeypatch.setattr(al, "_resolve_caps_for_error", _docdb_caps)
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 15)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 100)
    monkeypatch.setattr(al.settings, "agent_loop_dead_loop_window", 100)
    monkeypatch.setattr(al.settings, "agent_loop_error_class_window_size", 5)
    monkeypatch.setattr(al.settings, "agent_loop_error_class_threshold", 2)
    clarify_calls = []
    clarify = _clarify_capture(clarify_calls)
    # 反复命中 code 304 (同错误类), 决策配额 15 远未耗尽时第 2 次即 clarify
    llm = FakeLLM(responses=[_resp([_tc("execute_query", {"q": i})]) for i in range(10)]
                  + [ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={})])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="统计 A 级/B 级品牌资源类型占比",
        tools_registry={"execute_query": _err_tool(304), "clarify_with_user": clarify},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    # 第 2 次错误即触发 clarify, 远早于 max_decisive_calls=15
    assert len(clarify_calls) >= 1
    assert result.stop_reason != "max_decisive_calls"
    # 澄清文案含 DocumentDB 能力详情
    assert "documentdb" in clarify_calls[0]["question"].lower() or "$getField" in clarify_calls[0]["question"]


# ── C1: execute_query mode-aware 分桶 ──

@pytest.mark.asyncio
async def test_c1_probe_count_not_decisive(monkeypatch):
    """probe/count 归 exploratory: 大量 probe 不触 max_decisive_calls (改动 C1)."""
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 2)  # 极小
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 100)
    monkeypatch.setattr(al.settings, "agent_loop_dead_loop_window", 100)

    async def _ok(**kw):
        return {"rows": [{"x": 1}], "row_count": 1}

    # 8 次 probe (各不同 input 避免 dead_loop), 末轮 end_turn
    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"mode": "probe", "q": i}, f"c{i}")]) for i in range(8)
    ] + [ToolUseResponse(text="done", tool_calls=[], stop_reason="end_turn", usage={})])
    events, emit = _sse()
    result = await run_agent_loop(
        trace_id="t", question="q",
        tools_registry={"execute_query": _ok, "clarify_with_user": _clarify_stub()},
        tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
    )
    # probe 不入 decisive 桶 → 不触顶, 正常 end_turn
    assert result.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_c1_single_still_decisive(monkeypatch, caplog):
    """single 仍归 decisive 桶：超软上限记 warning，由 total_iterations 终止."""
    import logging
    monkeypatch.setattr(al, "_resolve_caps_for_error", _noop_caps)
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 2)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 4)
    monkeypatch.setattr(al.settings, "agent_loop_dead_loop_window", 100)

    async def _ok(**kw):
        return {"rows": [{"x": 1}], "row_count": 1}

    llm = FakeLLM(responses=[
        _resp([_tc("execute_query", {"mode": "single", "q": i}, f"c{i}")]) for i in range(10)
    ])
    events, emit = _sse()
    with caplog.at_level(logging.WARNING, logger="app.engine.agent_loop"):
        result = await run_agent_loop(
            trace_id="t", question="q",
            tools_registry={"execute_query": _ok, "clarify_with_user": _clarify_stub()},
            tool_specs=[], sse_emit=emit, user_correction_queue=asyncio.Queue(), llm=llm,
        )
    # 软上限：decisive 超限记 warning 不硬停，由 total_iterations 终止
    assert result.stop_reason == "cost_exhausted"
    assert any("决策类工具调用超软上限" in r.message for r in caplog.records)
