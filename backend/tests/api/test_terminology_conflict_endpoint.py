"""Phase 1c Task 1.6 — TerminologyConflict /resolve endpoint 回归.

覆盖 4 种 resolution_choice + 跨 ns 防越权 + 重复解决 409 + 非法 choice 422.
"""

import json
import pytest
from sqlalchemy import select

from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry
from app.models.terminology_conflict import TerminologyConflict


@pytest.mark.asyncio
async def test_keep_existing_marks_conflict_resolved(admin_client, seeded_open_conflict, db):
    ns_id, conflict_id, _existing_id = seeded_open_conflict
    resp = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "keep_existing"},
    )
    assert resp.status_code == 200
    db.expire_all()
    c = await db.get(TerminologyConflict, conflict_id)
    assert c is not None
    assert c.status == "resolved"
    assert c.resolution_choice == "keep_existing"


@pytest.mark.asyncio
async def test_replace_supersedes_existing(admin_client, seeded_open_conflict, db):
    ns_id, conflict_id, existing_id = seeded_open_conflict
    resp = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "replace"},
    )
    assert resp.status_code == 200
    db.expire_all()
    existing = await db.get(KnowledgeEntry, existing_id)
    # I-4 收紧查询: 按 ns_id + status=proposed 而非 id != existing_id 防 fixture 演化误伤
    new_kes = (await db.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.status == "proposed",
            KnowledgeEntry.id != existing_id,
        )
    )).scalars().all()
    logs = (await db.execute(select(KnowledgeAuditLog))).scalars().all()
    assert existing is not None and existing.is_superseded is True
    assert len(new_kes) == 1
    # I-1 candidate 血缘: candidate_source="code_extract" 透传到新 KE 而非写死 manual
    assert new_kes[0].source == "code_extract"
    assert json.loads(new_kes[0].payload)["term"] == "订单"
    assert {l.action for l in logs} >= {"supersede", "propose"}


@pytest.mark.asyncio
async def test_merge_both_unions_synonyms(admin_client, seeded_open_conflict, db):
    ns_id, conflict_id, existing_id = seeded_open_conflict
    resp = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "merge_both"},
    )
    assert resp.status_code == 200
    db.expire_all()
    existing = await db.get(KnowledgeEntry, existing_id)
    assert existing is not None
    payload = json.loads(existing.payload)
    syns = set(payload["synonyms"])
    # candidate.term=订单 + candidate.synonyms=[单子] 应并入 existing.synonyms
    assert syns == {"货品", "订单", "单子"}
    # I-5: merge_both 唯一带 diff_json 的路径, 锁住 before/after/candidate_term 三键
    log_row = (await db.execute(
        select(KnowledgeAuditLog).where(
            KnowledgeAuditLog.entry_id == existing_id,
            KnowledgeAuditLog.action == "merge",
        )
    )).scalar_one()
    diff = json.loads(log_row.diff_json)
    assert "before" in diff and "after" in diff and "candidate_term" in diff
    assert diff["candidate_term"] == "订单"


@pytest.mark.asyncio
async def test_reject_both_marks_existing_rejected(admin_client, seeded_open_conflict, db):
    ns_id, conflict_id, existing_id = seeded_open_conflict
    resp = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "reject_both"},
    )
    assert resp.status_code == 200
    db.expire_all()
    existing = await db.get(KnowledgeEntry, existing_id)
    assert existing is not None
    assert existing.status == "rejected"
    # I-5: reject_both 必须留 audit 痕迹 from_status=proposed → to_status=rejected
    # (fixture 经闸门创建 existing KE = status='proposed', 未经审核晋升 canonical)
    log_row = (await db.execute(
        select(KnowledgeAuditLog).where(
            KnowledgeAuditLog.entry_id == existing_id,
            KnowledgeAuditLog.action == "reject",
        )
    )).scalar_one()
    assert log_row.from_status == "proposed"
    assert log_row.to_status == "rejected"


@pytest.mark.asyncio
async def test_resolve_already_resolved_returns_409(admin_client, seeded_open_conflict):
    ns_id, conflict_id, _ = seeded_open_conflict
    r1 = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "keep_existing"},
    )
    assert r1.status_code == 200
    r2 = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "replace"},
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_invalid_choice_returns_422(admin_client, seeded_open_conflict):
    ns_id, conflict_id, _ = seeded_open_conflict
    resp = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "weird_choice"},
    )
    assert resp.status_code == 422


# ════════════════════════════════════════════
#  manual_edit choice — 就地改 existing → canonical, candidate 丢弃
# ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_manual_edit_overwrites_existing_and_canonicalizes(
    admin_client, seeded_open_conflict, db,
):
    """happy path: term/synonyms 改写 + status 翻 canonical + audit 双轨 (edit + approve)."""
    ns_id, conflict_id, existing_id = seeded_open_conflict
    edited = {
        "term": "精品货",  # 用户改了 term
        "primary_collection": "c_category",  # 路由三元组保持不变
        "primary_database": "db_q",
        "db_type": "mongodb",
        "synonyms": ["商品", "货品", "订单", "单子"],  # existing ∪ candidate 并集预填
        "source_collections": ["c_category"],
    }
    resp = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "manual_edit", "edited_payload": edited},
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()

    existing = await db.get(KnowledgeEntry, existing_id)
    assert existing is not None
    assert existing.status == "canonical"
    payload = json.loads(existing.payload)
    assert payload["term"] == "精品货"
    assert set(payload["synonyms"]) == {"商品", "货品", "订单", "单子"}
    assert existing.content == "精品货"

    # audit 双轨: edit + approve (existing 原 status=proposed)
    logs = (await db.execute(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == existing_id)
    )).scalars().all()
    actions = {l.action for l in logs}
    assert "edit" in actions
    assert "approve" in actions
    edit_log = next(l for l in logs if l.action == "edit")
    diff = json.loads(edit_log.diff_json)
    assert diff["before"]["term"] == "商品"
    assert diff["after"]["term"] == "精品货"


@pytest.mark.asyncio
async def test_manual_edit_routing_change_returns_422(
    admin_client, seeded_open_conflict, db,
):
    """路由三元组任一字段改变 → 422 拒绝跨表迁移."""
    ns_id, conflict_id, _ = seeded_open_conflict
    edited = {
        "term": "精品货",
        "primary_collection": "c_category_other",  # 改了 collection — 应被拒
        "primary_database": "db_q",
        "db_type": "mongodb",
        "synonyms": [],
        "source_collections": [],
    }
    resp = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "manual_edit", "edited_payload": edited},
    )
    assert resp.status_code == 422
    assert "routing" in resp.text.lower()


@pytest.mark.asyncio
async def test_manual_edit_missing_payload_returns_422(
    admin_client, seeded_open_conflict,
):
    """edited_payload 缺失 → 422."""
    ns_id, conflict_id, _ = seeded_open_conflict
    resp = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "manual_edit"},  # 没传 edited_payload
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_manual_edit_skips_approve_audit_when_already_canonical(
    admin_client, seeded_open_conflict, db,
):
    """existing 已 canonical 时 manual_edit 仅写 edit audit, 不重复 approve."""
    ns_id, conflict_id, existing_id = seeded_open_conflict
    # 把 existing 强制翻 canonical 模拟"已审过的术语再起冲突"场景
    existing = await db.get(KnowledgeEntry, existing_id)
    assert existing is not None
    existing.status = "canonical"
    await db.commit()

    edited = {
        "term": "精品货",
        "primary_collection": "c_category",
        "primary_database": "db_q",
        "db_type": "mongodb",
        "synonyms": ["货品"],
        "source_collections": ["c_category"],
    }
    resp = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "manual_edit", "edited_payload": edited},
    )
    assert resp.status_code == 200
    db.expire_all()
    logs = (await db.execute(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == existing_id)
    )).scalars().all()
    actions = [l.action for l in logs]
    # edit 必有, approve 不应再次写 (existing 已 canonical)
    assert actions.count("edit") >= 1
    assert actions.count("approve") == 0


@pytest.mark.asyncio
async def test_cross_namespace_returns_404(
    admin_client, seeded_open_conflict, seeded_other_ns,
):
    other_ns_id = seeded_other_ns
    _, conflict_id, _ = seeded_open_conflict
    resp = await admin_client.post(
        f"/api/namespaces/{other_ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "keep_existing"},
    )
    assert resp.status_code == 404


# ════════════════════════════════════════════
#  Phase 3 Task 3.3 — GET list endpoint
# ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_list_conflicts_returns_open_only(
    admin_client, seeded_open_conflict, db,
):
    ns_id, conflict_id, _ = seeded_open_conflict
    resp = await admin_client.get(
        f"/api/namespaces/{ns_id}/terminology/conflicts",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "conflicts" in body
    assert len(body["conflicts"]) == 1
    row = body["conflicts"][0]
    assert row["id"] == conflict_id
    assert row["namespace_id"] == ns_id
    assert row["status"] == "open"
    assert row["candidate_source"] == "code_extract"
    # candidate_payload 是原样 JSON 字符串, 前端自行 parse
    assert isinstance(row["candidate_payload"], str)
    parsed = json.loads(row["candidate_payload"])
    assert parsed["term"] == "订单"


@pytest.mark.asyncio
async def test_list_conflicts_filters_by_status(
    admin_client, seeded_open_conflict, db,
):
    ns_id, conflict_id, _ = seeded_open_conflict
    # 先 resolve 让 conflict 转 status=resolved
    r1 = await admin_client.post(
        f"/api/namespaces/{ns_id}/terminology/conflicts/{conflict_id}/resolve",
        json={"resolution_choice": "keep_existing"},
    )
    assert r1.status_code == 200
    # default ?status=open 应空
    r_open = await admin_client.get(
        f"/api/namespaces/{ns_id}/terminology/conflicts",
    )
    assert r_open.status_code == 200
    assert r_open.json()["conflicts"] == []
    # ?status=resolved 应找到刚才那一条
    r_res = await admin_client.get(
        f"/api/namespaces/{ns_id}/terminology/conflicts?status=resolved",
    )
    assert r_res.status_code == 200
    rows = r_res.json()["conflicts"]
    assert len(rows) == 1
    assert rows[0]["id"] == conflict_id
    assert rows[0]["status"] == "resolved"
