"""模型配置访问作用域 (RBAC) 测试。spec: spec-model-config-access-scoping.md

house pattern (见 tests/api/test_namespace_rbac.py): 外部 User / Namespace(created_by=) /
UserNamespaceAccess / ModelConfig 行全部经 db fixture 直接播种; make_client 只调一次
(行动身份)。避免 conftest.py:133 的 app.dependency_overrides[get_current_user] 全局
last-wins 覆盖 → 多 client 身份错配。
"""
import pytest
from app.models.user import User, UserNamespaceAccess
from app.models.namespace import Namespace
from app.models.model_config import ModelConfig


def _cfg(name: str, namespace_id: int | None, model_type: str = "CHAT") -> ModelConfig:
    """最小合法配置行 (其余字段走 ORM 默认)。"""
    return ModelConfig(
        provider="openai", base_url="https://x", api_key="sk-x",
        model_name=name, model_type=model_type, namespace_id=namespace_id,
    )


# ═══ 读端点 (Task 1) ═══════════════════════════════════════════════

@pytest.mark.asyncio
async def test_list_requires_admin(make_client, db):
    """D1: plain user 调 list → 403。"""
    u = User(username="rbac_plain", role="user", password_hash="x")
    db.add(u); await db.commit()
    client = await make_client(role="user", user_id=u.id, username="rbac_plain")
    assert (await client.get("/api/model-config/list")).status_code == 403


@pytest.mark.asyncio
async def test_list_owner_admin_sees_own_and_global(make_client, db):
    """owner admin 无参 list → 自己空间 ∪ 全局，不含他空间。"""
    a = User(username="rbac_owner", role="admin", password_hash="x")
    fo = User(username="rbac_fo1", role="admin", password_hash="x")
    db.add_all([a, fo]); await db.flush()
    mine = Namespace(name="A", slug="rbac-a", created_by=a.id)
    theirs = Namespace(name="B", slug="rbac-b", created_by=fo.id)
    db.add_all([mine, theirs]); await db.flush()
    db.add_all([_cfg("in-a", mine.id), _cfg("in-b", theirs.id), _cfg("global-chat", None)])
    await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="rbac_owner")
    resp = await client.get("/api/model-config/list")
    assert resp.status_code == 200
    names = {c["model_name"] for c in resp.json()}
    assert "in-a" in names and "global-chat" in names
    assert "in-b" not in names


@pytest.mark.asyncio
async def test_list_granted_admin_sees_granted_and_global(make_client, db):
    """被授权 (非 owner) 的 admin 无参 list → 被授空间 ∪ 全局。"""
    a = User(username="rbac_granted", role="admin", password_hash="x")
    fo = User(username="rbac_fo2", role="admin", password_hash="x")
    db.add_all([a, fo]); await db.flush()
    theirs = Namespace(name="G", slug="rbac-g", created_by=fo.id)
    db.add(theirs); await db.flush()
    db.add(UserNamespaceAccess(user_id=a.id, namespace_id=theirs.id))
    db.add_all([_cfg("in-g", theirs.id), _cfg("global-chat", None)])
    await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="rbac_granted")
    resp = await client.get("/api/model-config/list")
    assert {c["model_name"] for c in resp.json()} == {"in-g", "global-chat"}


@pytest.mark.asyncio
async def test_list_foreign_namespace_id_forbidden(make_client, db):
    """admin 带无权 namespace_id → 403。"""
    a = User(username="rbac_a3", role="admin", password_hash="x")
    fo = User(username="rbac_fo3", role="admin", password_hash="x")
    db.add_all([a, fo]); await db.flush()
    theirs = Namespace(name="B", slug="rbac-b3", created_by=fo.id)
    db.add(theirs); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="rbac_a3")
    resp = await client.get(f"/api/model-config/list?namespace_id={theirs.id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_own_namespace_id_returns_union_global(make_client, db):
    """admin 带自己 namespace_id → 该空间 ∪ 全局。"""
    a = User(username="rbac_a4", role="admin", password_hash="x")
    db.add(a); await db.flush()
    mine = Namespace(name="A", slug="rbac-a4", created_by=a.id)
    db.add(mine); await db.flush()
    db.add_all([_cfg("in-a", mine.id), _cfg("global-chat", None)])
    await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="rbac_a4")
    resp = await client.get(f"/api/model-config/list?namespace_id={mine.id}")
    assert resp.status_code == 200
    assert {c["model_name"] for c in resp.json()} == {"in-a", "global-chat"}


@pytest.mark.asyncio
async def test_list_empty_accessible_returns_only_global(make_client, db):
    """spec §4.3 边界: admin 零 owner/granted → in_([]) → 只返回全局。"""
    a = User(username="rbac_empty", role="admin", password_hash="x")
    fo = User(username="rbac_fo5", role="admin", password_hash="x")
    db.add_all([a, fo]); await db.flush()
    theirs = Namespace(name="B", slug="rbac-b5", created_by=fo.id)
    db.add(theirs); await db.flush()
    db.add_all([_cfg("in-b", theirs.id), _cfg("global-chat", None)])
    await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="rbac_empty")
    resp = await client.get("/api/model-config/list")
    assert resp.status_code == 200
    assert {c["model_name"] for c in resp.json()} == {"global-chat"}
