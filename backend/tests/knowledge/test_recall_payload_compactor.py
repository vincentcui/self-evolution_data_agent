"""recall_payload_compactor 单测 — 15 个语法形态 fixture.

设计目标: 不依赖生产数据, 完整覆盖 mongo / SQL 语法中可能出现的字面量场景.
"""
from __future__ import annotations

import pytest

from app.knowledge.recall_payload_compactor import compact_payload_for_recall


# ════════════════════════════════════════════
#  ObjectId / 长字面量数组
# ════════════════════════════════════════════

def test_compact_in_with_objectid_list():
    """$in: [80 ObjectId] → placeholder (avg_len=24 触发长 scalar 阈值)."""
    payload = {
        "query_json": {
            "pipeline": [
                {"$match": {"docId": {"$in": ["a" * 24] * 80}}},
            ],
        },
    }
    out = compact_payload_for_recall("example", payload)
    in_value = out["query_json"]["pipeline"][0]["$match"]["docId"]["$in"]
    assert isinstance(in_value, list) and len(in_value) == 1
    assert "__placeholder__" in in_value[0]
    assert "count=80" in in_value[0]["__placeholder__"]
    assert "str" in in_value[0]["__placeholder__"]


def test_compact_in_with_short_enum_kept():
    """$in: 5 个短状态枚举 → 全保留 (业务枚举有语义)."""
    payload = {
        "query_json": {
            "pipeline": [
                {"$match": {"status": {"$in": ["paid", "shipped", "refunded"]}}},
            ],
        },
    }
    out = compact_payload_for_recall("example", payload)
    in_value = out["query_json"]["pipeline"][0]["$match"]["status"]["$in"]
    assert in_value == ["paid", "shipped", "refunded"]


# ════════════════════════════════════════════
#  Regex 模式
# ════════════════════════════════════════════

def test_preserve_short_regex_pattern():
    """短 $regex 模式保留 (业务规则)."""
    payload = {"query_json": {"pipeline": [
        {"$match": {"name": {"$regex": "A级|B级", "$options": "i"}}},
    ]}}
    out = compact_payload_for_recall("example", payload)
    m = out["query_json"]["pipeline"][0]["$match"]["name"]
    assert m["$regex"] == "A级|B级"
    assert m["$options"] == "i"


def test_truncate_long_regex():
    """超长 $regex 截断 + <+K chars>."""
    long_pattern = "kw" + "|long_keyword_" * 30  # ~400 字
    payload = {"query_json": {"pipeline": [
        {"$match": {"name": {"$regex": long_pattern}}},
    ]}}
    out = compact_payload_for_recall("example", payload)
    truncated = out["query_json"]["pipeline"][0]["$match"]["name"]["$regex"]
    assert len(truncated) < len(long_pattern)
    assert "<+" in truncated and "chars>" in truncated


# ════════════════════════════════════════════
#  逻辑容器: $or / $and / pipeline
# ════════════════════════════════════════════

def test_preserve_or_logical_structure():
    """$or: [{a:1},{b:2}] 递归保元素, 不视为数据数组."""
    payload = {"query_json": {"pipeline": [
        {"$match": {"$or": [
            {"status": "paid"}, {"status": "shipped"}, {"refunded": True},
        ]}},
    ]}}
    out = compact_payload_for_recall("example", payload)
    or_value = out["query_json"]["pipeline"][0]["$match"]["$or"]
    assert isinstance(or_value, list) and len(or_value) == 3
    assert or_value[0] == {"status": "paid"}


def test_preserve_pipeline_stages_long():
    """pipeline 长 list[dict] 是控制流, 递归保所有 stage."""
    pipeline = [{"$match": {f"f{i}": i}} for i in range(20)]
    payload = {"query_json": {"pipeline": pipeline}}
    out = compact_payload_for_recall("example", payload)
    assert len(out["query_json"]["pipeline"]) == 20
    assert out["query_json"]["pipeline"][0] == {"$match": {"f0": 0}}


# ════════════════════════════════════════════
#  BSON 类型 wrapper
# ════════════════════════════════════════════

def test_compact_bson_oid_wrapper():
    """{"$oid": "..."} → 保 $oid 标识, 摘 value."""
    payload = {"query_json": {"pipeline": [
        {"$match": {"_id": {"$oid": "5f8a9b3c1a2b3c4d5e6f7a8b"}}},
    ]}}
    out = compact_payload_for_recall("example", payload)
    val = out["query_json"]["pipeline"][0]["$match"]["_id"]
    assert "$oid" in val
    assert "bson_value" in val["$oid"]


def test_compact_bson_date_wrapper():
    """{"$date": "..."} 同 $oid."""
    payload = {"query_json": {"pipeline": [
        {"$match": {"createdAt": {"$gte": {"$date": "2026-01-01T00:00:00Z"}}}},
    ]}}
    out = compact_payload_for_recall("example", payload)
    val = out["query_json"]["pipeline"][0]["$match"]["createdAt"]["$gte"]
    assert "$date" in val and "bson_value" in val["$date"]


# ════════════════════════════════════════════
#  $lookup 子 pipeline
# ════════════════════════════════════════════

def test_compact_lookup_with_subpipeline():
    """$lookup.pipeline 是嵌套 pipeline → 递归不摘."""
    payload = {"query_json": {"pipeline": [
        {"$lookup": {
            "from": "products",
            "let": {"oid": "$_id"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$order_id", "$$oid"]}}},
                {"$project": {"sku": 1, "qty": 1}},
            ],
            "as": "items",
        }},
    ]}}
    out = compact_payload_for_recall("example", payload)
    lk = out["query_json"]["pipeline"][0]["$lookup"]
    assert lk["from"] == "products"
    assert len(lk["pipeline"]) == 2
    assert "$expr" in lk["pipeline"][0]["$match"]


# ════════════════════════════════════════════
#  $bucket / $switch — 数值边界 / 分支
# ════════════════════════════════════════════

def test_compact_bucket_short_boundaries_kept():
    """$bucket.boundaries 短数组保留 (业务分桶规则)."""
    payload = {"query_json": {"pipeline": [
        {"$bucket": {
            "groupBy": "$price",
            "boundaries": [0, 100, 500, 1000, 5000],
            "default": "other",
        }},
    ]}}
    out = compact_payload_for_recall("example", payload)
    b = out["query_json"]["pipeline"][0]["$bucket"]
    assert b["boundaries"] == [0, 100, 500, 1000, 5000]


def test_compact_bucket_long_boundaries_compressed():
    """$bucket.boundaries > 阈值 → 摘."""
    long_boundaries = list(range(0, 200, 10))  # 20 个元素
    payload = {"query_json": {"pipeline": [
        {"$bucket": {"groupBy": "$x", "boundaries": long_boundaries}},
    ]}}
    out = compact_payload_for_recall("example", payload)
    b = out["query_json"]["pipeline"][0]["$bucket"]
    assert isinstance(b["boundaries"], list) and len(b["boundaries"]) == 1
    assert "__placeholder__" in b["boundaries"][0]
    assert "count=20" in b["boundaries"][0]["__placeholder__"]


# ════════════════════════════════════════════
#  长 str / JS 函数体
# ════════════════════════════════════════════

def test_truncate_where_js_string():
    """$where 长 JS 字符串截断."""
    js = "function() { return " + "this.field + " * 50 + "0 > 100; }"
    payload = {"query_json": {"pipeline": [
        {"$match": {"$where": js}},
    ]}}
    out = compact_payload_for_recall("example", payload)
    truncated = out["query_json"]["pipeline"][0]["$match"]["$where"]
    assert len(truncated) < len(js)
    assert "<+" in truncated


def test_preserve_short_str_values():
    """短 str 全保留."""
    payload = {"query_json": {"pipeline": [
        {"$match": {"category": "electronics", "brand": "acme"}},
    ]}}
    out = compact_payload_for_recall("example", payload)
    m = out["query_json"]["pipeline"][0]["$match"]
    assert m == {"category": "electronics", "brand": "acme"}


# ════════════════════════════════════════════
#  业务字段名 / mongo operator 全保留
# ════════════════════════════════════════════

def test_compact_preserves_all_field_names_and_operators():
    """递归任何深度, dict key 一律保留 (业务字段名 + $operator 都是知识本身)."""
    payload = {"query_json": {"pipeline": [
        {"$group": {
            "_id": "$category",
            "totalAmount": {"$sum": "$amount"},
            "avgQty": {"$avg": "$quantity"},
        }},
    ]}}
    out = compact_payload_for_recall("example", payload)
    g = out["query_json"]["pipeline"][0]["$group"]
    assert "_id" in g and "totalAmount" in g and "avgQty" in g
    assert "$sum" in g["totalAmount"] and "$avg" in g["avgQty"]


# ════════════════════════════════════════════
#  SQL IN 子句 (字符串 sql_signature 形式)
# ════════════════════════════════════════════

def test_compact_sql_long_in_clause_truncated_as_string():
    """SQL 字符串过长走 _walk_str 截断."""
    sql = "SELECT * FROM orders WHERE id IN (" + ",".join(str(i) for i in range(200)) + ")"
    payload = {"query_json": {"sql": sql}}
    out = compact_payload_for_recall("example", payload)
    truncated = out["query_json"]["sql"]
    assert len(truncated) < len(sql)
    assert "<+" in truncated and "chars>" in truncated


# ════════════════════════════════════════════
#  $or 含长 sub-match (递归处理)
# ════════════════════════════════════════════

def test_or_branches_with_long_in_each_branch_handled():
    """$or 是控制流但每个分支内的 $in 长数组仍要摘."""
    payload = {"query_json": {"pipeline": [
        {"$match": {"$or": [
            {"docIds": {"$in": ["x" * 24] * 50}},
            {"refIds": {"$in": ["y" * 24] * 50}},
        ]}},
    ]}}
    out = compact_payload_for_recall("example", payload)
    branches = out["query_json"]["pipeline"][0]["$match"]["$or"]
    assert len(branches) == 2
    assert "__placeholder__" in branches[0]["docIds"]["$in"][0]
    assert "__placeholder__" in branches[1]["refIds"]["$in"][0]


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


def test_example_question_preserved_only_query_json_compressed():
    """example.question 等语义字段保留, 仅 query_json 压缩."""
    long_question = "统计品牌名称包含A级、B级的品牌中各资源类型数量占比" * 5
    payload = {
        "question": long_question,
        "result_summary": "在 c_brand 上按 status 字段 $group + $sum:1" * 5,
        "target_collection": "c_brand",
        "query_json": {
            "pipeline": [{"$match": {"docId": {"$in": ["a" * 24] * 50}}}],
        },
    }
    out = compact_payload_for_recall("example", payload)
    # 语义字段原样
    assert out["question"] == long_question
    assert out["result_summary"] == payload["result_summary"]
    assert out["target_collection"] == "c_brand"
    # query_json 内的 ObjectId 列表被摘
    in_value = out["query_json"]["pipeline"][0]["$match"]["docId"]["$in"]
    assert "__placeholder__" in in_value[0]


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
