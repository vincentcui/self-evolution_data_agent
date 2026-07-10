"""_get_openai_client / _get_claude_client 从 registry 取 client + 负向路径.

NOTE: 特性分支 namespace-model-config 将 registry.chat_config (deprecated)
改为 registry.resolve_chat_config(namespace_id), 测试 mock 同步更新.
"""
from unittest.mock import patch

import pytest

from app.engine.model_registry import registry


def test_get_openai_client_uses_registry():
    """_get_openai_client 从 registry.get_chat_client() 取 client."""
    from app.engine.llm import _get_openai_client
    fake_cfg = {"protocol": "openai", "namespace_id": None}
    with patch.object(registry, 'resolve_chat_config', return_value=fake_cfg) as mock_resolve:
        with patch.object(registry, 'get_chat_client') as mock_get:
            mock_get.return_value = object()
            client = _get_openai_client()
            mock_resolve.assert_called_once_with(None)
            mock_get.assert_called_once()
            assert client is mock_get.return_value


def test_get_openai_client_raises_when_no_config():
    """_get_openai_client 无激活配置时抛 RuntimeError."""
    from app.engine.llm import _get_openai_client
    with patch.object(registry, 'resolve_chat_config', return_value=None):
        with pytest.raises(RuntimeError):
            _get_openai_client()


def test_get_openai_client_raises_on_protocol_mismatch():
    """激活的是 anthropic 协议, 调 _get_openai_client → RuntimeError."""
    from app.engine.llm import _get_openai_client
    fake_cfg = {"protocol": "anthropic", "namespace_id": None}
    with patch.object(registry, 'resolve_chat_config', return_value=fake_cfg):
        with pytest.raises(RuntimeError, match="无激活的 openai"):
            _get_openai_client()


def test_chat_completion_uses_registry_protocol():
    """chat_completion 从 resolve_chat_config()['protocol'] 取 provider."""
    from app.engine.llm import chat_completion
    fake_cfg = {"protocol": "anthropic", "model_name": "claude",
                "api_key": "k", "base_url": "https://x", "namespace_id": None}
    with patch.object(registry, 'resolve_chat_config', return_value=fake_cfg):
        with patch('app.engine.llm._claude_chat_with_retry') as mock_claude:
            mock_claude.return_value = "ok"
            result = chat_completion([{"role": "user", "content": "hi"}])
            mock_claude.assert_called_once()
            assert result == "ok"


def test_chat_completion_raises_when_no_active_config():
    """G8 验收: chat_completion() 无激活配置时抛 RuntimeError (非 fallback env)."""
    from app.engine.llm import chat_completion
    with patch.object(registry, 'resolve_chat_config', return_value=None):
        with pytest.raises(RuntimeError, match="无激活的 Chat"):
            chat_completion([{"role": "user", "content": "hi"}])


def test_get_openai_client_derives_namespace_from_cfg():
    """_get_openai_client 从 cfg['namespace_id'] 推导 namespace_id (hy3 修复核心).

    内部调用方 (_openai_chat / _openai_tool_use 等) 只传 cfg 不传 namespace_id,
    依赖 _get_openai_client 内部从 cfg 推导, 保证 client 缓存落到正确的 namespace 槽.
    """
    from app.engine.llm import _get_openai_client
    fake_cfg = {"protocol": "openai", "namespace_id": 42}
    with patch.object(registry, 'resolve_chat_config', return_value=fake_cfg):
        with patch.object(registry, 'get_chat_client') as mock_get:
            mock_get.return_value = object()
            _get_openai_client()
            mock_get.assert_called_once_with(fake_cfg, namespace_id=42)


def test_get_claude_client_derives_namespace_from_cfg():
    """_get_claude_client 从 cfg['namespace_id'] 推导 namespace_id (hy3 修复核心)."""
    from app.engine.llm import _get_claude_client
    fake_cfg = {"protocol": "anthropic", "namespace_id": 7}
    with patch.object(registry, 'resolve_chat_config', return_value=fake_cfg):
        with patch.object(registry, 'get_chat_client') as mock_get:
            mock_get.return_value = object()
            _get_claude_client()
            mock_get.assert_called_once_with(fake_cfg, namespace_id=7)
