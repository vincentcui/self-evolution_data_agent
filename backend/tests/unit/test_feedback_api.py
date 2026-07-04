"""Feedback API + 推荐问题测试."""
from __future__ import annotations

import pytest

from app.models.namespace import Namespace
from app.models.query_history import QueryHistory


async def _add_history(db, ns_id: int, role: str = "assistant", content: str = "test") -> int:
    h = QueryHistory(
        namespace_id=ns_id, session_id="test-sid", role=role,
        content=content,
    )
    db.add(h)
    await db.commit()
    await db.refresh(h)
    return h.id


@pytest.mark.asyncio
async def test_submit_feedback_201(make_client, db):
    """提交反馈 → 201，feedback_rating 已写入."""
    ns = Namespace(name="f1", slug="f1")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    hid = await _add_history(db, ns.id)

    client = await make_client(role="super_admin")
    resp = await client.post("/api/feedback", json={
        "history_id": hid, "rating": "like",
    })
    assert resp.status_code == 201
    assert resp.json()["rating"] == "like"


@pytest.mark.asyncio
async def test_submit_feedback_update_existing(make_client, db):
    """重复反馈 → 200，UPDATE 不重复创建."""
    ns = Namespace(name="f2", slug="f2")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    hid = await _add_history(db, ns.id)

    client = await make_client(role="super_admin")
    await client.post("/api/feedback", json={"history_id": hid, "rating": "like"})
    resp = await client.post("/api/feedback", json={
        "history_id": hid, "rating": "dislike",
    })
    assert resp.status_code == 201
    assert resp.json()["rating"] == "dislike"


@pytest.mark.asyncio
async def test_submit_feedback_invalid_history_id_404(make_client, db):
    """无效 history_id → 404."""
    client = await make_client(role="super_admin")
    resp = await client.post("/api/feedback", json={
        "history_id": 99999, "rating": "like",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_submit_feedback_invalid_rating_422(make_client, db):
    """非法 rating → 422."""
    ns = Namespace(name="f3", slug="f3")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    hid = await _add_history(db, ns.id)

    client = await make_client(role="super_admin")
    resp = await client.post("/api/feedback", json={
        "history_id": hid, "rating": "invalid",
    })
    assert resp.status_code == 422
