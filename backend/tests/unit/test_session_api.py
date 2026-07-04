"""Session CRUD API 测试."""
from __future__ import annotations

import pytest

from app.models.session import Session
from app.models.namespace import Namespace


@pytest.mark.asyncio
async def test_create_session_201(make_client, db):
    """创建会话 → 201 + title='新会话' + namespace_id 正确."""
    db.add(Namespace(id=1, name="t1", slug="t1"))
    await db.commit()

    client = await make_client(role="super_admin")
    resp = await client.post("/api/sessions", json={"namespace_id": 1})
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "新会话"
    assert data["namespace_id"] == 1


@pytest.mark.asyncio
async def test_list_sessions_ordered(make_client, db):
    """会话列表按 updated_at DESC 排序."""
    db.add(Namespace(id=2, name="t2", slug="t2"))
    await db.commit()

    # 创建两个会话
    client = await make_client(role="super_admin")
    r1 = await client.post("/api/sessions", json={"namespace_id": 2})
    r2 = await client.post("/api/sessions", json={"namespace_id": 2})
    assert r1.status_code == 201
    assert r2.status_code == 201

    resp = await client.get("/api/sessions", params={"namespace_id": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2
    # 后创建的排在前面
    assert data[0]["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_list_sessions_empty(make_client, db):
    """无会话 → 200 + 空数组."""
    db.add(Namespace(id=3, name="t3", slug="t3"))
    await db.commit()

    client = await make_client(role="super_admin")
    resp = await client.get("/api/sessions", params={"namespace_id": 3})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_rename_session_200(make_client, db):
    """重命名成功 → 200 + title 更新."""
    db.add(Namespace(id=4, name="t4", slug="t4"))
    await db.commit()

    client = await make_client(role="super_admin")
    r = await client.post("/api/sessions", json={"namespace_id": 4})
    sid = r.json()["id"]

    resp = await client.patch(f"/api/sessions/{sid}", json={"title": "新标题"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "新标题"


@pytest.mark.asyncio
async def test_rename_session_empty_title_422(make_client, db):
    """空标题 → 422."""
    db.add(Namespace(id=5, name="t5", slug="t5"))
    await db.commit()

    client = await make_client(role="super_admin")
    r = await client.post("/api/sessions", json={"namespace_id": 5})
    sid = r.json()["id"]

    resp = await client.patch(f"/api/sessions/{sid}", json={"title": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_session_204(make_client, db):
    """删除成功 → 204."""
    db.add(Namespace(id=6, name="t6", slug="t6"))
    await db.commit()

    client = await make_client(role="super_admin")
    r = await client.post("/api/sessions", json={"namespace_id": 6})
    sid = r.json()["id"]

    resp = await client.delete(f"/api/sessions/{sid}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_session_not_found_404(make_client, db):
    """删除不存在的会话 → 404."""
    client = await make_client(role="super_admin")
    resp = await client.delete(
        "/api/sessions/00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_session_other_user_403(make_client, db):
    """删除他人会话 → 403."""
    db.add(Namespace(id=7, name="t7", slug="t7"))
    await db.commit()

    # admin 创建会话
    admin = await make_client(role="super_admin", user_id=1, username="admin")
    r = await admin.post("/api/sessions", json={"namespace_id": 7})
    sid = r.json()["id"]

    # 另一个用户尝试删除
    other = await make_client(role="admin", user_id=2, username="other")
    resp = await other.delete(f"/api/sessions/{sid}")
    assert resp.status_code == 403
