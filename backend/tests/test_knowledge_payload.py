"""CollectionRef + 3 payload schema upgrade tests (Task 1).

Verifies:
- CollectionRef extra="forbid"
- RouteHintPayload.collection_path: list[CollectionRef]
- ExamplePayload.collections: list[CollectionRef]
- RulePayload.applies_to_collections: list[CollectionRef]
- _no_duplicate validator uses (database, collection) tuple dedup
- Legacy list[str] form rejected (no compat branch)
"""
import pytest
from app.schemas.knowledge_payload import (
    CollectionRef, RouteHintPayload, ExamplePayload, RulePayload, parse_payload,
)


def test_collection_ref_extra_forbid():
    with pytest.raises(Exception):
        CollectionRef(database="shop", collection="orders", extra="x")


def test_route_hint_collection_path_structured():
    p = RouteHintPayload(collection_path=[
        {"database": "shop", "collection": "orders"},
        {"database": "shop", "collection": "products"},
    ], navigation_note="n")
    assert p.collection_path[0].database == "shop"
    assert p.collection_path[0].collection == "orders"


def test_route_hint_no_duplicate_by_db_collection():
    with pytest.raises(Exception, match="重复"):
        RouteHintPayload(collection_path=[
            {"database": "shop", "collection": "orders"},
            {"database": "shop", "collection": "orders"},
        ])


def test_route_hint_same_collection_different_db_ok():
    # 不同 database 下同名 collection 不算重复
    p = RouteHintPayload(collection_path=[
        {"database": "shop", "collection": "orders"},
        {"database": "log", "collection": "orders"},
    ])
    assert len(p.collection_path) == 2


def test_route_hint_reject_legacy_string_form():
    # 存量忽略: 纯字符串形态必须被拒(不兼容)
    with pytest.raises(Exception):
        RouteHintPayload(collection_path=["shop.orders"])


def test_example_collections_structured():
    p = ExamplePayload(question_pattern="q", final_query_plan={"steps": []}, collections=[
        {"database": "shop", "collection": "orders"},
    ])
    assert p.collections[0].database == "shop"


def test_rule_applies_to_collections_structured():
    p = RulePayload(rule_text="r", applies_to_collections=[
        {"database": "shop", "collection": "orders"},
    ])
    assert p.applies_to_collections[0].collection == "orders"


def test_parse_payload_dispatches_to_new_schema():
    p = parse_payload("route_hint", {
        "collection_path": [{"database": "shop", "collection": "orders"}],
        "navigation_note": "n",
    })
    assert isinstance(p, RouteHintPayload)
    assert p.collection_path[0].database == "shop"
