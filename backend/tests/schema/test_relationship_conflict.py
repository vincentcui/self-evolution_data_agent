"""from_field conflict — 真 writer + 真 promote."""
from __future__ import annotations

import pytest
from sqlalchemy import select as sa_select

from app.models import DataSource, SchemaCanonicalCandidate


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
async def test_different_target_creates_conflict(test_session):
    from app.knowledge.canonical_promote import promote_candidates_to_canonical
    from app.knowledge.canonical_relationship_writer import (
        write_relationship_candidates_from_foreign_keys,
    )
    from app.knowledge.extraction_writer import _write_relationship_candidate

    ns = 2
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
        "to_target": "t_user_v2", "to_field": "id",
        "relation_type": "many_to_one",
    }]
    await write_relationship_candidates_from_foreign_keys(
        test_session, namespace_id=ns, datasource=ds,
        foreign_keys=fks, ds_id=ds.id,
    )
    await test_session.flush()

    report = await promote_candidates_to_canonical(test_session, ns)
    assert report.conflicted_count == 1


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
