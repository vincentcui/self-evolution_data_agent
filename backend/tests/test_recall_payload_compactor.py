"""召回 payload 投影测试 — Task 3: route_hint 召回投影.

Verifies:
- route_hint.collection_path projected from list[CollectionRef] → list[str] ("db.coll")
- example.collections projected likewise (on top of existing query_json compression)
- rule.applies_to_collections projected likewise
- Non-collection fields pass through untouched
- Edge cases: empty list, malformed dicts, None
"""
from app.knowledge.recall_payload_compactor import compact_payload_for_recall


# ── route_hint ──


def test_route_hint_compact_projects_collection_path():
    payload = {
        "collection_path": [
            {"database": "shop", "collection": "orders"},
            {"database": "shop", "collection": "products"},
        ],
        "navigation_note": "n",
    }
    out = compact_payload_for_recall("route_hint", payload)
    assert out["collection_path"] == ["shop.orders", "shop.products"]
    assert out["navigation_note"] == "n"


def test_route_hint_no_collection_path_passes_through():
    payload = {"navigation_note": "just a note"}
    out = compact_payload_for_recall("route_hint", payload)
    assert out == payload


def test_route_hint_empty_collection_path():
    payload = {"collection_path": [], "navigation_note": "n"}
    out = compact_payload_for_recall("route_hint", payload)
    assert out["collection_path"] == []


def test_route_hint_malformed_refs_skipped():
    payload = {
        "collection_path": [
            {"database": "shop", "collection": "orders"},
            {"database": "", "collection": "no_db"},      # empty database
            {"collection": "no_db_key"},                   # missing database
            "bare_string",                                 # not a dict
            None,                                          # null element
        ],
    }
    out = compact_payload_for_recall("route_hint", payload)
    assert out["collection_path"] == ["shop.orders"]


# ── example ──


def test_example_compact_projects_collections():
    payload = {
        "question_pattern": "q",
        "collections": [{"database": "shop", "collection": "orders"}],
    }
    out = compact_payload_for_recall("example", payload)
    assert out["collections"] == ["shop.orders"]


def test_example_compact_preserves_query_json_compression():
    """Existing example query_json compression must not regress."""
    payload = {
        "query_json": {"$match": {"_id": {"$oid": "665f1e2a3b4c5d6e7f8a9b0c"}}},
        "collections": [{"database": "shop", "collection": "orders"}],
    }
    out = compact_payload_for_recall("example", payload)
    # query_json should be compressed (ObjectId value replaced)
    assert out["query_json"]["$match"]["_id"]["$oid"].startswith("<bson_value")
    # AND collections should be projected
    assert out["collections"] == ["shop.orders"]


# ── rule ──


def test_rule_compact_projects_applies_to_collections():
    payload = {
        "rule_text": "r",
        "applies_to_collections": [{"database": "shop", "collection": "orders"}],
    }
    out = compact_payload_for_recall("rule", payload)
    assert out["applies_to_collections"] == ["shop.orders"]


# ── unaffected types ──


def test_terminology_payload_unchanged():
    payload = {"term": "GMV", "reason": "gross merchandise value"}
    out = compact_payload_for_recall("terminology", payload)
    assert out == payload


def test_instance_alias_payload_unchanged():
    payload = {"alias": "foo", "canonical": "bar"}
    out = compact_payload_for_recall("instance_alias", payload)
    assert out == payload


def test_non_dict_payload_returned_as_is():
    assert compact_payload_for_recall("example", "not a dict") == "not a dict"
