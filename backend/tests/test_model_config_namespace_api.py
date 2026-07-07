"""测试 namespace 级别模型配置 API."""
import asyncio
import pytest


@pytest.mark.asyncio
async def test_add_chat_config_with_namespace_id(admin_client):
    """创建 CHAT 配置时可以指定 namespace_id."""
    # 先创建 test namespace
    ns_resp = await admin_client.post(
        "/api/namespaces",
        json={"name": "test-ns-api", "slug": "test-ns-api", "description": "test"},
    )
    ns_id = ns_resp.json()["id"]

    resp = await admin_client.post("/api/model-config/add", json={
        "provider": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "sk-test",
        "model_name": "gpt-4o",
        "model_type": "CHAT",
        "namespace_id": ns_id,
    })
    assert resp.status_code == 201
    assert resp.json()["namespace_id"] == ns_id


@pytest.mark.asyncio
async def test_add_embedding_config_namespace_id_forced_null(admin_client):
    """EMBEDDING 配置的 namespace_id 强制为 NULL, 即使传了也忽略."""
    resp = await admin_client.post("/api/model-config/add", json={
        "provider": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "sk-test",
        "model_name": "text-embedding-3",
        "model_type": "EMBEDDING",
        "namespace_id": 999,
    })
    assert resp.status_code == 201
    assert resp.json()["namespace_id"] is None


@pytest.mark.asyncio
async def test_add_config_with_nonexistent_namespace_returns_400(admin_client):
    """不存在的 namespace_id 应返回 400, 而非 FK IntegrityError → 500."""
    resp = await admin_client.post("/api/model-config/add", json={
        "provider": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "sk-test",
        "model_name": "gpt-4o",
        "model_type": "CHAT",
        "namespace_id": 99999,
    })
    assert resp.status_code == 400
    assert "不存在" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_filter_by_namespace(admin_client):
    """list 端点可按 namespace_id 过滤."""
    # 创建 test namespace
    ns_resp = await admin_client.post(
        "/api/namespaces",
        json={"name": "test-ns-list", "slug": "test-ns-list", "description": "test"},
    )
    ns_id = ns_resp.json()["id"]

    # 在那个 namespace 下创建 CHAT config
    await admin_client.post("/api/model-config/add", json={
        "provider": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "sk-test",
        "model_name": "gpt-4o",
        "model_type": "CHAT",
        "namespace_id": ns_id,
    })

    resp = await admin_client.get(f"/api/model-config/list?namespace_id={ns_id}")
    assert resp.status_code == 200
    for c in resp.json():
        assert c["namespace_id"] == ns_id


@pytest.mark.asyncio
async def test_concurrent_activation_same_namespace(admin_client):
    """同 namespace 并发激活两个 config → 最终只有一个 active."""
    # 创建 test namespace
    ns_resp = await admin_client.post(
        "/api/namespaces",
        json={"name": "test-ns-race", "slug": "test-ns-race", "description": "test"},
    )
    ns_id = ns_resp.json()["id"]

    # 创建两条 namespace CHAT config
    ids = []
    for i in range(2):
        resp = await admin_client.post("/api/model-config/add", json={
            "provider": "openai",
            "base_url": f"https://api{i}.openai.com",
            "api_key": f"sk-{i}",
            "model_name": f"model-{i}",
            "model_type": "CHAT",
            "namespace_id": ns_id,
        })
        ids.append(resp.json()["id"])

    # 并发激活
    await asyncio.gather(
        admin_client.post(f"/api/model-config/activate/{ids[0]}"),
        admin_client.post(f"/api/model-config/activate/{ids[1]}"),
        return_exceptions=True,
    )

    # 查询最终状态
    resp = await admin_client.get(f"/api/model-config/list?namespace_id={ns_id}")
    active_count = sum(1 for c in resp.json() if c["is_active"])
    assert active_count == 1, f"应有且仅有一个 active, 实际 {active_count}"
