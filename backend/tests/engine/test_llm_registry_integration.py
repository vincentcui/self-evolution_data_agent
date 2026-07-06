"""_get_openai_client / _get_claude_client 从 registry 取 client + 负向路径."""
from unittest.mock import PropertyMock, patch

import pytest

from app.engine.model_registry import ModelRegistry, registry


def test_get_openai_client_uses_registry():
    """_get_openai_client 从 registry.get_chat_client() 取 client."""
    from app.engine.llm import _get_openai_client
    with patch.object(registry, 'get_chat_client') as mock_get:
        mock_get.return_value = object()
        with patch.object(ModelRegistry, 'chat_config', new_callable=PropertyMock) as mock_cfg:
            mock_cfg.return_value = {'protocol': 'openai'}
            client = _get_openai_client()
            mock_get.assert_called_once()
            assert client is mock_get.return_value


def test_get_openai_client_raises_when_no_config():
    """_get_openai_client 无激活配置时抛 RuntimeError."""
    from app.engine.llm import _get_openai_client
    with patch.object(ModelRegistry, 'chat_config', new_callable=PropertyMock) as mock_cfg:
        mock_cfg.return_value = None
        with pytest.raises(RuntimeError):
            _get_openai_client()


def test_get_openai_client_raises_on_protocol_mismatch():
    """激活的是 anthropic 协议, 调 _get_openai_client → RuntimeError."""
    from app.engine.llm import _get_openai_client
    with patch.object(ModelRegistry, 'chat_config', new_callable=PropertyMock) as mock_cfg:
        mock_cfg.return_value = {'protocol': 'anthropic'}
        with pytest.raises(RuntimeError, match="无激活的 openai"):
            _get_openai_client()


def test_chat_completion_uses_registry_protocol():
    """chat_completion 从 registry.chat_config['protocol'] 取 provider."""
    from app.engine.llm import chat_completion
    with patch.object(ModelRegistry, 'chat_config', new_callable=PropertyMock) as mock_cfg:
        mock_cfg.return_value = {'protocol': 'anthropic', 'model_name': 'claude',
                                 'api_key': 'k', 'base_url': 'https://x'}
        with patch('app.engine.llm._claude_chat_with_retry') as mock_claude:
            mock_claude.return_value = "ok"
            result = chat_completion([{"role": "user", "content": "hi"}])
            mock_claude.assert_called_once()
            assert result == "ok"


def test_chat_completion_raises_when_no_active_config():
    """G8 验收: chat_completion() 无激活配置时抛 RuntimeError (非 fallback env)."""
    from app.engine.llm import chat_completion
    with patch.object(ModelRegistry, 'chat_config', new_callable=PropertyMock) as mock_cfg:
        mock_cfg.return_value = None
        with pytest.raises(RuntimeError, match="无激活的 Chat"):
            chat_completion([{"role": "user", "content": "hi"}])
