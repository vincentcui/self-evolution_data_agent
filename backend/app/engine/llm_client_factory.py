"""OpenAI / Anthropic 客户端工厂 — 无依赖, model_registry + llm 共用."""
from __future__ import annotations

import anthropic
import httpx
from openai import OpenAI


def build_openai_client(
    api_key: str, base_url: str, *, timeout: float, proxy_url: str | None = None,
) -> OpenAI:
    """构造 OpenAI 兼容客户端."""
    kwargs: dict = {"api_key": api_key, "base_url": base_url, "timeout": timeout}
    if proxy_url:
        kwargs["http_client"] = httpx.Client(proxy=proxy_url)
    return OpenAI(**kwargs)


def build_anthropic_client(
    api_key: str, base_url: str, *, timeout: float, proxy_url: str | None = None,
) -> anthropic.Anthropic:
    """构造 Anthropic 客户端."""
    kwargs: dict = {"api_key": api_key, "base_url": base_url, "timeout": timeout}
    if proxy_url:
        kwargs["http_client"] = httpx.Client(proxy=proxy_url)
    return anthropic.Anthropic(**kwargs)
