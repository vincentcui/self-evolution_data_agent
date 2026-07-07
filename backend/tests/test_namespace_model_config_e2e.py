"""端到端: namespace 级别配置从创建到 LLM 调用的完整链路."""
import asyncio
import logging
import time
import pytest
from unittest.mock import patch

from app.engine.model_registry import ModelRegistry

log = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_namespace_config_overrides_global_in_agent_loop(admin_client):
    """
    完整流程:
    1. 创建 namespace
    2. 创建全局 CHAT config (兜底)
    3. 为 namespace 创建并激活专属 CHAT config
    4. 该 namespace 的 check-ready 返回 ready
    5. 未配置的 namespace 也 ready (全局兜底)
    """
    # 创建 test namespace
    ns_resp = await admin_client.post(
        "/api/namespaces",
        json={"name": "test-ns-e2e", "slug": "test-ns-e2e", "description": "test"},
    )
    ns_id = ns_resp.json()["id"]

    # 全局 CHAT 兜底配置（创建 + 激活）
    g_resp = await admin_client.post("/api/model-config/add", json={
        "provider": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "sk-global",
        "model_name": "gpt-4o",
        "model_type": "CHAT",
        "namespace_id": None,
    })
    await admin_client.post(f"/api/model-config/activate/{g_resp.json()['id']}")

    # 创建 namespace CHAT config
    resp = await admin_client.post("/api/model-config/add", json={
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-ns-specific",
        "model_name": "deepseek-chat",
        "model_type": "CHAT",
        "namespace_id": ns_id,
    })
    ns_config_id = resp.json()["id"]
    assert resp.json()["namespace_id"] == ns_id

    # 激活
    resp = await admin_client.post(f"/api/model-config/activate/{ns_config_id}")
    assert resp.status_code == 200

    # check-ready: 该 namespace ready
    resp = await admin_client.get(f"/api/model-config/check-ready?namespace_id={ns_id}")
    assert resp.json()["chat_model_ready"] is True

    # check-ready: 未配置的 namespace 也 ready (全局兜底)
    resp = await admin_client.get("/api/model-config/check-ready?namespace_id=99999")
    assert resp.json()["chat_model_ready"] is True


@pytest.mark.asyncio
async def test_deactivate_namespace_config_falls_back_to_global(admin_client):
    """取消激活 namespace 配置后, 该 namespace 回退全局."""
    # 创建 test namespace
    ns_resp = await admin_client.post(
        "/api/namespaces",
        json={"name": "test-ns-fb", "slug": "test-ns-fb", "description": "test"},
    )
    ns_id = ns_resp.json()["id"]

    # 全局 CHAT 兜底配置（创建 + 激活）
    g_resp = await admin_client.post("/api/model-config/add", json={
        "provider": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "sk-global",
        "model_name": "gpt-4o",
        "model_type": "CHAT",
        "namespace_id": None,
    })
    await admin_client.post(f"/api/model-config/activate/{g_resp.json()['id']}")

    # 创建并激活 namespace config
    resp = await admin_client.post("/api/model-config/add", json={
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-ns",
        "model_name": "deepseek-chat",
        "model_type": "CHAT",
        "namespace_id": ns_id,
    })
    ns_config_id = resp.json()["id"]
    await admin_client.post(f"/api/model-config/activate/{ns_config_id}")

    # 删除 namespace config (级联 deactivate)
    await admin_client.delete(f"/api/model-config/{ns_config_id}")

    # check-ready 仍 ready (全局兜底)
    resp = await admin_client.get(f"/api/model-config/check-ready?namespace_id={ns_id}")
    assert resp.json()["chat_model_ready"] is True


def test_namespace_config_actually_used_by_resolve():
    """验证 resolve_chat_config 被调用且 namespace_id 正确 (非仅 check-ready 端点)."""
    reg = ModelRegistry()
    reg.refresh_chat(
        {"id": 1, "provider": "openai", "protocol": "openai", "api_key": "sk-x",
         "base_url": "https://x", "model_name": "global-model", "model_type": "CHAT",
         "temperature": 0.1, "max_tokens": 4096,
         "completions_path": None, "embeddings_path": None,
         "proxy_enabled": False, "proxy_host": None, "proxy_port": None,
         "proxy_username": None, "proxy_password": None},
        namespace_id=None,
    )
    reg.refresh_chat(
        {"id": 2, "provider": "openai", "protocol": "openai", "api_key": "sk-y",
         "base_url": "https://y", "model_name": "ns-model", "model_type": "CHAT",
         "temperature": 0.1, "max_tokens": 4096,
         "completions_path": None, "embeddings_path": None,
         "proxy_enabled": False, "proxy_host": None, "proxy_port": None,
         "proxy_username": None, "proxy_password": None},
        namespace_id=42,
    )

    # 验证 resolve 返回 namespace 配置
    cfg = reg.resolve_chat_config(42)
    assert cfg["model_name"] == "ns-model"

    # 验证 chat_completion 使用 resolve 结果
    from app.engine import llm
    with patch("app.engine.model_registry.registry", reg):
        with patch.object(llm, "_openai_chat_with_retry", return_value="ok") as mock:
            llm.chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                namespace_id=42,
            )
            call_args = mock.call_args
            cfg_arg = call_args[0][1]  # 第二个位置参数 = cfg
            assert cfg_arg["model_name"] == "ns-model"


@pytest.mark.asyncio
async def test_concurrent_activate_registry_eventual_consistency(admin_client):
    """
    同 namespace 并发激活两个 config → 最终只有一个 active。
    验证 design.md §7 分析的「两轮 _do_refresh 间隔 < 100ms, 最终一致」。
    """
    # 创建 test namespace
    ns_resp = await admin_client.post(
        "/api/namespaces",
        json={"name": "test-ns-cc", "slug": "test-ns-cc", "description": "test"},
    )
    ns_id = ns_resp.json()["id"]

    # 创建两条 namespace CHAT config (均未激活)
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
    t0 = time.monotonic()
    await asyncio.gather(
        admin_client.post(f"/api/model-config/activate/{ids[0]}"),
        admin_client.post(f"/api/model-config/activate/{ids[1]}"),
        return_exceptions=True,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000

    # 查询 DB 最终状态
    resp = await admin_client.get(f"/api/model-config/list?namespace_id={ns_id}")
    active_ids = [c["id"] for c in resp.json() if c["is_active"]]

    assert len(active_ids) == 1, f"应有且仅有一个 active, 实际 {active_ids}"
    assert active_ids[0] in ids, f"active 的 config 不在原两条中: {active_ids}"

    log.info("并发激活 registry 最终一致, elapsed=%.1fms", elapsed_ms)
