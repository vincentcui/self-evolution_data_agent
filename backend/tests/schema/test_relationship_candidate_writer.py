"""write_relationship_candidates_from_foreign_keys — FK→candidate (source 走 evidence)."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select as sa_select

from app.models import DataSource, SchemaCanonicalCandidate


def _fk(child, col, parent, ref, db="db_a", to_db="mysql"):
    return [{
        "from_target": child, "from_field": col,
        "to_db_type": to_db, "to_database": db,
        "to_target": parent, "to_field": ref,
        "relation_type": "many_to_one",
    }]


@pytest.mark.asyncio
async def test_writes_candidate_no_source_in_value(test_session):
    from app.knowledge.canonical_relationship_writer import (
        write_relationship_candidates_from_foreign_keys,
    )
    ds = DataSource(
        namespace_id=1, db_type="mysql",
        host="h", port=3306, database="db_a", username="u", password="p",
    )
    test_session.add(ds)
    await test_session.flush()
    await write_relationship_candidates_from_foreign_keys(
        test_session, namespace_id=1, datasource=ds,
        foreign_keys=_fk("t_order", "user_id", "t_user", "id"), ds_id=ds.id,
    )
    rows = list((await test_session.execute(
        sa_select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.candidate_kind == "relationship",
        ),
    )).scalars().all())
    val = json.loads(rows[0].candidate_value_json)
    assert "source" not in val
    ev = json.loads(rows[0].evidence_sources_json)
    assert ev[0]["source"] == "introspect_fk"


@pytest.mark.asyncio
async def test_empty_noop(test_session):
    from app.knowledge.canonical_relationship_writer import (
        write_relationship_candidates_from_foreign_keys,
    )
    ds = DataSource(
        namespace_id=1, db_type="mysql",
        host="h", port=3306, database="db_a", username="u", password="p",
    )
    n = await write_relationship_candidates_from_foreign_keys(
        test_session, namespace_id=1, datasource=ds, foreign_keys=[], ds_id=999,
    )
    assert n == 0


@pytest.mark.asyncio
async def test_scoped_by_referenced_targets(test_session):
    from app.knowledge.canonical_relationship_writer import (
        write_relationship_candidates_from_foreign_keys,
    )
    ds = DataSource(
        namespace_id=1, db_type="mysql",
        host="h", port=3306, database="db_a", username="u", password="p",
    )
    test_session.add(ds)
    await test_session.flush()
    fks = _fk("t_order", "user_id", "t_user", "id") \
        + _fk("t_payment", "order_id", "t_order", "id")
    await write_relationship_candidates_from_foreign_keys(
        test_session, namespace_id=1, datasource=ds, foreign_keys=fks,
        ds_id=ds.id, referenced_targets={"t_order"},
    )
    rows = list((await test_session.execute(
        sa_select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.candidate_kind == "relationship",
        ),
    )).scalars().all())
    assert len(rows) == 1
    assert rows[0].target == "t_order"


@pytest.mark.asyncio
async def test_idempotent(test_session):
    from app.knowledge.canonical_relationship_writer import (
        write_relationship_candidates_from_foreign_keys,
    )
    ds = DataSource(
        namespace_id=1, db_type="mysql",
        host="h", port=3306, database="db_a", username="u", password="p",
    )
    test_session.add(ds)
    await test_session.flush()
    fks = _fk("t_order", "user_id", "t_user", "id")
    await write_relationship_candidates_from_foreign_keys(
        test_session, namespace_id=1, datasource=ds, foreign_keys=fks, ds_id=ds.id,
    )
    n = await write_relationship_candidates_from_foreign_keys(
        test_session, namespace_id=1, datasource=ds, foreign_keys=fks, ds_id=ds.id,
    )
    assert n == 1  # upsert by value_hash
