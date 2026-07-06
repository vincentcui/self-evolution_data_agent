"""registry.load_from_db() 测试."""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.models.model_config import ModelConfig


@pytest.mark.asyncio
async def test_load_from_db_restores_ready_state(db, model_registry_isolated, monkeypatch):
    """seed DB active config 后 load_from_db 能恢复 ready 状态."""
    chat_row = ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="test-key",
        model_name="test-chat", model_type="CHAT",
        is_active=True, is_deleted=False,
    )
    emb_row = ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="test-key",
        model_name="test-embedding", model_type="EMBEDDING",
        is_active=True, is_deleted=False,
    )
    db.add(chat_row)
    db.add(emb_row)
    await db.commit()

    @asynccontextmanager
    async def _same_session():
        yield db

    monkeypatch.setattr("app.db.metadata.async_session", _same_session)

    await model_registry_isolated.load_from_db()

    ready = model_registry_isolated.is_ready()
    assert ready["chat_ready"] is True
    assert ready["embedding_ready"] is True
    assert ready["ready"] is True


@pytest.mark.asyncio
async def test_is_ready_all_false_when_no_active_config(model_registry_isolated):
    """无 active config 时 is_ready() 全为 false."""
    ready = model_registry_isolated.is_ready()
    assert ready["chat_ready"] is False
    assert ready["embedding_ready"] is False
    assert ready["ready"] is False


@pytest.mark.asyncio
async def test_load_from_db_isolated_does_not_affect_other_tests(model_registry_isolated):
    """model_registry_isolated fixture 确保测试间隔离."""
    # 先确认隔离后是空状态
    ready = model_registry_isolated.is_ready()
    assert ready["chat_ready"] is False
    assert ready["embedding_ready"] is False

    # 手动设置一个配置
    model_registry_isolated.refresh_chat({"model_name": "test", "protocol": "openai"})
    ready = model_registry_isolated.is_ready()
    assert ready["chat_ready"] is True

    # fixture teardown 会重置，不影响其他测试（由 fixture 保证）


def test_build_proxy_url_disabled_returns_none():
    from app.engine.model_registry import _build_proxy_url
    assert _build_proxy_url({"proxy_enabled": False}) is None


def test_build_proxy_url_enabled_with_auth():
    from app.engine.model_registry import _build_proxy_url
    url = _build_proxy_url({
        "proxy_enabled": True, "proxy_host": "proxy.example.com", "proxy_port": 8080,
        "proxy_username": "u", "proxy_password": "p",
    })
    assert url == "http://u:p@proxy.example.com:8080"


def test_get_chat_client_uses_factory_not_direct_construction():
    """registry 不得直接 OpenAI()/Anthropic(), 必须走 llm 工厂."""
    from unittest.mock import patch

    from app.engine.model_registry import ModelRegistry
    registry = ModelRegistry()
    registry.refresh_chat({
        "protocol": "openai", "api_key": "k", "base_url": "https://x/v1",
        "model_name": "m", "proxy_enabled": False,
    })
    with patch("app.engine.llm_client_factory.build_openai_client") as mock_fac:
        registry._get_chat_client({"protocol": "openai", "api_key": "k",
                                   "base_url": "https://x/v1", "model_name": "m"})
        mock_fac.assert_called_once()
