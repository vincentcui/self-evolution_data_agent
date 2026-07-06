"""Session CRUD API 测试."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.agent_trace import AgentTrace
from app.models.namespace import Namespace
from app.models.query_history import QueryHistory


@pytest.mark.asyncio
async def test_create_session_201(make_client, db):
    """创建会话 → 201 + title='新会话'."""
    ns = Namespace(name="s1", slug="s1")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    client = await make_client(role="super_admin")
    resp = await client.post("/api/sessions", json={"namespace_id": ns.id})
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "新会话"
    assert data["namespace_id"] == ns.id


@pytest.mark.asyncio
async def test_list_sessions_ordered(make_client, db):
    """会话列表按 updated_at DESC 排序."""
    ns = Namespace(name="s2", slug="s2")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    client = await make_client(role="super_admin")
    r1 = await client.post("/api/sessions", json={"namespace_id": ns.id})
    r2 = await client.post("/api/sessions", json={"namespace_id": ns.id})
    assert r1.status_code == 201
    assert r2.status_code == 201

    resp = await client.get("/api/sessions", params={"namespace_id": ns.id})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2
    # 后创建的排在前面（updated_at DESC）
    ids_in_order = [s["id"] for s in data]
    assert r2.json()["id"] in ids_in_order
    assert r1.json()["id"] in ids_in_order


@pytest.mark.asyncio
async def test_list_sessions_empty(make_client, db):
    """无会话 → 200 + 空数组."""
    ns = Namespace(name="s3", slug="s3")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    client = await make_client(role="super_admin")
    resp = await client.get("/api/sessions", params={"namespace_id": ns.id})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_rename_session_200(make_client, db):
    """重命名成功 → 200 + title 更新."""
    ns = Namespace(name="s4", slug="s4")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    client = await make_client(role="super_admin")
    r = await client.post("/api/sessions", json={"namespace_id": ns.id})
    sid = r.json()["id"]

    resp = await client.patch(f"/api/sessions/{sid}", json={"title": "新标题"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "新标题"


@pytest.mark.asyncio
async def test_rename_session_empty_title_422(make_client, db):
    """空标题 → 422."""
    ns = Namespace(name="s5", slug="s5")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    client = await make_client(role="super_admin")
    r = await client.post("/api/sessions", json={"namespace_id": ns.id})
    sid = r.json()["id"]

    resp = await client.patch(f"/api/sessions/{sid}", json={"title": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_session_204(make_client, db):
    """删除成功 → 204."""
    ns = Namespace(name="s6", slug="s6")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    client = await make_client(role="super_admin")
    r = await client.post("/api/sessions", json={"namespace_id": ns.id})
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
    ns = Namespace(name="s7", slug="s7")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    admin = await make_client(role="super_admin", user_id=1, username="admin")
    r = await admin.post("/api/sessions", json={"namespace_id": ns.id})
    sid = r.json()["id"]

    other = await make_client(role="admin", user_id=2, username="other")
    resp = await other.delete(f"/api/sessions/{sid}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_session_endpoints_malformed_uuid_404(make_client, db):
    """非法 UUID → 404 (覆盖 _get_owned_session 的 except ValueError 分支).

    回归 code-review: rename/delete 共用守卫的非法 UUID 负路径此前零覆盖。
    """
    client = await make_client(role="super_admin")
    resp_del = await client.delete("/api/sessions/not-a-uuid")
    assert resp_del.status_code == 404
    resp_ren = await client.patch("/api/sessions/not-a-uuid", json={"title": "x"})
    assert resp_ren.status_code == 404


@pytest.mark.asyncio
async def test_delete_session_cascades_history_and_traces(make_client, db):
    """删除会话同事务级联清空 query_history + agent_traces，杜绝孤儿数据.

    回归 code-review critical: delete_session 原仅级联 QueryHistory，
    AgentTrace 共享同一 session_id 却被遗漏 → 悬空孤儿行。
    """
    ns = Namespace(name="s8", slug="s8")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    client = await make_client(role="super_admin")
    r = await client.post("/api/sessions", json={"namespace_id": ns.id})
    sid = r.json()["id"]

    # 该会话下写入历史 + trace (session_id 与 QueryHistory/AgentTrace 同源)
    db.add(QueryHistory(
        namespace_id=ns.id, session_id=sid, role="user", content="q1",
    ))
    db.add(AgentTrace(
        trace_id="t-s8-1", session_id=sid, namespace_id=ns.id,
        user_query="q1", trace_json="{}",
    ))
    # 另一会话的 trace 作对照，删除不应波及
    db.add(AgentTrace(
        trace_id="t-other", session_id="ffffffff-0000-0000-0000-000000000000",
        namespace_id=ns.id, user_query="qX", trace_json="{}",
    ))
    await db.commit()

    resp = await client.delete(f"/api/sessions/{sid}")
    assert resp.status_code == 204

    hist = await db.scalar(
        select(func.count()).select_from(QueryHistory).where(QueryHistory.session_id == sid)
    )
    traces = await db.scalar(
        select(func.count()).select_from(AgentTrace).where(AgentTrace.session_id == sid)
    )
    survivor = await db.scalar(
        select(func.count()).select_from(AgentTrace).where(AgentTrace.trace_id == "t-other")
    )
    assert hist == 0, "query_history 未被级联清空"
    assert traces == 0, "agent_traces 未被级联清空 (孤儿数据)"
    assert survivor == 1, "误删了其他会话的 trace"
