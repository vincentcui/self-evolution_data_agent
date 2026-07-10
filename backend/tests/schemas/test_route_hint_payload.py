"""route_hint 新 schema (spec 2026-07-08 C2): {collection_path, navigation_note}.

question_pattern 不在 payload (唯一真相源是 content). 旧字段 join_fields/
cost_strategy/avoid_path/reason/question_pattern 一律 extra_forbidden.
"""
import pytest
from pydantic import ValidationError

from app.schemas.knowledge_payload import RouteHintPayload, parse_payload


def test_accepts_new_shape_minimal():
    p = RouteHintPayload()
    assert p.collection_path == []
    assert p.navigation_note == ""


def test_accepts_new_shape_full():
    p = RouteHintPayload(
        collection_path=["shop.orders", "shop.products"],
        navigation_note="orders.items[].sku ↔ products.sku (nested_array), 非 products.id",
    )
    assert p.collection_path == ["shop.orders", "shop.products"]


def test_rejects_question_pattern():
    with pytest.raises(ValidationError):
        RouteHintPayload(question_pattern="x", collection_path=[], navigation_note="")


def test_rejects_old_join_fields():
    with pytest.raises(ValidationError):
        RouteHintPayload(collection_path=[], navigation_note="", join_fields=[{"a": "x", "b": "y"}])


def test_rejects_old_cost_strategy_and_reason():
    with pytest.raises(ValidationError):
        RouteHintPayload(collection_path=[], navigation_note="", cost_strategy="default", reason="r")


def test_rejects_duplicate_collection_path():
    with pytest.raises(ValidationError):
        RouteHintPayload(collection_path=["shop.orders", "shop.orders"], navigation_note="")


def test_parse_payload_route_hint_dispatch():
    p = parse_payload("route_hint", {"collection_path": ["a"], "navigation_note": "n"})
    assert isinstance(p, RouteHintPayload)
    assert p.navigation_note == "n"
