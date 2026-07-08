"""G3 回归测试: 多轮 history_messages 经 Anthropic 适配后的角色转换不变量。

锁定 `_claude_tool_use` 内联的 system 拆分逻辑: 注入的成对 history_messages
(build_history_messages 产出形状 = system + N 对 user/assistant + 当前 user)
经转换后必须满足:
  1. system 消息提升到顶层 `system=` 参数, 不残留在 messages 数组
  2. messages 数组以 user 开头, user/assistant 严格交替 (Anthropic 硬约束)
  3. 最后一条是当前用户问题
"""
import pytest

from app.engine import llm


class _Sentinel(Exception):
    """哨兵异常: 在捕获 create() 入参后立即中断, 无需伪造合法 Anthropic 响应。"""


class _FakeMessages:
    def __init__(self, sink: dict):
        self._sink = sink

    def create(self, **kw):
        self._sink["system"] = kw.get("system")
        self._sink["messages"] = kw.get("messages")
        raise _Sentinel()

    def stream(self, **kw):
        # 防御性占位: _claude_tool_use 当前实现只走 .create, 此方法不会被触发。
        self._sink["system"] = kw.get("system")
        self._sink["messages"] = kw.get("messages")
        raise _Sentinel()


class _FakeClient:
    def __init__(self, sink: dict):
        self.messages = _FakeMessages(sink)


def test_claude_conversion_system_hoisted_and_alternating(monkeypatch):
    """system 提到顶层 system 参数; 成对 history 在 messages 数组严格交替."""
    sink: dict = {}
    monkeypatch.setattr(llm, "_get_claude_client", lambda cfg: _FakeClient(sink))

    cfg = {
        "protocol": "anthropic", "model_name": "claude-x",
        "api_key": "k", "base_url": "http://x",
        "temperature": 0.0, "max_tokens": 1024,
    }
    # 注入序列: system + 2 对 history + 当前 user (build_history_messages 产出形状)
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "旧问1"},
        {"role": "assistant", "content": "执行的查询：\nq1\n\n回答：\nA1"},
        {"role": "user", "content": "旧问2"},
        {"role": "assistant", "content": "A2"},
        {"role": "user", "content": "当前问题"},
    ]
    with pytest.raises(_Sentinel):
        llm._claude_tool_use(messages, tools=[], cfg=cfg)

    # 不变量 1: system 提顶层
    assert sink["system"] and "SYS" in sink["system"]

    # 不变量 2/3: messages 数组不含 system, 以 user 开头, user/assistant 严格交替
    sent = sink["messages"]
    roles = [m["role"] for m in sent]
    assert "system" not in roles
    assert roles[0] == "user"
    for i in range(len(roles) - 1):
        assert roles[i] != roles[i + 1], f"相邻同 role 破坏交替: {roles}"

    # 不变量 4: 最后一条是当前用户问题 (content 可能是字符串或 block 列表)
    assert roles[-1] == "user"
    last_content = sent[-1]["content"]
    if isinstance(last_content, str):
        assert last_content == "当前问题"
    else:
        assert any(
            (blk.get("text") if isinstance(blk, dict) else blk) == "当前问题"
            for blk in last_content
        )
