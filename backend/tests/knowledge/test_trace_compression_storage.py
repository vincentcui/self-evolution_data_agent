"""compact_tool_trace_for_storage: 落库前结构裁剪, 保证产出合法 JSON."""
from __future__ import annotations

import copy
import json

from app.knowledge.trace_compression import compact_tool_trace_for_storage


def _execute_query_call(row_count: int) -> dict:
    """构造 execute_query tool_call, rows 含 row_count 行."""
    return {
        "id": "tc_exec",
        "name": "execute_query",
        "status": "ok",
        "input": {"target": "orders", "query": {"sql": "SELECT * FROM orders"}, "mode": "single"},
        "output": {
            "rows": [{"id": i, "status": "paid"} for i in range(row_count)],
            "row_count": row_count,
            "truncated": False,
            "columns": ["id", "status"],
            "timezone": "Asia/Shanghai",
        },
    }


def _inspect_values_call(value_count: int) -> dict:
    return {
        "id": "tc_inspect",
        "name": "inspect_values",
        "status": "ok",
        "input": {"target": "orders", "field": "status"},
        "output": {
            "values": [f"v{i}" for i in range(value_count)],
            "field": "status",
            "target": "orders",
        },
    }


def _lookup_knowledge_call(hit_count: int) -> dict:
    return {
        "id": "tc_lookup",
        "name": "lookup_knowledge",
        "status": "ok",
        "input": {"query": "order status", "types": ["example"]},
        "output": [
            {"content": f"example {i}", "entry_type": "example", "status": "canonical",
             "distance": 0.1 * i, "tier": "normal", "payload": {"k": i}}
            for i in range(hit_count)
        ],
    }


def test_under_budget_returns_original_untouched():
    """小 trace 不触发裁剪, 返回原 list (同一对象)."""
    trace = [_execute_query_call(3)]
    out = compact_tool_trace_for_storage(trace, max_bytes=200_000, row_cap=20)
    assert out is trace  # 零拷贝快路径


def test_execute_query_rows_truncated_with_marker():
    """超预算 → execute_query.rows 截到 row_cap, 加 trace_rows_truncated 标记."""
    trace = [_execute_query_call(464)]
    out = compact_tool_trace_for_storage(trace, max_bytes=200, row_cap=20)
    call = out[0]
    assert len(call["output"]["rows"]) == 20
    assert call["output"]["row_count"] == 464          # 原始总行数保留
    assert call["output"]["truncated"] is False         # driver 语义不动
    assert call["output"]["trace_rows_truncated"] is True
    assert call["output"]["trace_rows_kept"] == 20
    assert call["input"]["target"] == "orders"          # input 不动


def test_inspect_values_truncated_with_marker():
    trace = [_inspect_values_call(200)]
    out = compact_tool_trace_for_storage(trace, max_bytes=200, row_cap=20)
    call = out[0]
    assert len(call["output"]["values"]) == 20
    assert call["output"]["trace_values_truncated"] is True
    assert call["output"]["trace_values_kept"] == 20
    assert call["output"]["trace_values_total"] == 200


def test_lookup_knowledge_truncated():
    trace = [_lookup_knowledge_call(60)]
    out = compact_tool_trace_for_storage(trace, max_bytes=200, row_cap=20)
    call = out[0]
    assert len(call["output"]) == 20                    # output 本身是 list
    assert call["output"][0]["content"] == "example 0"  # 前 20 条原样


def test_does_not_mutate_input():
    """裁剪不能 mutate 原 tool_trace (live loop 已 emit)."""
    trace = [_execute_query_call(464)]
    original = copy.deepcopy(trace)
    compact_tool_trace_for_storage(trace, max_bytes=200, row_cap=20)
    assert trace == original                            # 原始未变


def test_error_output_not_truncated():
    """error 分支 output 无 rows 键, 不裁."""
    trace = [{
        "id": "tc_err", "name": "execute_query", "status": "error",
        "input": {}, "output": {"error_type": "DriverError", "error_message": "boom"},
    }]
    out = compact_tool_trace_for_storage(trace, max_bytes=200, row_cap=20)
    assert out[0]["output"] == {"error_type": "DriverError", "error_message": "boom"}


def test_mongo_count_one_row_not_truncated():
    """mongo count mode rows=[{"count":N}] 只 1 行, 不裁."""
    trace = [{
        "id": "tc_count", "name": "execute_query", "status": "ok",
        "input": {}, "output": {"rows": [{"count": 42}], "row_count": 1},
    }]
    out = compact_tool_trace_for_storage(trace, max_bytes=200, row_cap=20)
    assert out[0]["output"]["rows"] == [{"count": 42}]  # 1 行不触发


def test_other_tools_preserved():
    """fetch_schema 等非三类工具原样保留, 即使大表."""
    trace = [{
        "id": "tc_schema", "name": "fetch_schema", "status": "ok",
        "input": {}, "output": {"fields": [{"name": f"f{i}"} for i in range(100)]},
    }]
    out = compact_tool_trace_for_storage(trace, max_bytes=200, row_cap=20)
    assert len(out[0]["output"]["fields"]) == 100       # 不裁


def test_output_always_valid_json_after_compact():
    """裁后产出可 json.dumps 出合法 JSON (不破坏结构)."""
    trace = [_execute_query_call(464), _inspect_values_call(200), _lookup_knowledge_call(60)]
    out = compact_tool_trace_for_storage(trace, max_bytes=200, row_cap=20)
    # 必须不抛异常
    s = json.dumps({"tool_trace": out}, ensure_ascii=False, default=str)
    assert json.loads(s)  # round-trip 成功
