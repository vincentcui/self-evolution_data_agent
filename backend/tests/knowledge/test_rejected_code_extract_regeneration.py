"""G9: rejected code_extract KE 被 purge 清后, 同 anchor 重生走 _create_proposed.

spec 2026-05-21-git-source-full-purge 声明:
    清场后该 ns 内 source='code_extract' ∧ repo_id=R 的 rejected KE 全删.
    下一轮 LLM 抽到同 anchor 同 term 时, _find_active_duplicate 无命中,
    走 _create_proposed 分支写新 KE(status='proposed') + audit_log(action='propose').

    这是既有逻辑天然成立, 不需要新代码. 仅做回归保护.
"""

import json

import pytest
from sqlalchemy import func, select

from app.knowledge.terminology_intake import upsert_terminology_with_validation
from app.knowledge.trainer_purge import purge_legacy_for_full_rebuild
from app.models.git_repo import GitRepo
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import DataSource, Namespace
from app.models.terminology_conflict import TerminologyConflict


@pytest.mark.asyncio
async def test_rejected_code_extract_regenerates_as_proposed_after_purge(
    async_session, chroma_isolated,
):
    """purge 清掉 rejected code_extract KE 后, 同 anchor 重抽 → _create_proposed."""
    async with async_session() as db:
        ns = Namespace(name="g9", slug="g9", description="")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        ds = DataSource(
            namespace_id=ns.id, db_type="mongodb",
            host="localhost", port=27017, database="db_g9",
            username="", password="",
        )
        db.add(ds)
        repo = GitRepo(namespace_id=ns.id, url="https://e.com/r.git")
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

        # 种 1 条 rejected code_extract terminology
        payload = {
            "term": "订单", "synonyms": ["order"],
            "primary_collection": "c_orders", "primary_database": "db_g9",
            "db_type": "mongodb", "source_collections": ["c_orders"],
        }
        rejected_ke = KnowledgeEntry(
            namespace_id=ns.id, entry_type="terminology",
            source="code_extract", status="rejected", is_superseded=False,
            payload=json.dumps(payload, ensure_ascii=False),
            content="订单", raw_input="", evidence_json="{}",
            repo_id=repo.id,
        )
        db.add(rejected_ke)
        await db.commit()
        rejected_id = rejected_ke.id

        # purge
        await purge_legacy_for_full_rebuild(db, repo.id, ns.id)
        await db.commit()
        assert await db.get(KnowledgeEntry, rejected_id) is None

        # 重新走闸门 (模拟下一轮 LLM 抽到同 term)
        result = await upsert_terminology_with_validation(
            db, ns_id=ns.id, payload_dict=payload,
            source="code_extract", repo_id=repo.id,
        )
        await db.commit()

        assert result is not None
        assert result.status == "proposed", f"应入 proposed, got {result.status}"
        assert result.source == "code_extract"
        # 验 audit_log 写 propose
        log_row = (await db.execute(
            select(KnowledgeAuditLog).where(
                KnowledgeAuditLog.entry_id == result.id,
                KnowledgeAuditLog.action == "propose",
            )
        )).scalar_one_or_none()
        assert log_row is not None, "应有 propose audit log"


@pytest.mark.asyncio
async def test_rejected_purge_then_regeneration_routes_to_conflict_when_non_code_extract_canonical_exists(
    async_session, chroma_isolated,
):
    """G9 负面路径: purge 后, 若同 anchor 已存在非 code_extract canonical, 重生走 conflict."""
    async with async_session() as db:
        ns = Namespace(name="g9b", slug="g9b", description="")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        ds = DataSource(
            namespace_id=ns.id, db_type="mongodb",
            host="localhost", port=27017, database="db_g9b",
            username="", password="",
        )
        db.add(ds)
        repo = GitRepo(namespace_id=ns.id, url="https://e.com/r.git")
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

        payload = {
            "term": "订单", "synonyms": ["order"],
            "primary_collection": "c_orders", "primary_database": "db_g9b",
            "db_type": "mongodb", "source_collections": ["c_orders"],
        }

        # 种 rejected code_extract KE (将被 purge 删)
        rejected_ke = KnowledgeEntry(
            namespace_id=ns.id, entry_type="terminology",
            source="code_extract", status="rejected", is_superseded=False,
            payload=json.dumps(payload, ensure_ascii=False),
            content="订单", raw_input="", evidence_json="{}",
            repo_id=repo.id,
        )
        # 种 manual canonical 同 anchor (purge 不动它)
        manual_payload = {
            "term": "工单", "synonyms": ["ticket"],
            "primary_collection": "c_orders", "primary_database": "db_g9b",
            "db_type": "mongodb", "source_collections": ["c_orders"],
        }
        manual_canonical = KnowledgeEntry(
            namespace_id=ns.id, entry_type="terminology",
            source="manual", status="canonical", is_superseded=False,
            payload=json.dumps(manual_payload, ensure_ascii=False),
            content="工单", raw_input="manual seed", evidence_json="{}",
        )
        db.add_all([rejected_ke, manual_canonical])
        await db.commit()
        await db.refresh(manual_canonical)
        manual_id = manual_canonical.id

        # purge: 只动 code_extract, manual canonical 保留
        await purge_legacy_for_full_rebuild(db, repo.id, ns.id)
        await db.commit()
        assert await db.get(KnowledgeEntry, manual_id) is not None

        # 重生 (LLM 抽到 '订单', 与 manual '工单' 锚点重叠 lex 不重叠)
        ke_count_before = (await db.execute(
            select(func.count(KnowledgeEntry.id)).where(
                KnowledgeEntry.namespace_id == ns.id
            )
        )).scalar_one()
        result = await upsert_terminology_with_validation(
            db, ns_id=ns.id, payload_dict=payload,
            source="code_extract", repo_id=repo.id,
        )
        await db.commit()

        # 预期: 走 conflict, 不新建 KE
        assert result is None, "应返 None 落 conflict, 不写新 proposed"
        ke_count_after = (await db.execute(
            select(func.count(KnowledgeEntry.id)).where(
                KnowledgeEntry.namespace_id == ns.id
            )
        )).scalar_one()
        assert ke_count_after == ke_count_before, "不应有新 KE 写入"
        conflict = (await db.execute(
            select(TerminologyConflict).where(
                TerminologyConflict.namespace_id == ns.id,
                TerminologyConflict.status == "open",
            )
        )).scalar_one()
        assert conflict.existing_entry_id == manual_id, "conflict 应指向 manual canonical"
