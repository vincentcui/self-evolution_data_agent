"""Phase 2 — trace_extractor 单测.

验证 extract_collections / normalize_query_plan / extract_join_keys 与
api/query.py 原实现行为一致.

route_hint 专属机械字段 (derive_cost_strategy / extract_join_fields / extract_final_pipeline)
已随 C5 死代码清理删除 — route_hint 已收敛为纯人工录入, 不再有代码侧抽取.
"""

from app.knowledge.trace_extractor import (
    extract_collections,
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
