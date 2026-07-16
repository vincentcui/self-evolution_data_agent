"""HQ _extract_route_path tests — Task 3: CollectionRef 裸名投影.

Verifies:
- _extract_route_path returns bare collection names (database-agnostic)
- Handles empty/missing/malformed collection_path
- JSON decode errors return None
"""
import json
from app.knowledge.hq_writer import _extract_route_path


class _FakeEntry:
    """Minimal stand-in for KnowledgeEntry (only .payload used by _extract_route_path)."""
    def __init__(self, payload):
        self.payload = payload


def test_extract_route_path_returns_bare_collection_names():
    entry = _FakeEntry(json.dumps({"collection_path": [
        {"database": "shop", "collection": "orders"},
        {"database": "log", "collection": "events"},
    ]}))
    # HQ 校验用裸 collection 名 (拓扑无关 database), 与 LLM 产 covered_path 可比
    assert _extract_route_path(entry) == ["orders", "events"]


def test_extract_route_path_empty_payload():
    assert _extract_route_path(_FakeEntry(json.dumps({}))) is None


def test_extract_route_path_empty_collection_path():
    assert _extract_route_path(_FakeEntry(json.dumps({"collection_path": []}))) is None


def test_extract_route_path_null_payload():
    assert _extract_route_path(_FakeEntry(None)) is None


def test_extract_route_path_invalid_json():
    assert _extract_route_path(_FakeEntry("not json")) is None


def test_extract_route_path_skips_malformed_refs():
    entry = _FakeEntry(json.dumps({"collection_path": [
        {"database": "shop", "collection": "orders"},
        {"database": "x"},                   # missing collection
        "bare_string",                        # not a dict
        None,
    ]}))
    assert _extract_route_path(entry) == ["orders"]


def test_extract_route_path_all_malformed_returns_none():
    entry = _FakeEntry(json.dumps({"collection_path": [
        {"database": "x"},
        "not_a_dict",
    ]}))
    assert _extract_route_path(entry) is None


def test_extract_route_path_collection_path_not_list():
    entry = _FakeEntry(json.dumps({"collection_path": "orders"}))
    assert _extract_route_path(entry) is None
