"""测试 ModelRegistry 的多 namespace 槽 + 优先级解析."""
import pytest
from app.engine.model_registry import ModelRegistry


def _cfg(model_name: str) -> dict:
    return {
        "id": 1,
        "provider": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "sk-test",
        "model_name": model_name,
        "model_type": "CHAT",
        "protocol": "openai",
        "temperature": 0.1,
        "max_tokens": 4096,
        "completions_path": None,
        "embeddings_path": None,
        "proxy_enabled": False,
        "proxy_host": None,
        "proxy_port": None,
        "proxy_username": None,
        "proxy_password": None,
    }


class TestResolveChatConfig:
    def test_namespace_config_takes_priority_over_global(self):
        """namespace 槽有值时, 不走全局槽."""
        reg = ModelRegistry()
        reg.refresh_chat(_cfg("gpt-4o"), namespace_id=None)       # 全局
        reg.refresh_chat(_cfg("deepseek-chat"), namespace_id=1)   # namespace 1
        cfg = reg.resolve_chat_config(namespace_id=1)
        assert cfg["model_name"] == "deepseek-chat"

    def test_fallback_to_global_when_namespace_not_configured(self):
        """namespace 槽无值时, 回退全局."""
        reg = ModelRegistry()
        reg.refresh_chat(_cfg("gpt-4o"), namespace_id=None)
        cfg = reg.resolve_chat_config(namespace_id=999)
        assert cfg["model_name"] == "gpt-4o"

    def test_returns_none_when_neither_configured(self):
        """两级都无配置 → None."""
        reg = ModelRegistry()
        assert reg.resolve_chat_config(namespace_id=1) is None
        assert reg.resolve_chat_config(namespace_id=None) is None

    def test_deactivate_namespace_falls_back_to_global(self):
        """namespace 配置被清除后回退全局."""
        reg = ModelRegistry()
        reg.refresh_chat(_cfg("gpt-4o"), namespace_id=None)
        reg.refresh_chat(_cfg("deepseek-chat"), namespace_id=1)
        reg.refresh_chat(None, namespace_id=1)  # 清除 namespace 1
        cfg = reg.resolve_chat_config(namespace_id=1)
        assert cfg["model_name"] == "gpt-4o"

    def test_different_namespaces_isolated(self):
        """不同 namespace 的配置互不干扰."""
        reg = ModelRegistry()
        reg.refresh_chat(_cfg("ns-a-model"), namespace_id=1)
        reg.refresh_chat(_cfg("ns-b-model"), namespace_id=2)
        assert reg.resolve_chat_config(1)["model_name"] == "ns-a-model"
        assert reg.resolve_chat_config(2)["model_name"] == "ns-b-model"
