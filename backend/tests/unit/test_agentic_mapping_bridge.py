"""Agent result → 通道映射桥 — 格式契约测试 (无 LLM, 无 DB).

验证 trainer._map_agent_to_channels 产出的 dict 携带 extraction_writer 下游消费方
期望的 key, 防止 key 错位静默丢数据。

注: 直接 import 生产函数 _map_agent_to_channels (而非 plan 草案的自包含副本),
使本测真正覆盖生产映射逻辑。返回 (CodeParseResult, business_examples)。
"""
from __future__ import annotations

from app.knowledge.trainer import _map_agent_to_channels

_MOCK_AGENT_OBJECTS = [
    {
        "paradigm": "relational",
        "kind": "table",
        "name": "orders",
        "description": "Order table",
        "source_ref": "src/Order.java:15",
        "fields": [
            {"name": "id", "type": "Long", "nullable": False},
            {"name": "customer_id", "type": "Long", "nullable": False},
            {"name": "status", "type": "String", "enum_values": [
                {"name": "PENDING", "db_value": "PENDING"},
                {"name": "CONFIRMED", "db_value": "CONFIRMED"},
            ]},
        ],
        "relations": [
            {"from_field": "customer_id", "to_object": "customers",
             "to_field": "id", "relation_type": "many_to_one"},
        ],
    },
    {
        "paradigm": "document",
        "kind": "collection",
        "name": "products",
        "description": "Product collection",
        "source_ref": "src/Product.java:20",
        "fields": [
            {"name": "_id", "type": "ObjectId", "nullable": False},
            {"name": "name", "type": "String"},
        ],
        "relations": [],
    },
]

_MOCK_KNOWLEDGE_PROPOSALS = [
    {"entry_type": "route_hint", "payload": {
        "mapper_namespace": "com.example.OrderMapper",
        "canonical_sql": "SELECT * FROM orders WHERE status = ?",
    }},
    {"entry_type": "terminology", "payload": {
        "term": "pending order",
        "definition": "Order with status PENDING",
        "primary_collection": "orders",
    }},
    {"entry_type": "rule", "payload": {
        "rule_text": "Orders must have a customer_id",
    }},
    {"entry_type": "example", "payload": {
        "sql_pattern": "SELECT * FROM orders WHERE status = ?",
        "tables": ["orders"],
        "question": "Find orders by status",
        "mapper_namespace": "com.example.OrderMapper",
    }},
]

_COLL_TO_DB = {"orders": "shop_db", "products": "shop_db"}


class TestJPAEntityMapping:
    def test_jpa_entity_has_table_keys(self):
        cr, _ = _map_agent_to_channels(_MOCK_AGENT_OBJECTS, [], _COLL_TO_DB)
        assert len(cr.jpa_entities) == 1
        je = cr.jpa_entities[0]
        assert je["table"] == "orders"
        assert je["table_name"] == "orders"
        assert len(je["fields"]) == 3

    def test_jpa_entity_has_database_from_coll_to_db(self):
        cr, _ = _map_agent_to_channels(_MOCK_AGENT_OBJECTS, [], _COLL_TO_DB)
        assert cr.jpa_entities[0]["database"] == "shop_db"

    def test_jpa_field_has_name_key(self):
        cr, _ = _map_agent_to_channels(_MOCK_AGENT_OBJECTS, [], _COLL_TO_DB)
        fd = cr.jpa_entities[0]["fields"][1]  # customer_id
        assert fd["name"] == "customer_id"
        assert fd["type"] == "Long"


class TestMongoMapping:
    def test_mongo_entity_has_collection_keys(self):
        cr, _ = _map_agent_to_channels(_MOCK_AGENT_OBJECTS, [], _COLL_TO_DB)
        assert len(cr.mongo_documents) == 1
        md = cr.mongo_documents[0]
        assert md["collection"] == "products"
        assert md["collection_name"] == "products"
        assert md["class_name"] == "products"


class TestRelationshipMapping:
    def test_relationship_from_jpa_entity(self):
        """relations 挂在 entity dict 上, 与 field 共享同一 database gate — 不剥离独立通道."""
        cr, _ = _map_agent_to_channels(_MOCK_AGENT_OBJECTS, [], _COLL_TO_DB)
        je = cr.jpa_entities[0]
        assert "relations" in je
        assert len(je["relations"]) == 1
        rel = je["relations"][0]
        assert rel["from_target"] == "orders"
        assert rel["from_field"] == "customer_id"
        assert rel["to_target"] == "customers"
        assert rel["to_field"] == "id"
        assert rel["relation_type"] == "many_to_one"
        assert rel["source"] == "agentic"


class TestRouteHintMapping:
    def test_route_hint_proposal_ignored(self):
        """C10/D6: route_hint 分支已删, 即使收到该 entry_type 也不再路由到 mybatis_entries."""
        cr, _ = _map_agent_to_channels(_MOCK_AGENT_OBJECTS, _MOCK_KNOWLEDGE_PROPOSALS, _COLL_TO_DB)
        assert cr.mybatis_entries == []


class TestTerminologyMapping:
    def test_terminology_has_term(self):
        cr, _ = _map_agent_to_channels(_MOCK_AGENT_OBJECTS, _MOCK_KNOWLEDGE_PROPOSALS, _COLL_TO_DB)
        assert len(cr.business_terms_candidates) == 1
        bt = cr.business_terms_candidates[0]
        assert bt["term"] == "pending order"
        assert "primary_collection" in bt
        assert "primary_database" in bt  # resolved from coll_to_db
        assert "db_type" not in bt  # resolved by writer

    def test_terminology_db_fields_resolved_from_coll_to_db(self):
        """primary_collection 由 agent 提供, primary_database 由 coll_to_db 程序化反查."""
        cr, _ = _map_agent_to_channels(_MOCK_AGENT_OBJECTS, _MOCK_KNOWLEDGE_PROPOSALS, _COLL_TO_DB)
        bt = cr.business_terms_candidates[0]
        assert bt["primary_collection"] == "orders"  # agent 提供
        assert bt["primary_database"] == "shop_db"   # coll_to_db 反查
        assert "db_type" not in bt                   # writer 反查

    def test_terminology_missing_collection_defaults_to_empty_database(self):
        """Agent 未填 primary_collection 时, coll_to_db 反查不到 → primary_database 为空."""
        proposals = [{"entry_type": "terminology", "payload": {"term": "no-coll term"}}]
        cr, _ = _map_agent_to_channels(_MOCK_AGENT_OBJECTS, proposals, _COLL_TO_DB)
        bt = cr.business_terms_candidates[0]
        assert bt["primary_database"] == ""  # coll_to_db.get("", "") = ""
        assert bt["primary_collection"] == ""


class TestRuleMapping:
    def test_rule_has_rule_text(self):
        cr, _ = _map_agent_to_channels(_MOCK_AGENT_OBJECTS, _MOCK_KNOWLEDGE_PROPOSALS, _COLL_TO_DB)
        assert len(cr.business_rules_candidates) == 1
        assert cr.business_rules_candidates[0]["rule_text"] == "Orders must have a customer_id"


class TestExampleMapping:
    def test_example_has_sql_pattern_and_tables(self):
        cr, business_examples = _map_agent_to_channels(
            _MOCK_AGENT_OBJECTS, _MOCK_KNOWLEDGE_PROPOSALS, _COLL_TO_DB)
        assert len(business_examples) >= 1
        ex = business_examples[0]
        assert ex["sql_pattern"] == "SELECT * FROM orders WHERE status = ?"
        assert "orders" in ex["tables"]
        assert ex.get("question")


class TestEnumValuesPreserved:
    def test_enum_values_in_field(self):
        cr, _ = _map_agent_to_channels(_MOCK_AGENT_OBJECTS, [], _COLL_TO_DB)
        status_fd = next(f for f in cr.jpa_entities[0]["fields"] if f["name"] == "status")
        assert len(status_fd["enum_values"]) == 2
