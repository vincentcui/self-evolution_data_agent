"""Stage 2 — recall_window + trace_compression 识别 present_result."""
from __future__ import annotations


def test_recall_window_treats_present_result_as_adopting():
    from app.engine.recall_window import ADOPTING_TOOLS
    assert "present_result" in ADOPTING_TOOLS
    # 钉死兼容不变量: recommend_chart 旧名保留 (旧 trace 重放), 不得被误删
    assert "recommend_chart" in ADOPTING_TOOLS


def test_trace_compression_extracts_chart_type_from_chart_spec():
    from app.knowledge.trace_compression import compact_tool_call
    # chart_spec 在 input (tool schema 定义), 不在 output — 这是正确行为
    rec = compact_tool_call(0, {
        "name": "present_result",
        "input": {"ref": "c1", "chart_spec": {"chart_type": "line", "x": "day"}},
        "output": {"status": "ok"},
    })
    assert rec.get("chart_type") == "line"
    assert rec.get("category_column") == "day"
