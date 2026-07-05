"""连接测试路径显式传 settings.llm_connect_test_timeout_secs, 不吃工厂默认."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.engine import llm_client_factory as factory


def test_build_openai_client_timeout_required():
    """build_openai_client 的 timeout 参数无默认, 不传必抛 TypeError."""
    import inspect
    sig = inspect.signature(factory.build_openai_client)
    assert sig.parameters["timeout"].default is inspect.Parameter.empty


def test_build_anthropic_client_timeout_required():
    """build_anthropic_client 的 timeout 参数无默认."""
    import inspect
    sig = inspect.signature(factory.build_anthropic_client)
    assert sig.parameters["timeout"].default is inspect.Parameter.empty


def test_build_chat_client_timeout_required():
    """build_chat_client 的 timeout 参数无默认."""
    import inspect

    from app.engine.llm import build_chat_client
    sig = inspect.signature(build_chat_client)
    assert sig.parameters["timeout"].default is inspect.Parameter.empty


def test_connect_test_uses_settings_timeout(monkeypatch):
    """_test_openai_chat 显式传 settings.llm_connect_test_timeout_secs."""
    captured: dict = {}
    def _fake_openai(api_key, base_url, *, timeout, proxy_url=None):
        captured["timeout"] = timeout
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock()
        return client
    monkeypatch.setattr("app.engine.llm.build_openai_client", _fake_openai)
    monkeypatch.setattr("app.config.settings.llm_connect_test_timeout_secs", 30)

    from app.api.model_config import _test_openai_chat
    _test_openai_chat({"api_key": "k", "base_url": "http://x", "model_name": "m"})
    assert captured["timeout"] == 30
