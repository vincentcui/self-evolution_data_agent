"""relationship candidate promotion — 同源字段可指向多个目标。"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select as sa_select

from app.models import (
    DataSource,
    SchemaCanonicalCandidate,
    SchemaCanonicalConflict,
    SchemaCanonicalObject,
)
from app.models.schema_canonical_conflict import build_conflict_scope


@pytest.mark.asyncio
async def test_code_and_fk_same_relationship_merge(test_session):
    """真 writer 产 code+FK 候选, 同关系 → value_hash 同 → merge."""
    from app.knowledge.canonical_promote import promote_candidates_to_canonical
    from app.knowledge.canonical_relationship_writer import (
        write_relationship_candidates_from_foreign_keys,
    )
    from app.knowledge.extraction_writer import _write_relationship_candidate

    ns = 1
    ds = DataSource(
        namespace_id=ns, db_type="mysql",
        host="h", port=3306, database="db_a", username="u", password="p",
    )
    test_session.add(ds)
    await test_session.flush()

    await _write_relationship_candidate(
        test_session, ns, 1,
        from_target="t_order", from_field="user_id",
        to_target="t_user", to_field="id",
        relation_type="many_to_one",
        db_type="mysql", database="db_a",
        source_file="", evidence_source="code_jpa",
        to_db_type="mysql", to_database="db_a",
    )
    fks = [{
        "from_target": "t_order", "from_field": "user_id",
        "to_db_type": "mysql", "to_database": "db_a",
        "to_target": "t_user", "to_field": "id",
        "relation_type": "many_to_one",
    }]
    await write_relationship_candidates_from_foreign_keys(
        test_session, namespace_id=ns, datasource=ds,
        foreign_keys=fks, ds_id=ds.id,
    )
    await test_session.flush()

    rows = list((await test_session.execute(
        sa_select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.candidate_kind == "relationship",
        ),
    )).scalars().all())
    # code+FK 同 value_hash → UPSERT by write_canonical_candidate → 1 row
    assert len(rows) == 1

    report = await promote_candidates_to_canonical(test_session, ns)
    assert report.conflicted_count == 0
    assert report.promoted_count == 1


@pytest.mark.asyncio
async def test_different_targets_coexist(test_session):
    """同一 source field 指向多个目标表时，全部关系都进入 canonical。"""
    from app.knowledge.canonical_promote import promote_candidates_to_canonical
    from app.knowledge.extraction_writer import _write_relationship_candidate

    ns = 2
    relations = [
        ("t_user_role", "one_to_many"),
        ("t_user_token", "one_to_one"),
        ("t_user_textbook", "one_to_many"),
    ]
    for to_target, relation_type in relations:
        await _write_relationship_candidate(
            test_session, ns, 1,
            from_target="t_user", from_field="user_id",
            to_target=to_target, to_field="user_id",
            relation_type=relation_type,
            db_type="mysql", database="db_rp_manage_system",
            source_file="", evidence_source="code_relation",
            to_db_type="mysql", to_database="db_rp_manage_system",
        )
    await test_session.flush()

    report = await promote_candidates_to_canonical(test_session, ns)

    assert report.conflicted_count == 0
    assert report.promoted_count == len(relations)
    sco = (await test_session.execute(
        sa_select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == ns,
            SchemaCanonicalObject.target == "t_user",
        )
    )).scalar_one()
    promoted = json.loads(sco.relationships_json)
    assert {
        (item["to_target"], item["relation_type"])
        for item in promoted
    } == set(relations)


@pytest.mark.asyncio
async def test_legacy_multi_target_conflict_is_reclassified(test_session):
    """旧 field-level relationship conflict 在 promote 时恢复为独立关系。"""
    from app.knowledge.canonical_promote import promote_candidates_to_canonical
    from app.knowledge.extraction_writer import _write_relationship_candidate

    ns = 3
    for to_target in ("t_user_role", "t_user_token"):
        await _write_relationship_candidate(
            test_session, ns, 1,
            from_target="t_user", from_field="user_id",
            to_target=to_target, to_field="user_id",
            relation_type="one_to_many",
            db_type="mysql", database="db_a",
            source_file="", evidence_source="code_relation",
            to_db_type="mysql", to_database="db_a",
        )
    await test_session.flush()
    candidates = list((await test_session.execute(
        sa_select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns,
            SchemaCanonicalCandidate.candidate_kind == "relationship",
        )
    )).scalars().all())
    for candidate in candidates:
        candidate.status = "in_conflict"

    field_key = ("mysql", "db_a", "t_user", "user_id", "relationship")
    conflict = SchemaCanonicalConflict(
        namespace_id=ns,
        db_type="mysql",
        database="db_a",
        target="t_user",
        field_path="user_id",
        candidate_kind="relationship",
        conflict_scope=build_conflict_scope(field_key),
        conflict_type="field_value",
        candidate_ids_json=json.dumps([candidate.id for candidate in candidates]),
        candidates_snapshot_json=json.dumps([
            {"candidate_id": candidate.id, "value": json.loads(candidate.candidate_value_json)}
            for candidate in candidates
        ]),
        status="open",
    )
    test_session.add(conflict)
    await test_session.flush()

    report = await promote_candidates_to_canonical(test_session, ns)

    assert report.promoted_count == 2
    assert conflict.status == "resolved"
    assert all(candidate.status == "active" for candidate in candidates)


@pytest.mark.asyncio
async def test_different_from_fields_coexist(test_session):
    from app.knowledge.canonical_promote import promote_candidates_to_canonical
    from app.knowledge.extraction_writer import _write_relationship_candidate

    ns = 3
    await _write_relationship_candidate(
        test_session, ns, 1,
        from_target="t_user", from_field="default_order_id",
        to_target="t_order", to_field="id",
        relation_type="many_to_one",
        db_type="mysql", database="db_a",
        source_file="", evidence_source="code_jpa",
        to_db_type="mysql", to_database="db_a",
    )
    await _write_relationship_candidate(
        test_session, ns, 1,
        from_target="t_user", from_field="default_payment_id",
        to_target="t_payment", to_field="id",
        relation_type="many_to_one",
        db_type="mysql", database="db_a",
        source_file="", evidence_source="code_jpa",
        to_db_type="mysql", to_database="db_a",
    )
    await test_session.flush()

    report = await promote_candidates_to_canonical(test_session, ns)
    assert report.conflicted_count == 0
    assert report.promoted_count == 2
