"""L2 contract: PUT /api/knowledge/{id} with new 5-field example payload → 200."""
import json
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.auth import get_current_user
from app.db.metadata import get_db
from app.models import KnowledgeEntry
from app.models.namespace import Namespace
from app.models.user import User


@pytest.mark.asyncio
async def test_put_example_5field_payload_200(
    db_session,
):
    """New 5-field payload passes parse_payload gate and returns 200."""
    # ── Seed a namespace and proposed example entry ──
    ns = Namespace(name="test-ns", slug="test-ns", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    ke = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="example",
        status="proposed",
        tier="normal",
        content="查询订单",
        created_at=datetime.utcnow(),
        payload=json.dumps({
            "question_pattern": "查询订单",
            "collections": ["shop.orders"],
            "join_keys": [],
            "final_query_plan": None,
            "result_summary": "",
        }),
        source="manual",
    )
    db_session.add(ke)
    await db_session.commit()
    await db_session.refresh(ke)

    # ── Override deps with test user + same session ──
    async def _fake_user():
        return User(id=1, username="admin", role="super_admin", password_hash="x")

    async def _fake_db():
        yield db_session

    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                f"/api/knowledge/{ke.id}",
                json={
                    "content": "查询订单各状态数量",
                    "tier": "normal",
                    "reason": "test contract",
                    "payload": {
                        "question_pattern": "查询订单各状态数量",
                        "collections": ["shop.orders"],
                        "join_keys": [],
                        "final_query_plan": {
                            "steps": [{
                                "db_type": "mysql",
                                "database": "shop",
                                "collection": "orders",
                                "operation": "sql",
                                "query": {"sql": "SELECT status, COUNT(*) FROM orders GROUP BY status"},
                            }],
                        },
                        "result_summary": "按状态分组统计订单数量",
                    },
                },
            )

        assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_put_example_old_payload_compat_200(
    db_session,
):
    """Old payload with question+target_collection+query_json still passes extra='allow'."""
    ns = Namespace(name="test-ns-old", slug="test-ns-old", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    ke = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="example",
        status="proposed",
        tier="normal",
        content="查看各订单状态",
        created_at=datetime.utcnow(),
        payload=json.dumps({
            "question": "查看各订单状态",
            "target_collection": "orders",
            "query_json": {"pipeline": []},
        }),
        source="qmql_history",
    )
    db_session.add(ke)
    await db_session.commit()
    await db_session.refresh(ke)

    async def _fake_user():
        return User(id=1, username="admin", role="super_admin", password_hash="x")

    async def _fake_db():
        yield db_session

    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                f"/api/knowledge/{ke.id}",
                json={
                    "content": "查看各订单状态分布",
                    "tier": "normal",
                    "reason": "test compat",
                    "payload": {
                        "question_pattern": "查看各订单状态分布",
                        "question": "查看各订单状态",
                        "target_collection": "orders",
                        "query_json": {"pipeline": []},
                        "collections": ["shop.orders"],
                        "join_keys": [],
                        "final_query_plan": None,
                        "result_summary": "",
                    },
                },
            )

        assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.clear()
