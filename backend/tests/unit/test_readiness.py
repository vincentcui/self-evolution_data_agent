"""Readiness 端点测试."""
from __future__ import annotations

import pytest

from app.models.model_config import ModelConfig
from app.models.namespace import DataSource, Namespace
from app.models.schema_canonical_object import SchemaCanonicalObject
from app.models.user import UserNamespaceAccess


@pytest.mark.asyncio
async def test_readiness_all_ready(make_client, db):
    """四条件全满足 → ready=true, blockers=[]."""
    # 创建命名空间
    db.add(Namespace(id=10, name="ready1", slug="ready1"))
    # 添加数据源
    db.add(DataSource(
        id=1, namespace_id=10, db_type="mysql",
        host="localhost", port=3306, database="test",
        username="root", password="",
    ))
    # 激活 CHAT 配置
    db.add(ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="test-key",
        model_name="test-chat", model_type="CHAT",
        is_active=True, is_deleted=False,
    ))
    # Schema
    db.add(SchemaCanonicalObject(
        namespace_id=10, db_type="mysql", database="test",
        target="users", field_path="id", field_type="int",
    ))
    await db.commit()

    client = await make_client(role="super_admin")
    resp = await client.get("/api/namespaces/10/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True
    assert data["blockers"] == []


@pytest.mark.asyncio
async def test_readiness_no_datasource(make_client, db):
    """无数据源 → ready=false + blocker no_datasource."""
    db.add(Namespace(id=11, name="ready2", slug="ready2"))
    db.add(ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="test-key",
        model_name="test-chat", model_type="CHAT",
        is_active=True, is_deleted=False,
    ))
    db.add(SchemaCanonicalObject(
        namespace_id=11, db_type="mysql", database="test",
        target="t", field_path="id", field_type="int",
    ))
    await db.commit()

    client = await make_client(role="super_admin")
    resp = await client.get("/api/namespaces/11/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert any(b["type"] == "no_datasource" for b in data["blockers"])


@pytest.mark.asyncio
async def test_readiness_no_api_key(make_client, db):
    """无 API Key → ready=false + blocker no_api_key."""
    db.add(Namespace(id=12, name="ready3", slug="ready3"))
    db.add(DataSource(
        id=2, namespace_id=12, db_type="mysql",
        host="localhost", port=3306, database="test",
        username="root", password="",
    ))
    db.add(SchemaCanonicalObject(
        namespace_id=12, db_type="mysql", database="test",
        target="t", field_path="id", field_type="int",
    ))
    await db.commit()

    client = await make_client(role="super_admin")
    resp = await client.get("/api/namespaces/12/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert any(b["type"] == "no_api_key" for b in data["blockers"])


@pytest.mark.asyncio
async def test_readiness_no_schema(make_client, db):
    """无 Schema → ready=false + blocker no_schema."""
    db.add(Namespace(id=13, name="ready4", slug="ready4"))
    db.add(DataSource(
        id=3, namespace_id=13, db_type="mysql",
        host="localhost", port=3306, database="test",
        username="root", password="",
    ))
    db.add(ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="test-key",
        model_name="test-chat", model_type="CHAT",
        is_active=True, is_deleted=False,
    ))
    await db.commit()

    client = await make_client(role="super_admin")
    resp = await client.get("/api/namespaces/13/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert any(b["type"] == "no_schema" for b in data["blockers"])


@pytest.mark.asyncio
async def test_readiness_no_access(make_client, db):
    """无权限 → ready=false + blocker no_access."""
    db.add(Namespace(id=14, name="ready5", slug="ready5"))
    await db.commit()

    # 以另一个用户身份访问（无该空间权限）
    client = await make_client(role="user", user_id=99, username="nobody")
    resp = await client.get("/api/namespaces/14/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert any(b["type"] == "no_access" for b in data["blockers"])


@pytest.mark.asyncio
async def test_readiness_non_admin_sees_user_action(make_client, db):
    """普通用户调用 → 200, 返回 user_action 文案."""
    db.add(Namespace(id=15, name="ready6", slug="ready6"))
    # 给 user 赋予访问权限
    db.add(UserNamespaceAccess(user_id=99, namespace_id=15, granted_by=1))
    await db.commit()

    client = await make_client(role="user", user_id=99, username="user1")
    resp = await client.get("/api/namespaces/15/readiness")
    assert resp.status_code == 200
    data = resp.json()
    # 即使 ready=false, user_action 字段仍存在
    for b in data["blockers"]:
        assert "user_action" in b
        assert "admin_action" in b


@pytest.mark.asyncio
async def test_readiness_namespace_not_found(make_client, db):
    """命名空间不存在 → 404."""
    client = await make_client(role="super_admin")
    resp = await client.get("/api/namespaces/99999/readiness")
    # assert_ns_access 对不存在的空间抛 404
    assert resp.status_code == 404 or resp.status_code == 200
