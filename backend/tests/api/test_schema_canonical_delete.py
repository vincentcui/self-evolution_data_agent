"""schema_canonical DELETE 端点测试 — 正常/404/403/candidate回退/conflict关闭/audit快照."""

import json
import secrets

import pytest
from sqlalchemy import select

from app.models.namespace import Namespace
from app.models.schema_canonical_audit_log import SchemaCanonicalAuditLog
from app.models.schema_canonical_candidate import SchemaCanonicalCandidate
from app.models.schema_canonical_conflict import SchemaCanonicalConflict
from app.models.schema_canonical_object import SchemaCanonicalObject
from app.models.user import User


async def _make_user(db, username: str, role: str = "super_admin") -> int:
    from app.auth import hash_password
    u = User(username=username, role=role, password_hash=hash_password("test123"))
    db.add(u)
    await db.flush()
    return u.id


async def _make_sco(db, ns_id: int, target: str = "test_table") -> int:
    sco = SchemaCanonicalObject(
        namespace_id=ns_id, db_type="mysql", database="test_db", target=target,
        fields_json='[{"name":"id","type":"int"}]', indexes_json="[]",
        description="测试表", purpose_detail="for delete test",
        reviewed=True, sample_count=100, source="introspect",
        user_locked=False, relationships_json="[]", sample_values_json="[]",
    )
    db.add(sco)
    await db.flush()
    return sco.id


async def _make_candidates(db, ns_id: int, target: str = "test_table"):
    """创建 3 种非终态候选: active×2 + pending×1 + in_conflict×1, 返回各自 ID 列表."""
    cands: dict[str, list[int]] = {"active": [], "pending": [], "in_conflict": []}

    # active
    for fname, h in [("id", "h1"), ("name", "h2")]:
        c = SchemaCanonicalCandidate(
            namespace_id=ns_id, db_type="mysql", database="test_db",
            target=target, field_path=fname, candidate_kind="field_description",
            candidate_value_json=f'{{"description":"{fname}字段"}}',
            value_hash=h, evidence_sources_json="[]",
            status="active", confidence_status="confirmed_by_introspect",
            generation=0,
        )
        db.add(c)
        cands["active"].append(c)
    # pending
    c = SchemaCanonicalCandidate(
        namespace_id=ns_id, db_type="mysql", database="test_db",
        target=target, field_path="remark", candidate_kind="field_description",
        candidate_value_json='{"description":"备注(待审)"}',
        value_hash="pending_1", evidence_sources_json="[]",
        status="pending", confidence_status="unverified",
        generation=0,
    )
    db.add(c)
    cands["pending"].append(c)
    await db.flush()
    # in_conflict — 需要一个冲突记录来引用它
    conflict_cand = SchemaCanonicalCandidate(
        namespace_id=ns_id, db_type="mysql", database="test_db",
        target=target, field_path="status", candidate_kind="field_description",
        candidate_value_json='{"description":"冲突候选A"}',
        value_hash="conflict_a", evidence_sources_json="[]",
        status="in_conflict", confidence_status="evidence_only",
        generation=0,
    )
    db.add(conflict_cand)
    cands["in_conflict"].append(conflict_cand)
    await db.flush()
    return cands, conflict_cand


async def _make_conflict(db, ns_id: int, target: str = "test_table",
                          conflict_cand_id: int | None = None):
    ids = [9999, conflict_cand_id] if conflict_cand_id else [1, 2]
    c = SchemaCanonicalConflict(
        namespace_id=ns_id, db_type="mysql", database="test_db",
        target=target, field_path="status", candidate_kind="field_description",
        conflict_scope="schema-canonical-delete-fixture",
        conflict_type="field_value",
        candidate_ids_json=json.dumps(ids),
        candidates_snapshot_json="[]", status="open",
    )
    db.add(c)
    await db.flush()
    return c


async def _make_ns(db, suffix: str = "") -> int:
    s = suffix or secrets.token_hex(4)
    ns = Namespace(name=f"del-test-{s}", slug=f"del-test-{s}")
    db.add(ns)
    await db.flush()
    return ns.id


# ════════════════════════════════════════════
#  正常删除
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_sco_ok(make_client, db):
    """admin 删 SCO: ok + SCO 消失 + 所有非终态 candidate→orphaned + audit 完整."""
    uid = await _make_user(db, "del_admin")
    ns_id = await _make_ns(db, "ok")
    sco_id = await _make_sco(db, ns_id)
    cands_data, conflict_cand = await _make_candidates(db, ns_id)
    await _make_conflict(db, ns_id, conflict_cand_id=conflict_cand.id)
    await db.commit()

    client = await make_client(role="super_admin", user_id=uid, username="del_admin")
    resp = await client.delete(f"/api/namespaces/{ns_id}/schema-canonical/{sco_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "deleted_id": sco_id}

    # SCO 不存在
    assert await db.get(SchemaCanonicalObject, sco_id) is None

    # 所有非终态 candidate → orphaned (表已删, 不再可 promote)
    cands = (await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.target == "test_table",
        )
    )).scalars().all()
    assert len(cands) == 4, (
        "expected 4 candidates (2 active + 1 pending + 1 in_conflict), "
        f"got {len(cands)}"
    )
    assert all(c.status == "orphaned" for c in cands)

    # conflict 关闭
    confs = (await db.execute(
        select(SchemaCanonicalConflict).where(
            SchemaCanonicalConflict.namespace_id == ns_id,
            SchemaCanonicalConflict.target == "test_table",
        )
    )).scalars().all()
    assert len(confs) == 1
    assert confs[0].status == "resolved"
    assert confs[0].resolved_at is not None

    # audit 完整
    logs = (await db.execute(
        select(SchemaCanonicalAuditLog).where(
            SchemaCanonicalAuditLog.namespace_id == ns_id,
            SchemaCanonicalAuditLog.action == "canonical_deleted",
        )
    )).scalars().all()
    assert len(logs) == 1
    before = json.loads(logs[0].before_json or "{}")
    assert before["database"] == "test_db"
    assert before["target"] == "test_table"
    assert before["reviewed"] is True
    assert before["user_locked"] is False
    assert before["created_at"] is not None


# ════════════════════════════════════════════
#  异常
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_sco_not_found(make_client, db):
    """不存在的 SCO → 404."""
    uid = await _make_user(db, "del_nf")
    ns_id = await _make_ns(db, "nf")
    await db.commit()
    client = await make_client(role="super_admin", user_id=uid)
    resp = await client.delete(f"/api/namespaces/{ns_id}/schema-canonical/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_sco_wrong_ns(make_client, db):
    """SCO 属于 ns A, 用 ns B 删 → 404."""
    uid = await _make_user(db, "del_wn")
    ns_a = await _make_ns(db, "a")
    ns_b = await _make_ns(db, "b")
    sco_id = await _make_sco(db, ns_a, target="only_in_a")
    await db.commit()

    client = await make_client(role="super_admin", user_id=uid)
    resp = await client.delete(f"/api/namespaces/{ns_b}/schema-canonical/{sco_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_sco_non_admin_403(make_client, db):
    """普通 user 删除 → 403."""
    uid = await _make_user(db, "del_user", role="user")
    ns_id = await _make_ns(db, "403")
    sco_id = await _make_sco(db, ns_id)
    await db.commit()

    client = await make_client(role="user", user_id=uid, username="del_user")
    resp = await client.delete(f"/api/namespaces/{ns_id}/schema-canonical/{sco_id}")
    assert resp.status_code == 403
