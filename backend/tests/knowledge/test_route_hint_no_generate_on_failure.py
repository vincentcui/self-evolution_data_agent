# ============================================================================
# Phase 7 — _async_extract_after_end_turn 不应触发场景
# ----------------------------------------------------------------------------
# - stop_reason != "end_turn" → skip
# - tool_count < min → skip
# - 无 execution rows → skip
# ============================================================================
import pytest

from app.api.query import _should_extract
from app.config import settings


def _make_fake_result(tool_calls, stop_reason):
    from app.engine.agent_loop import AgentResult
    return AgentResult(
        final_answer="x",
        iterations=1,
        stop_reason=stop_reason,
        tool_trace=tool_calls,
        usage_total={},
    )


@pytest.mark.parametrize("stop_reason", ["max_iterations", "dead_loop", "cancelled"])
def test_no_route_hint_for_non_success_stop(stop_reason):
    fake_trace = [
        {"name": "fetch_schema", "input": {"target": "c_category"},
         "output": {}, "status": "ok"},
        {"name": "fetch_schema", "input": {"target": "c_product"},
         "output": {}, "status": "ok"},
    ]
    fake_result = _make_fake_result(fake_trace, stop_reason)
    should, reason = _should_extract(fake_result, settings)
    assert should is False
    assert "stop_reason" in reason


def test_single_collection_does_not_generate():
    """tool_count < min (默认 5) → skip."""
    fake_trace = [
        {"name": "fetch_schema", "input": {"target": "c_category"},
         "output": {}, "status": "ok"},
    ]
    fake_result = _make_fake_result(fake_trace, "end_turn")
    should, reason = _should_extract(fake_result, settings)
    assert should is False
    assert "tool_count" in reason
