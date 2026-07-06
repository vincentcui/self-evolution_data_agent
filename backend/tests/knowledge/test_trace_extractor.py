"""Phase 2 — trace_extractor 单测.

验证 extract_collections / derive_cost_strategy / extract_join_fields / extract_final_pipeline
与 api/query.py 原实现行为一致.
"""

from app.knowledge.trace_extractor import (
    derive_cost_strategy,
    extract_collections,
    extract_final_pipeline,
    extract_join_fields,
    normalize_query_plan,
    extract_join_keys,
)


# ── extract_collections ──

def test_extract_collections_dedupe_preserve_order():
    tool_trace = [
        {"name": "fetch_schema", "input": {"target": "c_a"}},
        {"name": "fetch_schema", "input": {"target": "c_b"}},
        {"name": "fetch_schema", "input": {"target": "c_a"}},  # dup
        {"name": "execute_query", "input": {"target": "c_c"}},
    ]
    assert extract_collections(tool_trace) == ["c_a", "c_b", "c_c"]


def test_extract_collections_skips_non_data_tools():
    tool_trace = [
        {"name": "lookup_knowledge", "input": {"query": "x"}},
        {"name": "save_knowledge", "input": {"content": "y"}},
        {"name": "fetch_schema", "input": {"target": "c_real"}},
    ]
    assert extract_collections(tool_trace) == ["c_real"]


def test_extract_collections_handles_empty():
    assert extract_collections([]) == []


def test_extract_collections_from_estimate_cost():
    tool_trace = [
        {"name": "estimate_cost", "input": {"target": "c_product"}},
    ]
    assert extract_collections(tool_trace) == ["c_product"]


# ── derive_cost_strategy ──

def test_derive_cost_strategy_count_only():
    tool_trace = [
        {"name": "execute_query", "input": {"target": "c_a", "mode": "count"}},
        {"name": "execute_query", "input": {"target": "c_b", "mode": "single"}},
    ]
    assert derive_cost_strategy(tool_trace) == "count_only_first"


def test_derive_cost_strategy_batched():
    tool_trace = [
        {"name": "execute_query", "input": {"target": "c_a", "mode": "batched"}},
    ]
    assert derive_cost_strategy(tool_trace) == "batched_count_only"


def test_derive_cost_strategy_default():
    tool_trace = [
        {"name": "execute_query", "input": {"target": "c_a", "mode": "single"}},
    ]
    assert derive_cost_strategy(tool_trace) == "default"


def test_derive_cost_strategy_no_execute():
    tool_trace = [
        {"name": "fetch_schema", "input": {"target": "c_a"}},
    ]
    assert derive_cost_strategy(tool_trace) == "default"


def test_derive_cost_strategy_batched_wins_over_count():
    tool_trace = [
        {"name": "execute_query", "input": {"mode": "count"}},
        {"name": "execute_query", "input": {"mode": "batched"}},
    ]
    assert derive_cost_strategy(tool_trace) == "batched_count_only"


# ── extract_join_fields ──

def test_extract_join_fields_from_plan():
    final_pipeline = {
        "type": "execute_plan",
        "steps": [
            {"step_idx": 0, "collection": "c_a"},
            {
                "step_idx": 1, "collection": "c_b",
                "pipeline": [
                    {"$lookup": {
                        "from": "c_a", "localField": "a_id", "foreignField": "_id",
                        "as": "joined",
                    }},
                ],
            },
        ],
    }
    out = extract_join_fields(final_pipeline)
    assert {"a": "c_b.a_id", "b": "c_a._id"} in out


def test_extract_join_fields_no_plan():
    assert extract_join_fields(None) == []
    assert extract_join_fields({"type": "other"}) == []


# ── extract_final_pipeline ──

def test_extract_final_pipeline_from_execute_plan():
    trace = [
        {"name": "fetch_schema", "input": {"target": "c_a"}},
        {"name": "execute_plan", "input": {"plan": {"steps": [
            {"step_idx": 1, "collection": "c_a"},
        ]}}, "output": {}},
    ]
    result = extract_final_pipeline(trace)
    assert result == {"type": "execute_plan", "steps": [{"step_idx": 1, "collection": "c_a"}]}


def test_extract_final_pipeline_takes_last():
    trace = [
        {"name": "execute_plan", "input": {"plan": {"steps": [{"step_idx": 1}]}}, "output": {}},
        {"name": "execute_plan", "input": {"plan": {"steps": [{"step_idx": 2}]}}, "output": {}},
    ]
    result = extract_final_pipeline(trace)
    assert result["steps"] == [{"step_idx": 2}]


def test_extract_final_pipeline_none_when_no_plan():
    trace = [
        {"name": "execute_query", "input": {"target": "c_a", "mode": "single"}, "output": {}},
    ]
    assert extract_final_pipeline(trace) is None


# ── normalize_query_plan ──

def test_normalize_query_plan_from_execute_query_mysql():
    trace = [
        {"name": "execute_query", "input": {
            "db_type": "mysql", "database": "shop", "target": "orders",
            "query": {"sql": "SELECT user_id, COUNT(*) FROM orders GROUP BY user_id"},
        }},
    ]
    plan = normalize_query_plan(trace)
    assert plan is not None
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["db_type"] == "mysql"
    assert plan["steps"][0]["operation"] == "sql"


def test_normalize_query_plan_from_execute_query_oracle():
    """Oracle SQL_DB_TYPES member → operation='sql'."""
    trace = [
        {"name": "execute_query", "input": {
            "db_type": "oracle", "database": "SHOP", "target": "ORDERS",
            "query": {"sql": "SELECT user_id, COUNT(*) FROM ORDERS GROUP BY user_id"},
        }},
    ]
    plan = normalize_query_plan(trace)
    assert plan is not None
    assert plan["steps"][0]["db_type"] == "oracle"
    assert plan["steps"][0]["operation"] == "sql"


def test_normalize_query_plan_from_execute_query_mongo():
    trace = [
        {"name": "execute_query", "input": {
            "db_type": "mongodb", "database": "shop", "target": "orders",
            "query": {"pipeline": [{"$group": {"_id": "$status"}}]},
        }},
    ]
    plan = normalize_query_plan(trace)
    assert plan["steps"][0]["db_type"] == "mongodb"
    assert plan["steps"][0]["operation"] == "aggregate"


def test_normalize_query_plan_prioritizes_execute_plan():
    trace = [
        {"name": "execute_query", "input": {"db_type": "mysql", "query": {"sql": "SELECT 1"}}},
        {"name": "execute_plan", "input": {"plan": {
            "steps": [{"db_type": "mysql", "collection": "orders", "query": {"sql": "SELECT * FROM orders"}}]
        }}},
    ]
    plan = normalize_query_plan(trace)
    assert plan["steps"][0]["collection"] == "orders"


def test_normalize_query_plan_returns_none_when_empty():
    assert normalize_query_plan([]) is None
    assert normalize_query_plan([{"name": "fetch_schema", "input": {}}]) is None


# ── normalize_query_plan — negative paths ──

def test_normalize_query_plan_skips_malformed_calls():
    """Calls missing 'name' or 'input' are silently skipped."""
    trace = [
        {},  # no name
        {"name": "execute_query"},  # no input
        {"name": "execute_query", "input": None},
        {"name": "fetch_schema", "input": {"target": "t"}},  # not a query tool
    ]
    assert normalize_query_plan(trace) is None


def test_normalize_query_plan_skips_execute_query_without_query():
    """execute_query with missing query dict is skipped."""
    trace = [
        {"name": "execute_query", "input": {"db_type": "mysql", "target": "t"}},
    ]
    assert normalize_query_plan(trace) is None


def test_normalize_query_plan_skips_execute_plan_with_empty_steps():
    """execute_plan with empty steps → None (not a valid plan)."""
    trace = [
        {"name": "execute_plan", "input": {"plan": {"steps": []}}},
    ]
    assert normalize_query_plan(trace) is None


# ── extract_join_keys ──

def test_extract_join_keys_mysql_join():
    plan = {"steps": [{
        "db_type": "mysql", "database": "shop", "collection": "orders",
        "operation": "sql",
        "query": {"sql": "SELECT * FROM orders JOIN users ON orders.user_id = users.id"},
    }]}
    keys = extract_join_keys(plan)
    assert len(keys) == 1
    assert keys[0]["from"] == "orders.user_id"
    assert keys[0]["to"] == "users.id"


def test_extract_join_keys_mysql_straight_join():
    """STRAIGHT_JOIN — MySQL 优化器 hint，语义等价 JOIN."""
    plan = {"steps": [{
        "db_type": "mysql", "database": "shop", "collection": "orders",
        "operation": "sql",
        "query": {"sql": "SELECT * FROM orders STRAIGHT_JOIN users ON orders.uid = users.id"},
    }]}
    keys = extract_join_keys(plan)
    assert len(keys) == 1
    assert keys[0]["from"] == "orders.uid"
    assert keys[0]["to"] == "users.id"


def test_extract_join_keys_mysql_join_with_alias():
    """JOIN with alias — ON clause uses alias prefix, resolved to table name.

    Note: current regex only resolves the JOINed table's alias (right of JOIN),
    not the FROM table's alias (left of JOIN). So o.user_id stays unresolved
    because we don't know 'o' is an alias for 'orders' from the FROM clause.
    """
    plan = {"steps": [{
        "db_type": "mysql", "database": "shop", "collection": "orders",
        "operation": "sql",
        "query": {"sql": "SELECT * FROM orders o JOIN users u ON o.user_id = u.id"},
    }]}
    keys = extract_join_keys(plan)
    assert len(keys) == 1
    # u.id → users.id (u is alias for JOINed table 'users')
    assert keys[0]["to"] == "users.id"
    # o.user_id stays unresolved (o is FROM-clause alias, not JOIN alias)
    assert keys[0]["from"] == "o.user_id"


def test_extract_join_keys_mongo_lookup():
    plan = {"steps": [{
        "db_type": "mongodb", "database": "shop", "collection": "orders",
        "operation": "aggregate",
        "pipeline": [{"$lookup": {"from": "users", "localField": "user_id", "foreignField": "_id"}}],
    }]}
    keys = extract_join_keys(plan)
    assert len(keys) == 1
    assert keys[0]["from"] == "orders.user_id"
    assert keys[0]["to"] == "users._id"


# ── extract_join_keys — negative paths ──

def test_extract_join_keys_sql_without_join_returns_empty():
    plan = {"steps": [{"db_type": "mysql", "query": {"sql": "SELECT * FROM t"}}]}
    assert extract_join_keys(plan) == []


def test_extract_join_keys_sql_with_multiple_joins():
    plan = {"steps": [{
        "db_type": "mysql", "database": "shop", "collection": "orders",
        "operation": "sql",
        "query": {"sql": "SELECT * FROM orders JOIN users ON orders.uid = users.id JOIN products ON orders.pid = products.id"},
    }]}
    keys = extract_join_keys(plan)
    assert len(keys) == 2


def test_extract_join_keys_malformed_lookup_skipped():
    plan = {"steps": [{"db_type": "mongodb", "pipeline": [
        {"$lookup": "not_a_dict"},  # malformed
        {"$lookup": {"localField": "a"}},  # missing required fields
    ]}]}
    keys = extract_join_keys(plan)
    assert keys == []
