"""Stage extractor-completeness Task 1 — 抽取器必须感知 stage 3 多态工具签名.

历史教训: extractor-protocol stage Task 2 写的 helper 看 input.collection /
旧 schema/inspect 工具名, 但 stage 3 把这些工具改名为 fetch_schema /
inspect_values / execute_query, 字段从 collection 改 target.
导致走 stage 3 工具的查询永远不沉淀知识. 本测试守住对齐.
"""
import pytest

from app.api.query import (
    _extract_collections,
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
        {"name": "present_result", "input": {"ref": "c1"}, "output": {}},
        {"name": "clarify_with_user", "input": {"question": "?"}, "output": {}},
    ]
    assert _extract_collections(trace) == []


def test_extract_collections_handles_empty_trace():
    assert _extract_collections([]) == []
    assert _extract_collections(None) == []  # type: ignore[arg-type]


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


