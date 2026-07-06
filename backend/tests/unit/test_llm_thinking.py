"""_build_extra_body / _claude_thinking_cfg / reasoning_content 往返单元测试."""
import pytest
from app.engine.llm import (
    _build_extra_body,
    _claude_thinking_cfg,
    _to_openai_messages,
    ToolCall,
    ToolUseResponse,
    build_assistant_message,
)


class TestBuildExtraBody:
    def test_false_none_returns_disabled(self):
        assert _build_extra_body(False, None) == {"thinking": {"type": "disabled"}}

    def test_true_none_returns_none(self):
        assert _build_extra_body(True, None) is None

    def test_false_merges_existing_fields(self):
        result = _build_extra_body(False, {"x": 1})
        assert result == {"x": 1, "thinking": {"type": "disabled"}}

    def test_true_preserves_existing_removes_thinking(self):
        result = _build_extra_body(True, {"x": 1, "thinking": {"type": "disabled"}})
        assert result == {"x": 1}

    def test_false_overrides_existing_thinking(self):
        result = _build_extra_body(False, {"thinking": {"type": "enabled"}})
        assert result == {"thinking": {"type": "disabled"}}

    def test_true_removes_thinking_empty_returns_none(self):
        result = _build_extra_body(True, {"thinking": {"type": "disabled"}})
        assert result is None

    def test_true_keeps_other_fields(self):
        result = _build_extra_body(True, {"x": 1})
        assert result == {"x": 1}


class TestClaudeThinkingCfg:
    def test_true_returns_enabled_with_budget(self):
        from app.config import settings
        cfg = _claude_thinking_cfg(True)
        assert cfg == {"type": "enabled", "budget_tokens": settings.llm_claude_thinking_budget_tokens}

    def test_false_returns_none(self):
        assert _claude_thinking_cfg(False) is None


class TestThinkingBudgetValidator:
    """config._validate_thinking_budget 边界值."""

    def test_budget_at_boundary_1024_passes(self, monkeypatch):
        """边界值 1024 = Anthropic 最小值, 应通过."""
        from app.config import Settings
        monkeypatch.setenv("IS_LLM_CLAUDE_THINKING_BUDGET_TOKENS", "1024")
        s = Settings()
        assert s.llm_claude_thinking_budget_tokens == 1024

    def test_budget_below_1024_raises(self, monkeypatch):
        """低于 1024 应拒绝."""
        from app.config import Settings
        monkeypatch.setenv("IS_LLM_CLAUDE_THINKING_BUDGET_TOKENS", "512")
        with pytest.raises(ValueError, match="必须 >= 1024"):
            Settings()


class TestReasoningContentRoundtrip:
    """验证 reasoning_content 从 ToolUseResponse → assistant 消息 → _to_openai_messages 全链路."""

    def test_roundtrip_with_reasoning_content(self):
        """模拟: _openai_tool_use 捕获 reasoning_content → 调用方构造 assistant 消息 → 回传."""
        tc = ToolCall(id="t1", name="grep", input={"pattern": "x"})
        response = ToolUseResponse(
            text="",
            tool_calls=[tc],
            stop_reason="tool_calls",
            reasoning_content="thinking about the pattern...",
        )
        asst_msg: dict = {
            "role": "assistant",
            "content": response.text or "",
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "input": tc.input}
                for tc in response.tool_calls
            ],
        }
        if response.reasoning_content:
            asst_msg["reasoning_content"] = response.reasoning_content

        messages = [asst_msg]
        result = _to_openai_messages(messages)
        assert result[0]["reasoning_content"] == "thinking about the pattern..."

    def test_roundtrip_without_reasoning_content(self):
        """无 reasoning_content 时字段不出现（避免 None 值干扰 API）."""
        tc = ToolCall(id="t1", name="grep", input={})
        response = ToolUseResponse(text="", tool_calls=[tc], stop_reason="tool_calls")
        asst_msg: dict = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "t1", "name": "grep", "input": {}}],
        }
        if response.reasoning_content:
            asst_msg["reasoning_content"] = response.reasoning_content
        result = _to_openai_messages([asst_msg])
        assert "reasoning_content" not in result[0]

    def test_user_message_passthrough(self):
        """非 assistant 消息原样透传."""
        msgs = [{"role": "user", "content": "hello"}]
        assert _to_openai_messages(msgs) == msgs

    def test_reasoning_content_empty_string_not_passed(self):
        """空字符串 '' 与 None 同行为 — 不写入 reasoning_content 字段."""
        tc = ToolCall(id="t1", name="grep", input={})
        asst_msg: dict = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "t1", "name": "grep", "input": {}}],
            "reasoning_content": "",
        }
        result = _to_openai_messages([asst_msg])
        assert "reasoning_content" not in result[0]

    def test_reasoning_content_none_explicit_not_passed(self):
        """显式 None 值 → 不写入 reasoning_content, 避免 API 收到 null."""
        tc = ToolCall(id="t1", name="grep", input={})
        asst_msg: dict = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "t1", "name": "grep", "input": {}}],
            "reasoning_content": None,
        }
        result = _to_openai_messages([asst_msg])
        assert "reasoning_content" not in result[0]


class TestMultiTurnReasoningContentIntegration:
    """模拟 agent_loop 2 轮迭代: reasoning_content → assistant msg → 回传 → 下一轮 LLM 输入."""

    def test_two_turn_reasoning_preserved(self):
        """第 1 轮 LLM 返回 reasoning_content → 存入 assistant msg → _to_openai_messages
        回传时 reasoning_content 出现在 assistant 消息中，供第 2 轮 LLM 接收."""
        tc1 = ToolCall(id="t1", name="lookup_knowledge", input={"query": "x"})
        resp1 = ToolUseResponse(
            text="let me check...",
            tool_calls=[tc1],
            stop_reason="tool_calls",
            reasoning_content="thinking about the query plan...",
        )
        asst1: dict = {
            "role": "assistant",
            "content": resp1.text,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "input": tc.input}
                for tc in resp1.tool_calls
            ],
        }
        if resp1.reasoning_content:
            asst1["reasoning_content"] = resp1.reasoning_content

        messages = [asst1]
        messages.append({
            "role": "tool",
            "tool_call_id": "t1",
            "content": '{"status": "ok", "results": [...]}',
        })

        tc2 = ToolCall(id="t2", name="execute_query", input={"sql": "SELECT ..."})
        resp2 = ToolUseResponse(
            text="found the data...",
            tool_calls=[tc2],
            stop_reason="tool_calls",
            reasoning_content="checking if this query covers the user intent...",
        )
        asst2: dict = {
            "role": "assistant",
            "content": resp2.text,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "input": tc.input}
                for tc in resp2.tool_calls
            ],
        }
        if resp2.reasoning_content:
            asst2["reasoning_content"] = resp2.reasoning_content
        messages.append(asst2)

        wire_msgs = _to_openai_messages(messages)
        assert wire_msgs[0]["reasoning_content"] == "thinking about the query plan..."
        assert wire_msgs[1]["role"] == "tool"
        assert wire_msgs[2]["reasoning_content"] == "checking if this query covers the user intent..."


class TestBuildAssistantMessage:
    """直接测试 build_assistant_message — reasoning_content 条件注入 + tool_calls 显式传入."""

    def test_injects_reasoning_content_when_present(self):
        """有 reasoning_content → msg 包含该字段."""
        tc = ToolCall(id="t1", name="grep", input={})
        resp = ToolUseResponse(
            text="let me search...",
            tool_calls=[tc],
            stop_reason="tool_calls",
            reasoning_content="thinking about the query plan...",
        )
        msg = build_assistant_message(resp)
        assert msg["role"] == "assistant"
        assert msg["content"] == "let me search..."
        assert msg["reasoning_content"] == "thinking about the query plan..."
        assert msg["tool_calls"] == [{"id": "t1", "name": "grep", "input": {}}]

    def test_omits_reasoning_content_when_none(self):
        """无 reasoning_content → msg 不含该字段."""
        tc = ToolCall(id="t1", name="lookup", input={})
        resp = ToolUseResponse(text="found", tool_calls=[tc], stop_reason="tool_calls")
        msg = build_assistant_message(resp)
        assert "reasoning_content" not in msg

    def test_omits_reasoning_content_when_empty_string(self):
        """空字符串 reasoning_content → msg 不含该字段 (与 None 同行为)."""
        tc = ToolCall(id="t1", name="read", input={})
        resp = ToolUseResponse(
            text="ok", tool_calls=[tc], stop_reason="tool_calls",
            reasoning_content="",
        )
        msg = build_assistant_message(resp)
        assert "reasoning_content" not in msg

    def test_uses_explicit_tool_calls_over_response(self):
        """显式 tool_calls 参数覆盖 response.tool_calls."""
        all_tcs = [
            ToolCall(id="t1", name="a", input={}),
            ToolCall(id="t2", name="b", input={}),
        ]
        resp = ToolUseResponse(text="", tool_calls=all_tcs, stop_reason="tool_calls")
        filtered = [all_tcs[0]]  # 模拟 processors 筛选
        msg = build_assistant_message(resp, tool_calls=filtered)
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["name"] == "a"
