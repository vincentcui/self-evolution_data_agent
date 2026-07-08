"""LLM 瞬态错误分类 + 重试退避单测 (P1-14).

聚焦 429 限流: 429 判 transient + 专属更长退避预算, 区别于 5xx 的通用预算.
构造真实 anthropic/openai APIStatusError (带 httpx.Response), 避免手搓 mock 失真.
"""
from unittest.mock import patch

import anthropic
import httpx
import openai
import pytest

from app.engine import llm as llm_mod
from app.engine.llm import (
    _is_transient_llm_error,
    _retry_after_secs,
    _retry_budget,
    _retry_wait_secs,
)

# ════════════════════════════════════════════
#  fixtures: 构造真实 SDK 异常
# ════════════════════════════════════════════

def _anthropic_err(status: int, headers: dict | None = None) -> anthropic.APIStatusError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status_code=status, request=req, headers=headers or {})
    if status == 429:
        return anthropic.RateLimitError("rate limited", response=resp, body=None)
    return anthropic.APIStatusError(f"http {status}", response=resp, body=None)


def _openai_err(status: int, headers: dict | None = None) -> openai.APIStatusError:
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(status_code=status, request=req, headers=headers or {})
    if status == 429:
        return openai.RateLimitError("rate limited", response=resp, body=None)
    return openai.APIStatusError(f"http {status}", response=resp, body=None)


# ════════════════════════════════════════════
#  分类: 429 现在判 transient
# ════════════════════════════════════════════

def test_429_anthropic_is_transient():
    assert _is_transient_llm_error(_anthropic_err(429)) is True


def test_429_openai_is_transient():
    assert _is_transient_llm_error(_openai_err(429)) is True


def test_5xx_anthropic_is_transient():
    assert _is_transient_llm_error(_anthropic_err(503)) is True


def test_4xx_business_error_not_transient():
    """400/401 业务错误不重试 (429 除外)."""
    assert _is_transient_llm_error(_anthropic_err(400)) is False
    assert _is_transient_llm_error(_openai_err(401)) is False


# ════════════════════════════════════════════
#  退避秒数: 429 长退避 + 尊重 retry-after
# ════════════════════════════════════════════

def test_retry_wait_429_without_header_exponential():
    """429 无 retry-after → 5/10/20 指数退避 (区别于 5xx 的 1/2/4)."""
    err = _anthropic_err(429)
    assert _retry_wait_secs(err, 0) == 5
    assert _retry_wait_secs(err, 1) == 10
    assert _retry_wait_secs(err, 2) == 20
    assert _retry_wait_secs(err, 3) == 20  # cap


def test_retry_wait_429_respects_retry_after_header():
    err = _anthropic_err(429, headers={"retry-after": "7"})
    assert _retry_wait_secs(err, 0) == 7


def test_retry_wait_429_retry_after_capped_at_60():
    err = _anthropic_err(429, headers={"retry-after": "120"})
    assert _retry_wait_secs(err, 0) == 60


def test_retry_wait_5xx_uses_short_backoff():
    err = _anthropic_err(503)
    assert _retry_wait_secs(err, 0) == 1
    assert _retry_wait_secs(err, 1) == 2


def test_retry_after_secs_missing_header_returns_none():
    assert _retry_after_secs(_anthropic_err(429)) is None


def test_retry_after_secs_garbage_returns_none():
    err = _anthropic_err(429, headers={"retry-after": "not-a-number"})
    assert _retry_after_secs(err) is None


# ════════════════════════════════════════════
#  预算: 429 用专属预算, 5xx 用通用预算
# ════════════════════════════════════════════

def test_retry_budget_429_uses_rate_limit_budget(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "llm_retry_max", 1)
    monkeypatch.setattr(llm_mod.settings, "llm_rate_limit_retry_max", 3)
    assert _retry_budget(_anthropic_err(429)) == 3


def test_retry_budget_5xx_uses_generic_budget(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "llm_retry_max", 1)
    monkeypatch.setattr(llm_mod.settings, "llm_rate_limit_retry_max", 3)
    assert _retry_budget(_anthropic_err(503)) == 1


# ════════════════════════════════════════════
#  端到端 retry loop (claude 路径)
# ════════════════════════════════════════════

@pytest.fixture
def _no_sleep():
    """禁用真实 sleep, 记录退避秒数."""
    waits: list[float] = []
    with patch.object(llm_mod.time, "sleep", side_effect=lambda s: waits.append(s)):
        yield waits


@pytest.fixture
def _budgets(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "llm_retry_max", 1)
    monkeypatch.setattr(llm_mod.settings, "llm_rate_limit_retry_max", 3)


def test_claude_429_retries_then_succeeds(_no_sleep, _budgets):
    """429 两次后成功: 429 预算=3, 通用预算=1 — 验证走的是 429 专属预算."""
    side_effects = [_anthropic_err(429), _anthropic_err(429), "OK"]
    with patch.object(llm_mod, "_claude_chat", side_effect=side_effects):
        result = llm_mod._claude_chat_with_retry([], {"protocol": "anthropic"})
    assert result == "OK"
    assert _no_sleep == [5, 10]  # 429 指数退避


def test_claude_429_exhausts_budget_then_raises(_no_sleep, _budgets):
    """429 持续: 1 次首调 + 3 次重试 = 4 次调用后抛 RateLimitError."""
    err = _anthropic_err(429)
    with patch.object(llm_mod, "_claude_chat", side_effect=[err, err, err, err]):
        with pytest.raises(anthropic.RateLimitError):
            llm_mod._claude_chat_with_retry([], {"protocol": "anthropic"})
    assert _no_sleep == [5, 10, 20]  # 3 次重试退避


def test_claude_429_respects_retry_after_in_loop(_no_sleep, _budgets):
    err = _anthropic_err(429, headers={"retry-after": "8"})
    with patch.object(llm_mod, "_claude_chat", side_effect=[err, err, "OK"]):
        result = llm_mod._claude_chat_with_retry([], {"protocol": "anthropic"})
    assert result == "OK"
    assert _no_sleep == [8, 8]


def test_claude_5xx_uses_generic_budget_only(_no_sleep, _budgets):
    """5xx 通用预算=1: 1 次首调 + 1 次重试后抛, 不借 429 预算."""
    err = _anthropic_err(503)
    with patch.object(llm_mod, "_claude_chat", side_effect=[err, err, err]):
        with pytest.raises(anthropic.APIStatusError):
            llm_mod._claude_chat_with_retry([], {"protocol": "anthropic"})
    assert _no_sleep == [1]  # 只重试一次, 短退避


def test_claude_400_raises_immediately_no_retry(_no_sleep, _budgets):
    err = _anthropic_err(400)
    with patch.object(llm_mod, "_claude_chat", side_effect=[err, "should-not-reach"]):
        with pytest.raises(anthropic.APIStatusError):
            llm_mod._claude_chat_with_retry([], {"protocol": "anthropic"})
    assert _no_sleep == []


def test_openai_429_retries_then_succeeds(_no_sleep, _budgets):
    side_effects = [_openai_err(429), "OK"]
    with patch.object(llm_mod, "_openai_chat", side_effect=side_effects):
        result = llm_mod._openai_chat_with_retry([], {"protocol": "openai"})
    assert result == "OK"
    assert _no_sleep == [5]
