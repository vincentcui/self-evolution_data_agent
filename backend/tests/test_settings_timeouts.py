"""新增 timeout / trace 裁剪配置字段的默认值与 env override 测试."""
from __future__ import annotations

from app.config import Settings


def test_llm_client_timeout_secs_default(monkeypatch):
    monkeypatch.delenv("IS_LLM_CLIENT_TIMEOUT_SECS", raising=False)
    assert Settings().llm_client_timeout_secs == 120


def test_llm_client_timeout_secs_env_override(monkeypatch):
    monkeypatch.setenv("IS_LLM_CLIENT_TIMEOUT_SECS", "300")
    assert Settings().llm_client_timeout_secs == 300


def test_llm_connect_test_timeout_secs_default(monkeypatch):
    monkeypatch.delenv("IS_LLM_CONNECT_TEST_TIMEOUT_SECS", raising=False)
    assert Settings().llm_connect_test_timeout_secs == 15


def test_llm_connect_test_timeout_secs_env_override(monkeypatch):
    monkeypatch.setenv("IS_LLM_CONNECT_TEST_TIMEOUT_SECS", "30")
    assert Settings().llm_connect_test_timeout_secs == 30


def test_agent_trace_compact_row_cap_default(monkeypatch):
    monkeypatch.delenv("IS_AGENT_TRACE_COMPACT_ROW_CAP", raising=False)
    assert Settings().agent_trace_compact_row_cap == 20


def test_agent_trace_compact_row_cap_env_override(monkeypatch):
    monkeypatch.setenv("IS_AGENT_TRACE_COMPACT_ROW_CAP", "50")
    assert Settings().agent_trace_compact_row_cap == 50
