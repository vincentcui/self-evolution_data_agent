"""Phase 1b Task 1.3 — 5 通道闸门 + audit_log action 区分."""

import pytest
from sqlalchemy import select

from app.knowledge.terminology_intake import upsert_terminology_with_validation
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry

VALID_PAYLOAD = {
    "term": "商品", "primary_collection": "c_category",
    "primary_database": "db_q", "db_type": "mongodb",
    "synonyms": ["货品"], "source_collections": ["c_category"],
}


@pytest.mark.parametrize("source", ["manual", "agent_learn", "schema", "code_extract"])
@pytest.mark.asyncio
async def test_valid_payload_inserts_proposed(
    async_session, seeded_ns_with_mongo_ds, source,
):
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=VALID_PAYLOAD, source=source,
        )
        await db.commit()
    assert ke is not None
    assert ke.entry_type == "terminology"
    assert ke.status == "proposed"
    assert ke.source == source


@pytest.mark.asyncio
async def test_invalid_payload_returns_none_no_insert(
    async_session, seeded_ns_with_mongo_ds,
):
    ns_id, _ = seeded_ns_with_mongo_ds
    bad = {**VALID_PAYLOAD, "term": "字段枚举值: 0=draft, 1=published, 2=archived"}
    async with async_session() as db:
        ke = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=bad, source="code_extract",
        )
        rows = (await db.execute(select(KnowledgeEntry))).scalars().all()
    assert ke is None and len(rows) == 0


@pytest.mark.asyncio
async def test_db_type_mismatch_rejected(async_session, seeded_ns_with_mongo_ds):
    ns_id, _ = seeded_ns_with_mongo_ds
    bad = {**VALID_PAYLOAD, "db_type": "mysql"}  # ns 下 db_q 是 mongodb
    async with async_session() as db:
        ke = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=bad, source="code_extract",
        )
    assert ke is None


@pytest.mark.asyncio
async def test_database_not_in_namespace_rejected(async_session, seeded_ns_with_mongo_ds):
    ns_id, _ = seeded_ns_with_mongo_ds
    bad = {**VALID_PAYLOAD, "primary_database": "db_unknown"}
    async with async_session() as db:
        ke = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=bad, source="code_extract",
        )
    assert ke is None


@pytest.mark.asyncio
async def test_audit_log_propose_for_manual(async_session, seeded_ns_with_mongo_ds):
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = await upsert_terminology_with_validation(
            db, ns_id=ns_id,
            payload_dict=VALID_PAYLOAD, source="manual",
        )
        await db.commit()
        log_row = (await db.execute(
            select(KnowledgeAuditLog).where(
                KnowledgeAuditLog.entry_id == ke.id,
                KnowledgeAuditLog.action == "propose",
            )
        )).scalar_one()
    assert log_row.to_status == "proposed"


@pytest.mark.asyncio
async def test_audit_log_propose_for_code_extract(async_session, seeded_ns_with_mongo_ds):
    ns_id, repo_id = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = await upsert_terminology_with_validation(
            db, ns_id=ns_id,
            payload_dict=VALID_PAYLOAD, source="code_extract", repo_id=repo_id,
        )
        await db.commit()
        log_row = (await db.execute(
            select(KnowledgeAuditLog).where(
                KnowledgeAuditLog.entry_id == ke.id,
                KnowledgeAuditLog.action == "propose",
            )
        )).scalar_one()
    assert log_row.to_status == "proposed"


# ════════════════════════════════════════════
#  G3 — canonical 一律不合并, 走 conflict (spec 2026-05-21)
# ════════════════════════════════════════════
import json

from app.models.terminology_conflict import TerminologyConflict


@pytest.mark.parametrize("non_code_extract_source", ["manual", "agent_learn", "schema"])
@pytest.mark.asyncio
async def test_canonical_4_sources_route_to_conflict_on_overlap(
    async_session, seeded_ns_with_mongo_ds, non_code_extract_source,
):
    """G3: 非 code_extract canonical + code_extract 候选 lex 重叠 → 落 conflict, 不合并."""
    ns_id, repo_id = seeded_ns_with_mongo_ds
    async with async_session() as db:
        # 种一条 canonical(source=非 code_extract), term='商品' synonyms=['货品']
        payload = {
            "term": "商品", "synonyms": ["货品"],
            "primary_collection": "c_category", "primary_database": "db_q",
            "db_type": "mongodb", "source_collections": ["c_category"],
        }
        canonical_ke = KnowledgeEntry(
            namespace_id=ns_id,
            entry_type="terminology",
            source=non_code_extract_source,
            status="canonical",
            is_superseded=False,
            payload=json.dumps(payload, ensure_ascii=False),
            content="商品",
            raw_input="seed",
            evidence_json="{}",
        )
        db.add(canonical_ke)
        await db.commit()
        await db.refresh(canonical_ke)
        canonical_id = canonical_ke.id

        # code_extract 候选 lex 与 canonical 重叠 (含 '货品')
        candidate = {
            "term": "货品", "synonyms": ["货物"],
            "primary_collection": "c_category", "primary_database": "db_q",
            "db_type": "mongodb", "source_collections": ["c_category"],
        }
        result = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=candidate,
            source="code_extract", repo_id=repo_id,
        )
        await db.commit()

        # 期望: 落 conflict 表, canonical synonyms 未动
        assert result is None, "canonical 不应被合并, 应返 None 落 conflict"
        conflict = (await db.execute(
            select(TerminologyConflict).where(
                TerminologyConflict.namespace_id == ns_id,
                TerminologyConflict.status == "open",
            )
        )).scalar_one_or_none()
        assert conflict is not None, f"source={non_code_extract_source} canonical 未触发 conflict"

        await db.refresh(canonical_ke)
        after_payload = json.loads(canonical_ke.payload)
        assert after_payload["synonyms"] == ["货品"], \
            f"canonical synonyms 被改动, before=['货品'], after={after_payload['synonyms']}"
