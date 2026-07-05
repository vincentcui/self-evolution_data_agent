"""model_registry client 超时从 settings.llm_client_timeout_secs 读取, 不再硬编码 15."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.engine import model_registry as mr


def _patch_factories(monkeypatch, captured: dict):
    """patch llm_client_factory 两个 build_* 函数, 捕获 timeout kwarg."""
    def _fake_openai(api_key, base_url, timeout, proxy_url=None):
        captured["openai_timeout"] = timeout
        return MagicMock(name="openai_client")

    def _fake_anthropic(api_key, base_url, timeout, proxy_url=None):
        captured["anthropic_timeout"] = timeout
        return MagicMock(name="anthropic_client")

    monkeypatch.setattr(
        "app.engine.llm_client_factory.build_openai_client", _fake_openai)
    monkeypatch.setattr(
        "app.engine.llm_client_factory.build_anthropic_client", _fake_anthropic)


def test_openai_chat_client_uses_settings_timeout(monkeypatch):
    captured: dict = {}
    _patch_factories(monkeypatch, captured)
    monkeypatch.setattr("app.config.settings.llm_client_timeout_secs", 300)

    cfg = {"api_key": "k", "base_url": "http://x", "protocol": "openai"}
    registry = mr.ModelRegistry()
    registry.refresh_chat(cfg)
    registry.get_chat_client(cfg)
    assert captured["openai_timeout"] == 300


def test_anthropic_chat_client_uses_settings_timeout(monkeypatch):
    captured: dict = {}
    _patch_factories(monkeypatch, captured)
    monkeypatch.setattr("app.config.settings.llm_client_timeout_secs", 90)

    cfg = {"api_key": "k", "base_url": "http://x", "protocol": "anthropic"}
    registry = mr.ModelRegistry()
    registry.refresh_chat(cfg)
    registry.get_chat_client(cfg)
    assert captured["anthropic_timeout"] == 90


def test_embedding_client_uses_settings_timeout(monkeypatch):
    captured: dict = {}
    _patch_factories(monkeypatch, captured)
    monkeypatch.setattr("app.config.settings.llm_client_timeout_secs", 180)

    cfg = {"api_key": "k", "base_url": "http://x"}
    registry = mr.ModelRegistry()
    registry.refresh_embedding(cfg)
    registry.get_embedding_client(cfg)
    assert captured["openai_timeout"] == 180


def test_no_hardcoded_15_constant():
    """_CLIENT_TIMEOUT 常量已删除, 不再存在于模块."""
    assert not hasattr(mr, "_CLIENT_TIMEOUT")
