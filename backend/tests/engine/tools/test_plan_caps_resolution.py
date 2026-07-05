"""Unit tests for plan_tools._resolve_caps_by_target (spec task 3.2).

per-collection capability resolution at the DATASOURCE dimension:
- mongodb collections resolve caps via ds.db_profile_json caps projection
- native/empty-restriction caps are omitted (no noise)
- mysql collections are skipped (no mongo capability concept)
- failure-safe: resolve_ds None → omit, no exception
- caps map key format is "[db_type] database.collection"

Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.2
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engine.tools.plan_tools import (
    _caps_target_key,
    _has_restrictions,
    _resolve_caps_by_target,
)

# ════════════════════════════════════════════
#  Test data helpers
# ════════════════════════════════════════════


def _native_caps() -> dict:
    """Native MongoDB: empty restriction set (resolves to no restrictions)."""
    return {
        "version": "5.0.0",
        "flavor": "mongodb",
        "unsupported_ops": [],
        "unsupported_stage_variants": [],
        "syntax_constraints": [],
        "equivalent_hints": [],
        "agg_ops_unsupported": [],
    }


def _documentdb_caps() -> dict:
    """DocumentDB-profile subset carrying real restrictions (from aws_documentdb.json)."""
    return {
        "version": "5.0.0",
        "flavor": "documentdb",
        "unsupported_ops": ["$round", "$function"],
        "unsupported_stage_variants": ["$facet", "$lookup.let_pipeline"],
        "syntax_constraints": ["project_no_dollar_fieldpath"],
        "equivalent_hints": [
            {"restriction": "$round", "suggestion": "改用 $floor/$ceil"},
            {"restriction": "project_no_dollar_fieldpath", "suggestion": "字段引用用裸名"},
        ],
        "agg_ops_unsupported": ["$round", "$function"],
    }


def _make_ds(ds_id: int, caps: dict | None = None) -> MagicMock:
    """Build a mock datasource. If caps provided, serializes them to db_profile_json."""
    ds = MagicMock()
    ds.id = ds_id
    ds.db_profile_json = json.dumps(caps) if caps else "{}"
    return ds


# ════════════════════════════════════════════
#  Pure-function unit tests (key format / restriction detection)
# ════════════════════════════════════════════


def test_caps_target_key_format():
    """Key format is exactly '[db_type] database.collection' (R3.6)."""
    assert _caps_target_key("mongodb", "rp_db", "c_brand") == "[mongodb] rp_db.c_brand"
    assert _caps_target_key("mysql", "shop_db", "orders") == "[mysql] shop_db.orders"


def test_has_restrictions_true_for_each_category():
    """Any one of the three restriction categories → True."""
    assert _has_restrictions({"unsupported_ops": ["$round"]}) is True
    assert _has_restrictions({"unsupported_stage_variants": ["$facet"]}) is True
    assert _has_restrictions({"syntax_constraints": ["project_no_dollar_fieldpath"]}) is True


def test_has_restrictions_false_for_native_empty_caps():
    """Native caps with all-empty restriction lists → False (omit, no noise)."""
    assert _has_restrictions(_native_caps()) is False
    assert _has_restrictions({}) is False


# ════════════════════════════════════════════
#  _resolve_caps_by_target — datasource-dimension resolution
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_two_datasources_native_omitted_documentdb_present():
    """Two mongodb collections on two datasources:
    native ds → empty caps omitted; documentdb ds → restrictions present (R3.2/3.3/3.4)."""
    ds_native = _make_ds(1, _native_caps())
    ds_doc = _make_ds(2, _documentdb_caps())

    def _resolve(db, ns_id, db_type, database):
        return {"native_db": ds_native, "doc_db": ds_doc}.get(database)

    with patch(
        "app.engine.tools.plan_tools.resolve_ds",
        AsyncMock(side_effect=_resolve),
    ):
        out = await _resolve_caps_by_target(
            MagicMock(),
            namespace_id=1,
            collections=[
                {"db_type": "mongodb", "database": "native_db", "collection": "products"},
                {"db_type": "mongodb", "database": "doc_db", "collection": "orders"},
            ],
        )

    # native collection omitted (no noise); documentdb collection present
    assert "[mongodb] native_db.products" not in out
    assert "[mongodb] doc_db.orders" in out
    caps = out["[mongodb] doc_db.orders"]
    assert caps["flavor"] == "documentdb"
    assert "$round" in caps["unsupported_ops"]
    assert "project_no_dollar_fieldpath" in caps["syntax_constraints"]


@pytest.mark.asyncio
async def test_key_format_matches_db_type_database_collection():
    """Resolved key uses exactly '[db_type] database.collection' (R3.6)."""
    ds_doc = _make_ds(2, _documentdb_caps())

    with patch(
        "app.engine.tools.plan_tools.resolve_ds",
        AsyncMock(return_value=ds_doc),
    ):
        out = await _resolve_caps_by_target(
            MagicMock(),
            namespace_id=7,
            collections=[
                {"db_type": "mongodb", "database": "rp_db", "collection": "c_brand"},
            ],
        )

    assert list(out.keys()) == ["[mongodb] rp_db.c_brand"]


@pytest.mark.asyncio
async def test_mysql_collection_skipped():
    """mysql collection is skipped — resolve_ds never called for it (R3.5)."""
    resolve_mock = AsyncMock()

    with patch(
        "app.engine.tools.plan_tools.resolve_ds",
        resolve_mock,
    ):
        out = await _resolve_caps_by_target(
            MagicMock(),
            namespace_id=1,
            collections=[
                {"db_type": "mysql", "database": "shop_db", "collection": "orders"},
            ],
        )

    assert out == {}
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_ds_none_omits_collection_no_exception():
    """resolve_ds returns None → collection omitted, no exception (R4.1)."""
    with patch(
        "app.engine.tools.plan_tools.resolve_ds",
        AsyncMock(return_value=None),
    ):
        out = await _resolve_caps_by_target(
            MagicMock(),
            namespace_id=1,
            collections=[
                {"db_type": "mongodb", "database": "ghost_db", "collection": "missing"},
            ],
        )

    assert out == {}


@pytest.mark.asyncio
async def test_mixed_collections_only_restricted_documentdb_kept():
    """End-to-end mix (R3.2–3.5): native omitted, mysql skipped, missing-ds omitted,
    only the documentdb collection with restrictions survives."""
    ds_native = _make_ds(1, _native_caps())
    ds_doc = _make_ds(2, _documentdb_caps())

    def _resolve(db, ns_id, db_type, database):
        return {"native_db": ds_native, "doc_db": ds_doc}.get(database)  # ghost_db → None

    with patch(
        "app.engine.tools.plan_tools.resolve_ds",
        AsyncMock(side_effect=_resolve),
    ):
        out = await _resolve_caps_by_target(
            MagicMock(),
            namespace_id=1,
            collections=[
                {"db_type": "mongodb", "database": "native_db", "collection": "products"},
                {"db_type": "mongodb", "database": "doc_db", "collection": "orders"},
                {"db_type": "mysql", "database": "shop_db", "collection": "users"},
                {"db_type": "mongodb", "database": "ghost_db", "collection": "missing"},
            ],
        )

    assert list(out.keys()) == ["[mongodb] doc_db.orders"]
