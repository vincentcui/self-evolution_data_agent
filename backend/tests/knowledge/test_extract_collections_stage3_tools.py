"""Stage extractor-completeness Task 1 — 抽取器必须感知 stage 3 多态工具签名.

历史教训: extractor-protocol stage Task 2 写的 helper 看 input.collection /
fetch_collection_schema / inspect_field_values, 但 stage 3 把这些工具改名为
fetch_schema / inspect_values / execute_query, 字段从 collection 改 target.
导致走 stage 3 工具的查询永远不沉淀知识. 本测试守住对齐.
"""
import pytest

from app.api.query import (
    _derive_cost_strategy,
    _extract_collections,
    _extract_field_mappings,
    _extract_final_pipeline,
)


# ── _extract_collections: stage 3 4 件套 + execute_plan ──

def test_extract_collections_from_fetch_schema():
    trace = [
        {"name": "fetch_schema", "input": {"target": "c_product", "db_type": "mongodb"}, "output": {}},
        {"name": "fetch_schema", "input": {"target": "c_category_group", "db_type": "mongodb"}, "output": {}},
    ]
    assert _extract_collections(trace) == ["c_product", "c_category_group"]


def test_extract_collections_from_inspect_values():
    trace = [
        {"name": "inspect_values", "input": {"target": "c_sku", "field": "itemType"}, "output": {}},
    ]
    assert _extract_collections(trace) == ["c_sku"]


def test_extract_collections_from_execute_query():
    trace = [
        {"name": "execute_query", "input": {"target": "c_product", "mode": "single"}, "output": {"rows": []}},
        {"name": "execute_query", "input": {"target": "c_sku", "mode": "count"}, "output": {"count": 0}},
    ]
    assert _extract_collections(trace) == ["c_product", "c_sku"]


def test_extract_collections_from_estimate_cost():
    trace = [
        {"name": "estimate_cost", "input": {"target": "c_product"}, "output": {}},
    ]
    assert _extract_collections(trace) == ["c_product"]


def test_extract_collections_dedupe_preserve_order():
    trace = [
        {"name": "fetch_schema", "input": {"target": "c_product"}, "output": {}},
        {"name": "execute_query", "input": {"target": "c_product", "mode": "single"}, "output": {}},
        {"name": "fetch_schema", "input": {"target": "c_category_group"}, "output": {}},
    ]
    assert _extract_collections(trace) == ["c_product", "c_category_group"]


def test_extract_collections_skips_non_data_tools():
    trace = [
        {"name": "lookup_knowledge", "input": {"query": "商品"}, "output": {}},
        {"name": "save_knowledge", "input": {"content": "x"}, "output": {}},
        {"name": "recommend_chart", "input": {"rows": []}, "output": {}},
        {"name": "clarify_with_user", "input": {"question": "?"}, "output": {}},
    ]
    assert _extract_collections(trace) == []


def test_extract_collections_handles_empty_trace():
    assert _extract_collections([]) == []
    assert _extract_collections(None) == []  # type: ignore[arg-type]


# ── _extract_field_mappings: 仅 PROBE_TOOLS (fetch_schema / inspect_values) ──

def test_field_mappings_from_inspect_values():
    trace = [
        {"name": "inspect_values", "input": {"target": "c_sku", "field": "itemType"}, "output": {}},
    ]
    assert _extract_field_mappings(trace) == [
        {"collection": "c_sku", "field": "itemType"},
    ]


def test_field_mappings_from_fetch_schema_no_field():
    trace = [
        {"name": "fetch_schema", "input": {"target": "c_product"}, "output": {}},
    ]
    assert _extract_field_mappings(trace) == [
        {"collection": "c_product", "field": ""},
    ]


def test_field_mappings_excludes_execute_query():
    """execute_query 不算 'real probe', 只是 'used result' — 不写入 field_mappings."""
    trace = [
        {"name": "execute_query", "input": {"target": "c_product", "mode": "single"}, "output": {}},
    ]
    assert _extract_field_mappings(trace) == []


def test_field_mappings_dedupe_collection_field_pair():
    trace = [
        {"name": "fetch_schema", "input": {"target": "c_product"}, "output": {}},
        {"name": "fetch_schema", "input": {"target": "c_product"}, "output": {}},  # 重复 schema 探查
        {"name": "inspect_values", "input": {"target": "c_product", "field": "name"}, "output": {}},
        {"name": "inspect_values", "input": {"target": "c_product", "field": "name"}, "output": {}},  # 重复 field 探查
    ]
    assert _extract_field_mappings(trace) == [
        {"collection": "c_product", "field": ""},
        {"collection": "c_product", "field": "name"},
    ]


# ── _extract_final_pipeline: 仅 execute_plan, 不再处理 execute_batched_aggregate ──

def test_final_pipeline_from_execute_plan_steps():
    trace = [
        {"name": "fetch_schema", "input": {"target": "c_product"}, "output": {}},
        {"name": "execute_plan", "input": {"plan": {"steps": [
            {"step_idx": 1, "collection": "c_product", "operation": "aggregate",
             "pipeline": [{"$match": {"deleted": False}}]},
        ]}}, "output": {"rows": [{"x": 1}]}},
    ]
    assert _extract_final_pipeline(trace) == {
        "type": "execute_plan",
        "steps": [{"step_idx": 1, "collection": "c_product", "operation": "aggregate",
                   "pipeline": [{"$match": {"deleted": False}}]}],
    }


def test_final_pipeline_none_when_only_execute_query():
    """走 execute_query 路径仍返 None — _extract_final_pipeline wrapper 未改."""
    trace = [
        {"name": "execute_query", "input": {"target": "c_product", "mode": "single"}, "output": {}},
    ]
    assert _extract_final_pipeline(trace) is None  # ← still passes, wrapper untouched


def test_normalize_query_plan_from_execute_query_returns_plan():
    """Phase 2: normalize_query_plan (new) returns valid plan from execute_query trace."""
    from app.knowledge.trace_extractor import normalize_query_plan
    trace = [
        {"name": "execute_query", "input": {
            "db_type": "mysql", "database": "db", "target": "t",
            "query": {"sql": "SELECT 1"},
        }},
    ]
    result = normalize_query_plan(trace)
    assert result is not None
    assert len(result["steps"]) == 1


def test_final_pipeline_takes_last_execute_plan():
    trace = [
        {"name": "execute_plan", "input": {"plan": {"steps": [{"step_idx": 1}]}}, "output": {}},
        {"name": "execute_plan", "input": {"plan": {"steps": [{"step_idx": 2}]}}, "output": {}},
    ]
    assert _extract_final_pipeline(trace) == {
        "type": "execute_plan",
        "steps": [{"step_idx": 2}],
    }


# ── _derive_cost_strategy: 适配 execute_query mode 维度 ──

def test_cost_strategy_default_when_no_special_mode():
    assert _derive_cost_strategy([
        {"name": "execute_query", "input": {"mode": "single"}, "output": {}},
    ]) == "default"


def test_cost_strategy_count_only_first():
    assert _derive_cost_strategy([
        {"name": "execute_query", "input": {"mode": "count"}, "output": {}},
        {"name": "execute_query", "input": {"mode": "single"}, "output": {}},
    ]) == "count_only_first"


def test_cost_strategy_batched_count_only():
    assert _derive_cost_strategy([
        {"name": "execute_query", "input": {"mode": "count"}, "output": {}},
        {"name": "execute_query", "input": {"mode": "batched"}, "output": {}},
    ]) == "batched_count_only"


def test_cost_strategy_batched_wins_over_count():
    """同 trace 含 batched + count → batched 优先."""
    assert _derive_cost_strategy([
        {"name": "execute_query", "input": {"mode": "batched"}, "output": {}},
        {"name": "execute_query", "input": {"mode": "count"}, "output": {}},
    ]) == "batched_count_only"


def test_cost_strategy_ignores_non_execute_query_tools():
    assert _derive_cost_strategy([
        {"name": "fetch_schema", "input": {"target": "c_product"}, "output": {}},
        {"name": "inspect_values", "input": {"target": "c_product", "field": "x"}, "output": {}},
    ]) == "default"
