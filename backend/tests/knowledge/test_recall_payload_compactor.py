"""recall_payload_compactor 单测.

Task 6 后: query_json 是死字段 (D1 后 example payload 不含 query_json),
仅保留 final_query_plan 压缩 + 非 example entry_type 原样保留的测试.
"""
from __future__ import annotations

import pytest

from app.knowledge.recall_payload_compactor import compact_payload_for_recall


# ════════════════════════════════════════════
#  Edge case: 非 dict payload
# ════════════════════════════════════════════

@pytest.mark.parametrize("bad_input", [None, "string", 123, []])
def test_non_dict_payload_returned_as_is(bad_input):
    assert compact_payload_for_recall("example", bad_input) == bad_input


def test_empty_dict_payload():
    assert compact_payload_for_recall("example", {}) == {}


# ════════════════════════════════════════════
#  entry_type 分发: 只 example 压缩, 其他类型语义文本必须原样
#  (修复 2026-05-29 KE 1141 route_hint.reason 被截断导致召回失效的回归)
# ════════════════════════════════════════════

def test_route_hint_reason_preserved_full_length():
    """route_hint.reason 是核心避坑链路, 即使 373 字也必须原样保留."""
    long_reason = (
        "路径穿透：step6 在 c_brand 上直接 $group by brandItemType/itemType "
        "无法得到子资源明细，step8 切 c_unit 获取 materialModules，step9 切 c_module "
        "获取 groups.resources；关联字段：step11 用 c_unit.brandId 关联品牌，step16 "
        "用 c_module.docId 关联模块（非 id）；嵌套位置：资源类型在 "
        "c_module.groups[].resources[].itemType，需双层 $unwind；避坑：step13 "
        "用 $expr/$match(id) 关联 c_module 返空，step14 用 _id 匹配也返空，step16 "
        "改用 docId 才得 12 行数据"
    )
    payload = {
        "question_pattern": "统计某类品牌所包含的各资源类型数量/占比",
        "reason": long_reason,
        "collection_path": ["c_brand", "c_unit", "c_module"],
    }
    out = compact_payload_for_recall("route_hint", payload)
    assert out["reason"] == long_reason  # 完整保留
    assert out["question_pattern"] == payload["question_pattern"]


def test_rule_rule_text_preserved_full_length():
    """rule.rule_text 是规则文本本身, 任何长度都必须原样保留."""
    long_text = (
        "c_module 与上游集合关联时, 应使用 c_module.docId 字段匹配, 而非 c_module.id "
        "或 c_module._id; 用 id/_id 或 \\$expr(id) 关联均返空 (step13 \\$match(\\$expr基于id) "
        "返0行, step14 \\$match(id) 返0行, step16 改 \\$match(docId) 得12行)" * 2
    )
    payload = {"rule_text": long_text, "rule_kind": "join_pattern", "priority": 10}
    out = compact_payload_for_recall("rule", payload)
    assert out["rule_text"] == long_text


def test_terminology_payload_preserved():
    """terminology payload 全是语义字段, 原样保留."""
    payload = {
        "term": "活跃用户",
        "primary_collection": "users",
        "primary_database": "appdb",
        "db_type": "mongodb",
        "synonyms": ["活跃账户", "活跃账号"] * 10,  # 长 list 也保留
    }
    out = compact_payload_for_recall("terminology", payload)
    assert out == payload


def test_instance_alias_payload_preserved():
    """instance_alias payload 全保留."""
    payload = {
        "alias": "我们的旗舰产品", "canonical_name": "Pro Max 系列",
        "target_id": "p_007", "id_field": "_id",
    }
    out = compact_payload_for_recall("instance_alias", payload)
    assert out == payload


# ════════════════════════════════════════════
#  final_query_plan 压缩
# ════════════════════════════════════════════

def test_compact_final_query_plan_mysql():
    payload = {
        "final_query_plan": {
            "steps": [{
                "db_type": "mysql", "database": "shop", "collection": "orders",
                "operation": "sql",
                "query": {"sql": "SELECT * FROM orders WHERE user_id = 42 AND name = 'Alice'"},
            }],
        },
    }
    out = compact_payload_for_recall("example", payload)
    sql = out["final_query_plan"]["steps"][0]["query"]["sql"]
    assert "Alice" not in sql


def test_compact_final_query_plan_oracle():
    """Oracle SQL literal stripping — same regex as MySQL."""
    payload = {
        "final_query_plan": {
            "steps": [{
                "db_type": "oracle", "database": "SHOP", "collection": "ORDERS",
                "operation": "sql",
                "query": {"sql": "SELECT * FROM ORDERS WHERE status = 'ACTIVE' AND qty > 100"},
            }],
        },
    }
    out = compact_payload_for_recall("example", payload)
    sql = out["final_query_plan"]["steps"][0]["query"]["sql"]
    assert "ACTIVE" not in sql
    assert "100" not in sql


def test_compact_final_query_plan_mongo(monkeypatch):
    """_walk 对长列表触发 placeholder 替换.

    用 monkeypatch 钉死 RECALL_PAYLOAD_MAX_LIST_LEN=8, 不受生产环境配置影响.
    """
    monkeypatch.setattr(
        "app.config.settings.recall_payload_max_list_len", 8
    )

    payload = {
        "final_query_plan": {
            "steps": [{
                "db_type": "mongodb", "database": "shop", "collection": "orders",
                "operation": "aggregate",
                "query": {"pipeline": [
                    {"$match": {"docId": {"$in": ["oid_001", "oid_002", "oid_003"] * 30}}},
                ]},
            }],
        },
    }
    out = compact_payload_for_recall("example", payload)
    query = out["final_query_plan"]["steps"][0]["query"]
    assert "__placeholder__" in str(query) or "list_of_str" in str(query)


def test_compact_sql_in_numeric_list():
    """SQL IN (1, 2, 3) 数值列表被替换为 IN(...), 保留运算符结构."""
    payload = {
        "final_query_plan": {
            "steps": [{
                "db_type": "mysql", "database": "shop", "collection": "orders",
                "operation": "sql",
                "query": {"sql": "SELECT * FROM orders WHERE status IN (1, 2, 3) AND user_id = 42"},
            }],
        },
    }
    out = compact_payload_for_recall("example", payload)
    sql = out["final_query_plan"]["steps"][0]["query"]["sql"]
    assert "IN(...)" in sql
    assert "1, 2, 3" not in sql
    assert "user_id = N" in sql  # = 42 → N


def test_compact_sql_not_in_preserved():
    """NOT IN (1, 2) — IN 锚定词边界, NOT IN(...) 中 IN 被独立替换."""
    payload = {
        "final_query_plan": {
            "steps": [{
                "db_type": "mysql", "database": "shop", "collection": "orders",
                "operation": "sql",
                "query": {"sql": "SELECT * FROM orders WHERE status NOT IN (1, 2)"},
            }],
        },
    }
    out = compact_payload_for_recall("example", payload)
    sql = out["final_query_plan"]["steps"][0]["query"]["sql"]
    assert "NOT IN(...)" in sql


# ════════════════════════════════════════════
#  Collection ref 投影 (Task 3)
# ════════════════════════════════════════════

def test_collection_ref_projection_route_hint():
    """route_hint.collection_path 投影为字符串列表."""
    payload = {
        "collection_path": [
            {"database": "shop", "collection": "orders"},
            {"database": "shop", "collection": "users"},
        ],
    }
    out = compact_payload_for_recall("route_hint", payload)
    assert out["collection_path"] == ["shop.orders", "shop.users"]


def test_collection_ref_projection_example():
    """example.collections 投影为字符串列表."""
    payload = {
        "collections": [
            {"database": "shop", "collection": "orders"},
        ],
        "final_query_plan": {"steps": []},
    }
    out = compact_payload_for_recall("example", payload)
    assert out["collections"] == ["shop.orders"]
