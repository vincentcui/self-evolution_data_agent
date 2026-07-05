"""LLM client 工厂单测 — 类型确定 + proxy 传入 httpx.Client."""
from unittest.mock import MagicMock, patch

import anthropic
import httpx
from openai import OpenAI

from app.engine.llm import build_anthropic_client, build_chat_client, build_openai_client


def test_build_openai_client_returns_openai_type():
    client = build_openai_client("sk-test", "https://api.example.com/v1", timeout=30)
    assert isinstance(client, OpenAI)


def test_build_anthropic_client_returns_anthropic_type():
    client = build_anthropic_client("sk-test", "https://api.example.com", timeout=30)
    assert isinstance(client, anthropic.Anthropic)


def test_build_chat_client_dispatches_by_protocol():
    assert isinstance(build_chat_client("k", "u", "openai", timeout=30), OpenAI)
    assert isinstance(build_chat_client("k", "u", "anthropic", timeout=30), anthropic.Anthropic)


def test_proxy_url_wires_httpx_client():
    with patch("app.engine.llm_client_factory.OpenAI") as mock_openai_cls:
        mock_instance = MagicMock()
        mock_openai_cls.return_value = mock_instance

        build_openai_client("k", "u", timeout=30, proxy_url="http://user:pwd@host:8080")

        # 验证 OpenAI 被调用时 http_client 参数包含真实 httpx.Client
        call_kwargs = mock_openai_cls.call_args[1]
        assert "http_client" in call_kwargs
        assert isinstance(call_kwargs["http_client"], httpx.Client)

