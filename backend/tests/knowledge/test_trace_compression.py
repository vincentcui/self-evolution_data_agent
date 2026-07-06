"""compact_tool_call 非字典元素容错源头单测.

覆盖 trace_compression.compact_tool_call 的 "不会抛异常" docstring 契约.
两条生产调用方 (summarize_trace_for_llm←refiner + get_trace_detail) 都吃
持久化 trace_json.tool_trace, 同源非字典风险, 源头测一次覆盖两者.
"""
from app.knowledge.trace_compression import compact_tool_call


def test_compact_tool_call_non_dict_returns_empty_row():
    """非字典元素 (None / str / int) → {"step": idx, "tool": ""}, 不抛."""
    assert compact_tool_call(0, None) == {"step": 0, "tool": ""}  # type: ignore[arg-type]
    assert compact_tool_call(1, "bad") == {"step": 1, "tool": ""}  # type: ignore[arg-type]
    assert compact_tool_call(2, 42) == {"step": 2, "tool": ""}    # type: ignore[arg-type]


def test_compact_tool_call_dict_unchanged():
    """良构 dict 不受守卫影响 (回归保护)."""
    rec = compact_tool_call(0, {"name": "fetch_schema", "input": {"target": "c_orders"}, "output": {}})
    assert rec["step"] == 0
    assert rec["tool"] == "fetch_schema"
    assert rec["target"] == "c_orders"


def test_compact_list_databases():
    rec = compact_tool_call(0, {"name": "list_databases", "input": {},
        "output": {"databases": [{"database": "db1"}, {"database": "db2"}], "count": 2}})
    assert rec["tool"] == "list_databases"
    assert rec["db_count"] == 2


def test_compact_list_tables():
    rec = compact_tool_call(0, {"name": "list_tables",
        "input": {"database": "shop_db"},
        "output": {"tables": [{"target": "orders"}, {"target": "items"}], "count": 2, "status": "ok"}})
    assert rec["database"] == "shop_db"
    assert rec["table_count"] == 2
    assert rec["status"] == "ok"


def test_compact_generate_query_plan():
    rec = compact_tool_call(0, {"name": "generate_query_plan",
        "input": {"collections": [
            {"db_type": "mysql", "database": "shop", "collection": "orders"},
            {"db_type": "mongodb", "database": "cat", "collection": "products"}]},
        "output": {"plan": {"strategy": "hash_join", "steps": [{"step_idx": 1}, {"step_idx": 2}]}}})
    assert rec["plan_collections"] == ["orders", "products"]
    assert rec["plan_strategy"] == "hash_join"
    assert rec["plan_step_count"] == 2


def test_compact_execute_plan_extracts_output():
    rec = compact_tool_call(0, {"name": "execute_plan",
        "input": {"plan": {"steps": [{"collection": "orders"}, {"collection": "items"}]}},
        "output": {"rows": [1, 2, 3], "total_row_count": 3, "truncated": False}})
    assert rec["plan_step_count"] == 2
    assert rec["plan_collections"] == ["orders", "items"]
    assert rec["rows_returned"] == 3  # 从 output.total_row_count
    assert "truncated" not in rec


def test_compact_execute_plan_truncated():
    rec = compact_tool_call(0, {"name": "execute_plan",
        "input": {"plan": {"steps": [{"collection": "orders"}]}},
        "output": {"rows": [1, 2], "total_row_count": 5000, "truncated": True}})
    assert rec["rows_returned"] == 5000
    assert rec["truncated"] is True


def test_compact_present_result_reads_chart_spec_from_input():
    """修预存 bug: chart_spec 在 input, 不在 output."""
    rec = compact_tool_call(0, {"name": "present_result",
        "input": {"ref": "tc_1", "chart_spec": {"chart_type": "bar", "x": "month"}},
        "output": {}})
    assert rec["chart_type"] == "bar"
    assert rec["category_column"] == "month"


def test_compact_recommend_chart_branch_removed():
    """recommend_chart 死分支已删 — 走 default, 仅 step+tool, 不产 chart_type."""
    rec = compact_tool_call(0, {"name": "recommend_chart",
        "input": {"category_column": "x"}, "output": {"chart_type": "bar"}})
    assert rec["tool"] == "recommend_chart"
    assert "chart_type" not in rec  # 分支已删, 不再误抽


def test_compact_estimate_cost_reads_warning_level():
    """CostEstimate 返 warning_level 字符串 (非 blocked 布尔键) — blocked 由 warning_level=='blocked' 推导."""
    rec = compact_tool_call(0, {"name": "estimate_cost",
        "input": {"db_type": "mysql", "database": "shop", "target": "orders"},
        "output": {"estimated_rows": 5000, "warning_level": "blocked"}})
    assert rec["est_rows"] == 5000
    assert rec["warning_level"] == "blocked"
    assert rec["blocked"] is True


def test_compact_estimate_cost_warning_ok_not_blocked():
    rec = compact_tool_call(0, {"name": "estimate_cost",
        "input": {}, "output": {"estimated_rows": 10, "warning_level": "ok"}})
    assert rec["blocked"] is False
    assert rec["warning_level"] == "ok"


def test_compact_clarify_reads_user_answer():
    """clarify_with_user 工具返 {user_answer, ...} (非 answer) — 修预存字段名 bug."""
    rec = compact_tool_call(0, {"name": "clarify_with_user",
        "input": {"question": "用哪个表?"},
        "output": {"user_answer": "用订单表", "timeout": False, "pending_id": "p1"}})
    assert rec["question"] == "用哪个表?"
    assert rec["user_answer"] == "用订单表"


def test_compact_execute_query_truncated_captures_output_error():
    """execute_query 截断返 {error, message, result_ref} 无 rows/count —
    error 在 output 内非顶层, compact 须捕获 message 进 rec['error'],
    否则前端返回值列显 '—' (真实事故 trace 3ff15f81)."""
    rec = compact_tool_call(3, {"name": "execute_query",
        "input": {"mode": "single", "target": "orders", "db_type": "mysql", "database": "shop", "query": {}},
        "output": {"error": "result_truncated_use_plan",
                   "message": "查询已返回约 1000 行, 超过单次上限 1000 行, 结果已被截断.",
                   "suggestion": "...", "result_ref": "tc_3"}})
    assert rec["tool"] == "execute_query"
    assert "rows_returned" not in rec  # 无 rows
    assert "count_returned" not in rec
    assert rec["error"].startswith("查询已返回约 1000 行")  # 取 output.message (可读) 非 error code
    assert "result_truncated_use_plan" not in rec["error"]


def test_compact_success_output_no_error_not_misflagged():
    """成功 output (无 error 键) 不应被误判为 error — message 仅在 output.error 存在时才取."""
    rec = compact_tool_call(0, {"name": "execute_query",
        "input": {"mode": "count", "target": "orders"},
        "output": {"count": 7, "result_ref": "tc_0"}})
    assert "error" not in rec
    assert rec["count_returned"] == 7
