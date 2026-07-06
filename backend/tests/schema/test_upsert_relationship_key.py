"""_upsert_relationship — 6-key dedup + sources merge + to_field non-empty wins."""
from __future__ import annotations

import json as j

from app.knowledge.canonical_promote import _upsert_relationship


def _mk(**kw):
    return {
        "from_target": "t_order", "from_field": "f1",
        "to_db_type": "mysql", "to_database": "db",
        "to_target": "t2", "to_field": "id",
        "relation_type": "many_to_one", "sources": ["code"],
        **kw,
    }


def test_new_key_append():
    ex = j.dumps([_mk()])
    r = j.loads(_upsert_relationship(ex, _mk(from_field="f2")))
    assert len(r) == 2


def test_same_key_merge():
    ex = j.dumps([_mk()])
    r = j.loads(_upsert_relationship(ex, _mk()))
    assert len(r) == 1


def test_to_field_nonempty_wins():
    ex = j.dumps([_mk(to_field="")])
    r = j.loads(_upsert_relationship(ex, _mk(to_field="real_id")))
    assert r[0]["to_field"] == "real_id"


def test_to_field_existing_nonempty_kept():
    """已有 non-empty to_field + value 空 → 保留已有, 不被空值覆盖."""
    ex = j.dumps([_mk(to_field="real_id")])
    r = j.loads(_upsert_relationship(ex, _mk(to_field="")))
    assert r[0]["to_field"] == "real_id"


def test_sources_accumulate():
    ex = j.dumps([_mk(sources=["code"])])
    r = j.loads(_upsert_relationship(ex, _mk(sources=["introspect_fk"])))
    assert set(r[0]["sources"]) == {"code", "introspect_fk"}


def test_legacy_entry_upgrade():
    legacy = j.dumps([{
        "from_target": "t_order", "from_field": "f1",
        "to_target": "t2", "to_field": "id",
        "relation_type": "many_to_one",
    }])
    r = j.loads(_upsert_relationship(legacy, _mk()))
    assert len(r) == 1
    assert r[0]["to_db_type"] == "mysql"


def test_legacy_empty_to_database_upgraded():
    legacy = j.dumps([{
        "from_target": "t_order", "from_field": "f1",
        "to_db_type": "mysql", "to_database": "",
        "to_target": "t2", "to_field": "id",
        "relation_type": "many_to_one",
    }])
    r = j.loads(_upsert_relationship(legacy, _mk()))
    assert r[0]["to_database"] == "db"
