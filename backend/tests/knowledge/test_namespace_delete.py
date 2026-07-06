"""Stage 2 Task 4 — DELETE namespace 走 BulkOpGuard + confirm_token e2e.

验收三态:
    1. dry_run=True   → 200 NamespaceDeletePreview, 数据不动
    2. 超阈值缺/错 token → 422 含 expected_token + affected_count
    3. 超阈值正确 token → 204, ns + 关联 KE 全部删除
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.namespace import _compute_confirm_token, router
from app.auth import get_current_user
from app.config import settings
from app.db.metadata import get_db
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace
from app.models.user import User


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(db_session) -> User:
    user = User(
        username="admin_test",
        password_hash="x",
        role="super_admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def client(
    db_session, admin_user, chroma_isolated
) -> AsyncGenerator[AsyncClient, None]:
    """ASGI 客户端 + db_session/admin override."""
    app = FastAPI()
    app.include_router(router)

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ─────────────────────────────────────────────────────────────────
# Case 1: dry_run=True → preview, 数据不动
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_returns_protected_count(client, db_session):
    ns = Namespace(name="t1", slug="t1", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    db_session.add_all(
        [
            KnowledgeEntry(
                namespace_id=ns.id,
                entry_type="schema_summary",
                content="x",
                source="code_extract",
                status="canonical",
                tier="normal",
            ),
            KnowledgeEntry(
                namespace_id=ns.id,
                entry_type="terminology",
                content="y",
                source="manual",
                status="canonical",
                tier="normal",
            ),
        ]
    )
    await db_session.commit()

    resp = await client.delete(f"/api/namespaces/{ns.id}?dry_run=true")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["op_name"] == "namespace_delete"
    assert body["affected_count"] == 2
    assert body["by_source"] == {"code_extract": 1, "manual": 1}
    assert body["by_entry_type"]["schema_summary"] == 1
    assert body["by_entry_type"]["terminology"] == 1
    assert body["confirm_required"] is False  # 2 ≤ 默认阈值 100

    # 数据未动
    rows = (await db_session.scalars(select(KnowledgeEntry))).all()
    assert len(rows) == 2
    assert (await db_session.scalar(select(Namespace).where(Namespace.id == ns.id))) is not None


# ─────────────────────────────────────────────────────────────────
# Case 2: 超阈值缺/错 token → 422
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_above_threshold_requires_confirm_token(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "bulk_op_require_confirm_above", 1)

    ns = Namespace(name="t2", slug="t2", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    db_session.add_all(
        [
            KnowledgeEntry(
                namespace_id=ns.id,
                entry_type="schema_summary",
                content=f"x{i}",
                source="code_extract",
                status="canonical",
                tier="normal",
            )
            for i in range(3)
        ]
    )
    await db_session.commit()

    # 缺 token
    resp = await client.delete(f"/api/namespaces/{ns.id}?dry_run=false")
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "confirm_token_required"
    assert detail["affected_count"] == 3
    expected = detail["expected_token"]
    assert expected == _compute_confirm_token(ns.id, 3)

    # 错 token
    resp_bad = await client.delete(
        f"/api/namespaces/{ns.id}?dry_run=false&confirm_token=wrong"
    )
    assert resp_bad.status_code == 422, resp_bad.text
    assert resp_bad.json()["detail"]["error"] == "confirm_token_mismatch"

    # 数据仍未动
    kes = (await db_session.scalars(
        select(KnowledgeEntry).where(KnowledgeEntry.namespace_id == ns.id)
    )).all()
    assert len(kes) == 3


# ─────────────────────────────────────────────────────────────────
# Case 3: 正确 token → 204, ns + KE 全部删除
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_correct_token_executes_delete(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "bulk_op_require_confirm_above", 1)

    ns = Namespace(name="t3", slug="t3", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    db_session.add_all(
        [
            KnowledgeEntry(
                namespace_id=ns.id,
                entry_type="schema_summary",
                content=f"y{i}",
                source="code_extract",
                status="canonical",
                tier="normal",
            )
            for i in range(3)
        ]
    )
    await db_session.commit()

    expected_token = _compute_confirm_token(ns.id, 3)
    resp = await client.delete(
        f"/api/namespaces/{ns.id}?dry_run=false&confirm_token={expected_token}"
    )
    assert resp.status_code == 204, resp.text

    # ns + KE 全删
    assert (
        await db_session.scalar(select(Namespace).where(Namespace.id == ns.id))
    ) is None
    kes = (await db_session.scalars(
        select(KnowledgeEntry).where(KnowledgeEntry.namespace_id == ns.id)
    )).all()
    assert len(kes) == 0
