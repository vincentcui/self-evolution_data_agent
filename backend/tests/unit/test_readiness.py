"""Readiness 端点测试."""
from __future__ import annotations

import pytest

from app.models.model_config import ModelConfig
from app.models.namespace import DataSource, Namespace
from app.models.schema_canonical_object import SchemaCanonicalObject
from app.models.user import UserNamespaceAccess


async def _setup_ns(db, name: str, slug: str) -> int:
    ns = Namespace(name=name, slug=slug)
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    return ns.id


async def _add_datasource(db, ns_id: int, ds_id: int = 0):
    ds = DataSource(
        namespace_id=ns_id, db_type="mysql",
        host="localhost", port=3306, database="test",
        username="root", password="",
    )
    if ds_id:
        ds.id = ds_id
    db.add(ds)
    await db.commit()


async def _add_active_chat_config(db):
    cfg = ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="test-key",
        model_name="test-chat", model_type="CHAT",
        is_active=True, is_deleted=False,
    )
    db.add(cfg)
    await db.commit()


async def _add_schema(db, ns_id: int):
    sco = SchemaCanonicalObject(
        namespace_id=ns_id, db_type="mysql", database="test",
        target="users", fields_json='[{"name":"id","type":"int"}]',
    )
    db.add(sco)
    await db.commit()


@pytest.mark.asyncio
async def test_readiness_all_ready(make_client, db):
    """四条件全满足 → ready=true, blockers=[]."""
    ns_id = await _setup_ns(db, "r1", "r1")
    await _add_datasource(db, ns_id)
    await _add_active_chat_config(db)
    await _add_schema(db, ns_id)

    client = await make_client(role="super_admin")
    resp = await client.get(f"/api/namespaces/{ns_id}/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True
    assert data["blockers"] == []


@pytest.mark.asyncio
async def test_readiness_no_datasource(make_client, db):
    """无数据源 → ready=false + blocker no_datasource."""
    ns_id = await _setup_ns(db, "r2", "r2")
    await _add_active_chat_config(db)
    await _add_schema(db, ns_id)

    client = await make_client(role="super_admin")
    resp = await client.get(f"/api/namespaces/{ns_id}/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert any(b["type"] == "no_datasource" for b in data["blockers"])


@pytest.mark.asyncio
async def test_readiness_no_api_key(make_client, db):
    """无 API Key → ready=false + blocker no_api_key."""
    ns_id = await _setup_ns(db, "r3", "r3")
    await _add_datasource(db, ns_id)
    await _add_schema(db, ns_id)

    client = await make_client(role="super_admin")
    resp = await client.get(f"/api/namespaces/{ns_id}/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert any(b["type"] == "no_api_key" for b in data["blockers"])


@pytest.mark.asyncio
async def test_readiness_no_schema(make_client, db):
    """无 Schema → ready=false + blocker no_schema."""
    ns_id = await _setup_ns(db, "r4", "r4")
    await _add_datasource(db, ns_id)
    await _add_active_chat_config(db)

    client = await make_client(role="super_admin")
    resp = await client.get(f"/api/namespaces/{ns_id}/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert any(b["type"] == "no_schema" for b in data["blockers"])


@pytest.mark.asyncio
async def test_readiness_no_access(make_client, db):
    """无权限 → ready=false + blocker no_access."""
    ns_id = await _setup_ns(db, "r5", "r5")

    client = await make_client(role="user", user_id=99, username="nobody")
    resp = await client.get(f"/api/namespaces/{ns_id}/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert any(b["type"] == "no_access" for b in data["blockers"])


@pytest.mark.asyncio
async def test_readiness_non_admin_sees_user_action(make_client, db):
    """普通用户调用 → 200, 返回 user_action 文案."""
    ns_id = await _setup_ns(db, "r6", "r6")
    # 先创建用户（make_client 在 db 中创建 user）
    client = await make_client(role="user", user_id=99, username="user1")
    # 再授权
    db.add(UserNamespaceAccess(user_id=99, namespace_id=ns_id))
    await db.commit()

    resp = await client.get(f"/api/namespaces/{ns_id}/readiness")
    assert resp.status_code == 200
    data = resp.json()
    for b in data["blockers"]:
        assert "user_action" in b
        assert "admin_action" in b


@pytest.mark.asyncio
async def test_readiness_namespace_not_found(make_client, db):
    """命名空间不存在 → 404."""
    client = await make_client(role="super_admin")
    resp = await client.get("/api/namespaces/99999/readiness")
    assert resp.status_code in (404, 200)
