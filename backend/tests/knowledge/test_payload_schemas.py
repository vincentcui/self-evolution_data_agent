"""5 类 payload Pydantic schemas 校验测试 — 真实环境无 mock"""
import pytest
from pydantic import ValidationError

from app.schemas.knowledge_payload import (
    TerminologyPayload, ExamplePayload, RulePayload,
    RouteHintPayload, parse_payload,
)


def test_terminology_payload_minimal():
    p = TerminologyPayload(
        term="条目",
        primary_collection="c_sku",
        primary_database="order_db",
        db_type="mongodb",
    )
    assert p.term == "条目"
    assert p.synonyms == []
    assert p.primary_collection == "c_sku"


def test_terminology_payload_full():
    p = TerminologyPayload(
        term="订单", synonyms=["order", "订单"],
        primary_collection="c_product", primary_database="order_db",
        db_type="mongodb",
        primary_field="categoryId", source_collections=["c_product"],
    )
    assert p.synonyms == ["order", "订单"]
    assert p.primary_field == "categoryId"


def test_example_payload_requires_question_pattern():
    """question_pattern is the only required field."""
    with pytest.raises(ValidationError):
        ExamplePayload()  # type: ignore[call-arg]


def test_example_payload_minimal():
    p = ExamplePayload(question_pattern="查询订单")
    assert p.question_pattern == "查询订单"
    assert p.collections == []
    assert p.join_keys == []
    assert p.final_query_plan is None
    assert p.result_summary == ""


def test_example_payload_full():
    p = ExamplePayload(
        question_pattern="查看各订单状态的数量分布",
        collections=["shop.orders"],
        join_keys=[],
        final_query_plan={"steps": [{"db_type": "mongodb", "collection": "orders", "query": {"pipeline": []}}]},
        result_summary="在 orders 上按 status 字段 $group + $sum:1",
    )
    assert len(p.collections) == 1


def test_example_payload_accepts_old_fields():
    """extra='allow' — old fields pass through without rejection."""
    p = ExamplePayload(
        question_pattern="查询订单",
        question="查询订单",
        target_collection="orders",
        query_json={"find": {"createdAt": {"$gte": "2026-04-28"}}},
        nl_paraphrases=["查看订单"],
    )
    assert p.question_pattern == "查询订单"
    assert p.model_extra is not None


def test_rule_payload():
    p = RulePayload(rule_text="订单必须含 categoryId", priority=1)
    assert p.applies_to_collections == []
    assert p.priority == 1


def test_route_hint_payload():
    p = RouteHintPayload(
        question_pattern="商品→订单→条目",
        collection_path=["c_category", "c_product", "c_sku"],
        join_fields=[{"from": "c_category._id", "to": "c_product.categoryId"}],
        cost_strategy="batched_count_only",
        reason="多层关联用分批",
    )
    assert p.cost_strategy == "batched_count_only"
    assert len(p.collection_path) == 3


def test_parse_payload_dispatch():
    """parse_payload(entry_type, raw_dict) → 对应 Pydantic 实例"""
    p = parse_payload("terminology", {
        "term": "条目",
        "primary_collection": "c_sku",
        "primary_database": "order_db",
        "db_type": "mongodb",
    })
    assert isinstance(p, TerminologyPayload)

    p = parse_payload("rule", {"rule_text": "x"})
    assert isinstance(p, RulePayload)

    with pytest.raises(ValueError):
        parse_payload("unknown_type", {})
