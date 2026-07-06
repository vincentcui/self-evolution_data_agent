"""Phase 1c Task 1.6 — terminology conflict endpoint 专用 fixtures."""

import json

import pytest_asyncio

from app.knowledge.terminology_intake import upsert_terminology_with_validation
from app.models.namespace import DataSource, Namespace
from app.models.terminology_conflict import TerminologyConflict
from app.models.user import User


VALID_EXISTING = {
    "term": "商品", "primary_collection": "c_category",
    "primary_database": "db_q", "db_type": "mongodb",
    "synonyms": ["货品"], "source_collections": ["c_category"],
}
CANDIDATE_PAYLOAD = {
    "term": "订单", "primary_collection": "c_category",
    "primary_database": "db_q", "db_type": "mongodb",
    "synonyms": ["单子"], "source_collections": ["c_category"],
}


async def _ensure_admin_row(db) -> None:
    """conftest._fake_admin 仅返实例; FK(actor_id) 要求 users 表存在 id=1 行."""
    existing = await db.get(User, 1)
    if existing is None:
        db.add(User(id=1, username="admin", role="admin", password_hash="x"))
        await db.commit()


@pytest_asyncio.fixture
async def seeded_open_conflict(db) -> tuple[int, int, int]:
    """种 1 ns + 1 mongodb DataSource + 1 canonical KE + 1 open TerminologyConflict.

    返回 (ns_id, conflict_id, existing_entry_id).

    流程: 先用 upsert_terminology_with_validation 落 1 条术语 KE (existing),
    再手动 insert 1 条 TerminologyConflict (status='open') 指向该 KE,
    candidate_payload 与 existing 在 (collection, database, db_type) 三元组重合
    但 lex 不交集 (term=订单, syns=[单子] 与 existing.{商品,货品} 互不相交).
    """
    await _ensure_admin_row(db)

    ns = Namespace(name="conflict_test", slug="conflict_test", description="task 1.6")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    db.add(DataSource(
        namespace_id=ns.id, db_type="mongodb", database="db_q",
        host="localhost", port=27017, username="", password="",
    ))
    await db.commit()

    existing = await upsert_terminology_with_validation(
        db, ns_id=ns.id, payload_dict=VALID_EXISTING, source="manual",
    )
    assert existing is not None
    await db.commit()

    conflict = TerminologyConflict(
        namespace_id=ns.id,
        existing_entry_id=existing.id,
        candidate_payload=json.dumps(CANDIDATE_PAYLOAD),
        candidate_source="code_extract",
        status="open",
    )
    db.add(conflict)
    await db.commit()
    await db.refresh(conflict)

    return ns.id, conflict.id, existing.id


@pytest_asyncio.fixture
async def seeded_other_ns(db) -> int:
    """另一个 ns 给跨 ns 越权测试用."""
    ns = Namespace(name="other_ns", slug="other_ns", description="cross-ns test")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    return ns.id
