"""_clean_namespace_mongo_canonical — 全量重建清场扩到 candidate + conflict 三表.

╔══════════════════════════════════════════════════════════════════════════╗
║  背景:                                                                   ║
║    原实现只清 SchemaCanonicalObject. 但 SchemaCanonicalCandidate 用      ║
║    五元组+value_hash UPSERT, 旧 repo 删除的字段产出的孤儿 candidate      ║
║    永不被新一轮解析覆盖, 会污染 promote 9 分支判断, 制造假冲突. 同时     ║
║    SchemaCanonicalConflict 是 candidate 派生物, candidate 清空时必须     ║
║    同清避免悬挂引用.                                                     ║
║                                                                          ║
║  本测验证: 清场后三表 ns 行数全 0, 跨 ns 数据不受影响.                   ║
║                                                                          ║
║  隔离: 使用 conftest db fixture (savepoint rollback), _clean_* 内部      ║
║    commit 被 after_transaction_end 自动重建 savepoint 兜住, 测试结束     ║
║    整体回滚 — 固定 slug 不再残留到下一轮.                                ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api.knowledge import _clean_namespace_mongo_canonical
from app.models import (
    Namespace,
    SchemaCanonicalCandidate,
    SchemaCanonicalConflict,
    SchemaCanonicalObject,
)

pytestmark = pytest.mark.asyncio


# ════════════════════════════════════════════
#  Fixtures — 用 conftest db (savepoint rollback) 替代独立 engine
# ════════════════════════════════════════════

@pytest_asyncio.fixture
async def two_namespaces_with_canonical_data(db) -> tuple[int, int]:
    """两个 ns 各塞 canonical 三表数据, 返回 (target_ns_id, peer_ns_id).

    db fixture 提供 savepoint 隔离: 内部 commit 不真正落库,
    测试结束自动回滚, 固定 slug 永不残留.
    """
    ns_target = Namespace(name="ns_target", slug="ns_target", description="")
    ns_peer = Namespace(name="ns_peer", slug="ns_peer", description="")
    db.add_all([ns_target, ns_peer])
    await db.commit()
    await db.refresh(ns_target)
    await db.refresh(ns_peer)

    for ns_id, prefix in [(ns_target.id, "t"), (ns_peer.id, "p")]:
        # SCO
        db.add(SchemaCanonicalObject(
            namespace_id=ns_id,
            db_type="mongodb",
            database=f"{prefix}_db",
            target=f"{prefix}_coll",
            fields_json="[]",
            description=f"{prefix}-desc",
        ))
        # Candidate (3 条, 不同 hash)
        for i in range(3):
            db.add(SchemaCanonicalCandidate(
                namespace_id=ns_id,
                db_type="mongodb",
                database=f"{prefix}_db",
                target=f"{prefix}_coll",
                field_path=f"f{i}",
                candidate_kind="field_description",
                candidate_value_json=json.dumps({"v": i}),
                value_hash=f"{prefix}_hash_{i}",
                evidence_sources_json="[]",
                status="pending",
                confidence_status="evidence_only",
            ))
        await db.flush()
        cand = (await db.execute(
            select(SchemaCanonicalCandidate).where(
                SchemaCanonicalCandidate.namespace_id == ns_id
            )
        )).scalars().first()
        assert cand is not None
        # Conflict (1 条, 引用首个 candidate)
        db.add(SchemaCanonicalConflict(
            namespace_id=ns_id,
            db_type="mongodb",
            database=f"{prefix}_db",
            target=f"{prefix}_coll",
            field_path="f0",
            candidate_kind="field_description",
            conflict_type="field_value",
            candidate_ids_json=json.dumps([cand.id]),
            candidates_snapshot_json=json.dumps([{"id": cand.id}]),
            status="open",
        ))
    await db.commit()

    return ns_target.id, ns_peer.id


# ════════════════════════════════════════════
#  Tests
# ════════════════════════════════════════════

async def test_clean_namespace_purges_candidates_and_conflicts(
    db, two_namespaces_with_canonical_data,
):
    """全量重建清场三表全清, 跨 ns 不受影响."""
    target_ns, peer_ns = two_namespaces_with_canonical_data

    stats = await _clean_namespace_mongo_canonical(db, target_ns)

    assert stats["schema_canonical_objects"] == 1
    assert stats["schema_canonical_candidates"] == 3
    assert stats["schema_canonical_conflicts"] == 1

    # target ns 三表已清空
    sco_target = (await db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == target_ns,
        )
    )).scalars().all()
    cand_target = (await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == target_ns,
        )
    )).scalars().all()
    conf_target = (await db.execute(
        select(SchemaCanonicalConflict).where(
            SchemaCanonicalConflict.namespace_id == target_ns,
        )
    )).scalars().all()
    assert sco_target == []
    assert cand_target == []
    assert conf_target == []

    # 跨 ns 不受影响
    sco_peer = (await db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == peer_ns,
        )
    )).scalars().all()
    cand_peer = (await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == peer_ns,
        )
    )).scalars().all()
    conf_peer = (await db.execute(
        select(SchemaCanonicalConflict).where(
            SchemaCanonicalConflict.namespace_id == peer_ns,
        )
    )).scalars().all()
    assert len(sco_peer) == 1
    assert len(cand_peer) == 3
    assert len(conf_peer) == 1


async def test_clean_namespace_idempotent_when_empty(db):
    """空 ns 清场不报错, stats 全 0."""
    ns = Namespace(name="ns_empty", slug="ns_empty", description="")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    stats = await _clean_namespace_mongo_canonical(db, ns.id)

    assert stats == {
        "schema_canonical_objects": 0,
        "schema_canonical_candidates": 0,
        "schema_canonical_conflicts": 0,
    }
