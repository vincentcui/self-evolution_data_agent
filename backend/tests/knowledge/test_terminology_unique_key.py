"""Phase 1b Task 1.3 — 唯一键 + 双向同义匹配 + canonical 保护 + superseded 排除."""

import json

import pytest
from sqlalchemy import select

from app.knowledge.terminology_intake import upsert_terminology_with_validation
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry
from app.models.terminology_conflict import TerminologyConflict


def _payload(term: str, syns: list[str]) -> dict:
    return {
        "term": term, "primary_collection": "c_category",
        "primary_database": "db_q", "db_type": "mongodb",
        "synonyms": syns, "source_collections": ["c_category"],
    }


@pytest.mark.asyncio
async def test_same_term_merges_synonyms(async_session, seeded_ns_with_mongo_ds):
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("商品", ["货品"]), source="code_extract",
        )
        await db.commit()
        await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("商品", ["货本"]), source="manual",
        )
        await db.commit()
        ke = (await db.execute(select(KnowledgeEntry))).scalar_one()
        merge_log = (await db.execute(
            select(KnowledgeAuditLog).where(KnowledgeAuditLog.action == "merge")
        )).scalar_one()
    assert sorted(json.loads(ke.payload)["synonyms"]) == ["货品", "货本"]
    assert "shared_terms" in merge_log.diff_json


@pytest.mark.asyncio
async def test_candidate_term_in_existing_synonyms_merges(
    async_session, seeded_ns_with_mongo_ds,
):
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("商品", ["货品", "存货"]), source="code_extract",
        )
        await db.commit()
        # candidate term=货品 ∈ existing.synonyms → 合并
        await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("货品", ["货本"]), source="manual",
        )
        await db.commit()
        ke = (await db.execute(select(KnowledgeEntry))).scalar_one()
    syns = json.loads(ke.payload)["synonyms"]
    assert "货本" in syns and "货品" in syns and "存货" in syns
    assert json.loads(ke.payload)["term"] == "商品"  # term 不变


@pytest.mark.asyncio
async def test_synonyms_intersection_merges(async_session, seeded_ns_with_mongo_ds):
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("商品", ["货品"]), source="code_extract",
        )
        await db.commit()
        # candidate term=存货, syn=[货品, 货本] — synonyms 交集 {货品} 非空
        await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("存货", ["货品", "货本"]), source="manual",
        )
        await db.commit()
        ke = (await db.execute(select(KnowledgeEntry))).scalar_one()
    syns = json.loads(ke.payload)["synonyms"]
    assert set(syns) >= {"货品", "货本", "存货"}


@pytest.mark.asyncio
async def test_no_intersection_creates_conflict(
    async_session, seeded_ns_with_mongo_ds,
):
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("商品", ["货品"]), source="code_extract",
        )
        await db.commit()
        # 完全无交集
        ret = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("订单", ["单子"]), source="code_extract",
        )
        await db.commit()
    assert ret is None
    async with async_session() as db:
        kes = (await db.execute(select(KnowledgeEntry))).scalars().all()
        confs = (await db.execute(select(TerminologyConflict))).scalars().all()
    assert len(kes) == 1
    assert len(confs) == 1 and confs[0].status == "open"


@pytest.mark.asyncio
async def test_canonical_protection_skips_git_extractor(
    async_session, seeded_ns_with_mongo_ds,
):
    """canonical + git source → 走 conflict, synonyms 不变."""
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("商品", ["货品"]), source="code_extract",
        )
        await db.commit()
        assert ke is not None
        ke.status = "canonical"
        await db.commit()
        # source=git + canonical → 走 conflict, syns 不变
        result = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("商品", ["货本"]), source="code_extract",
        )
        await db.commit()
        loaded = (await db.execute(select(KnowledgeEntry))).scalar_one()
        merge_logs = (await db.execute(
            select(KnowledgeAuditLog).where(KnowledgeAuditLog.action == "merge")
        )).scalars().all()
        confs = (await db.execute(select(TerminologyConflict))).scalars().all()
    assert result is None, "canonical 应返 None 落 conflict"
    assert json.loads(loaded.payload)["synonyms"] == ["货品"]
    assert merge_logs == []
    assert len(confs) == 1 and confs[0].status == "open"


@pytest.mark.asyncio
async def test_canonical_routes_to_conflict_regardless_of_source(async_session, seeded_ns_with_mongo_ds):
    """G3: canonical 一律不合并, 含 manual source — 走 conflict."""
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("商品", ["货品"]), source="code_extract",
        )
        await db.commit()
        assert ke is not None
        ke.status = "canonical"
        await db.commit()
        # source=manual + canonical → 走 conflict, 不合并
        result = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("商品", ["货本"]), source="manual",
        )
        await db.commit()
        loaded = (await db.execute(select(KnowledgeEntry))).scalar_one()
        confs = (await db.execute(select(TerminologyConflict))).scalars().all()
    assert result is None, "canonical 不应被合并, 应返 None 落 conflict"
    assert json.loads(loaded.payload)["synonyms"] == ["货品"], "canonical synonyms 不应被改动"
    assert len(confs) == 1 and confs[0].status == "open"


@pytest.mark.asyncio
async def test_superseded_excluded_from_unique_key(
    async_session, seeded_ns_with_mongo_ds,
):
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("商品", ["货品"]), source="code_extract",
        )
        await db.commit()
        assert ke is not None
        ke.is_superseded = True
        await db.commit()
        # superseded=True 不参与唯一键, 新 candidate 直插 (不冲突)
        new_ke = await upsert_terminology_with_validation(
            db, ns_id=ns_id, payload_dict=_payload("货本", ["促销"]), source="manual",
        )
        await db.commit()
    assert new_ke is not None
    assert new_ke.id != ke.id
