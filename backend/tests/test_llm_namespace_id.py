"""测试 LLM 入口函数正确传递 namespace_id 到 registry."""
import pytest
from unittest.mock import patch, MagicMock


def test_chat_completion_passes_namespace_id_to_registry():
    """chat_completion 应将 namespace_id 传给 resolve_chat_config."""
    from app.engine import llm
    fake_cfg = {"protocol": "openai", "api_key": "sk-x", "base_url": "http://x",
                "model_name": "test", "temperature": 0.1, "max_tokens": 100,
                "completions_path": None, "proxy_enabled": False}

    with patch("app.engine.model_registry.registry") as mock_reg:
        mock_reg.resolve_chat_config.return_value = fake_cfg
        mock_reg.get_chat_client.return_value = MagicMock()
        mock_reg.chat_config = fake_cfg  # backward compat path

        # 模拟 _openai_chat_with_retry 返回文本
        with patch.object(llm, "_openai_chat_with_retry", return_value="ok"):
            llm.chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                namespace_id=5,
            )
        mock_reg.resolve_chat_config.assert_called_once_with(5)


def test_chat_completion_without_namespace_id_uses_global():
    """不传 namespace_id 时走全局 (向后兼容)."""
    from app.engine import llm
    fake_cfg = {"protocol": "openai", "api_key": "sk-x", "base_url": "http://x",
                "model_name": "test", "temperature": 0.1, "max_tokens": 100,
                "completions_path": None, "proxy_enabled": False}

    with patch("app.engine.model_registry.registry") as mock_reg:
        mock_reg.resolve_chat_config.return_value = fake_cfg
        mock_reg.chat_config = fake_cfg

        with patch.object(llm, "_openai_chat_with_retry", return_value="ok"):
            llm.chat_completion(messages=[{"role": "user", "content": "hi"}])
        mock_reg.resolve_chat_config.assert_called_once_with(None)
