"""_write_relationship_candidate — 7键 candidate_value(无source) + relation_type收敛 + 删is_required."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select as sa_select

from app.knowledge.extraction_writer import _write_relationship_candidate
from app.models import SchemaCanonicalCandidate


async def _rel_rows(db):
    rows = list((await db.execute(
        sa_select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.candidate_kind == "relationship",
        ),
    )).scalars().all())
    return rows


@pytest.mark.asyncio
async def test_7_keys_no_source_no_is_required(db_session):
    await _write_relationship_candidate(
        db_session, 1, 1,
        from_target="t_order", from_field="user_id",
        to_target="t_user", to_field="id",
        relation_type="foreign_key",
        db_type="mysql", database="db_a",
        source_file="", evidence_source="code_jpa",
        to_db_type="mysql", to_database="db_a",
    )
    rows = await _rel_rows(db_session)
    val = json.loads(rows[0].candidate_value_json)
    assert val["relation_type"] == "many_to_one"
    assert "is_required" not in val
    assert "source" not in val  # source 不在 candidate_value
    assert val["to_db_type"] == "mysql"
    assert val["to_database"] == "db_a"


@pytest.mark.asyncio
async def test_relation_type_normalized_to_many_to_one(db_session):
    """foreign_key / fk / 空 → 统一为 many_to_one."""
    for raw in ("foreign_key", "fk", ""):
        await _write_relationship_candidate(
            db_session, 1, 1,
            from_target="t", from_field="f",
            to_target="t2", to_field="id",
            relation_type=raw,
            db_type="mysql", database="db",
            source_file="", evidence_source="code",
            to_db_type="mysql", to_database="db",
        )
    rows = await _rel_rows(db_session)
    # 同 value_hash → upsert, 1 row 非 3
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_coll_to_db_fills_target_info(db_session):
    """coll_to_db 命中 → to_db_type/to_database 从反查表取."""
    await _write_relationship_candidate(
        db_session, 1, 1,
        from_target="t_order", from_field="user_id",
        to_target="t_user", to_field="id",
        relation_type="many_to_one",
        db_type="mysql", database="db_a",
        source_file="", evidence_source="code_jpa",
        coll_to_db={"t_user": ("oracle", "remote_db")},
    )
    rows = await _rel_rows(db_session)
    val = json.loads(rows[0].candidate_value_json)
    assert val["to_db_type"] == "oracle"
    assert val["to_database"] == "remote_db"


@pytest.mark.asyncio
async def test_empty_gate_returns_zero(db_session):
    """missing from_target/to_target/database → 早返 0."""
    assert await _write_relationship_candidate(
        db_session, 1, 1,
        from_target="", from_field="f",
        to_target="t2", to_field="id",
        db_type="mysql", database="db",
    ) == 0
    assert await _write_relationship_candidate(
        db_session, 1, 1,
        from_target="t", from_field="f",
        to_target="", to_field="id",
        db_type="mysql", database="db",
    ) == 0
