"""query_stream 多轮上下文接入回归测试 (Task 4).

覆盖: query_stream 调 history_loader.build_history_messages(session_id, ns.id,
max_turns) 并把结果透传给 run_agent_loop(history_messages=...)。
真实 http client (make_client), monkeypatch loader + agent_loop 隔离 LLM 依赖。
"""
from unittest.mock import AsyncMock

import pytest

from app.engine import history_loader
from app.models.namespace import Namespace


@pytest.mark.asyncio
async def test_query_stream_passes_history_to_agent_loop(monkeypatch, db, make_client):
    """query_stream 调 build_history_messages 并把结果透传给 run_agent_loop.

    _write_query_history 走独立 db session (`_new_db_session`), 与测试事务
    隔离的 `db` fixture 连接不是同一个 — 测试事务内的 ns 对它不可见,
    真跑会撞 FK 违例。此处 monkeypatch 之 (同 tests/sse/test_sse_endpoint.py
    既有模式), 聚焦本测试目标: history_messages 透传, 不验证历史落库。
    """
    ns = Namespace(name="q-ns", slug="q-ns")
    db.add(ns)
    await db.flush()

    captured: dict = {}

    async def fake_build(dbarg, session_id, namespace_id, max_turns):
        captured["session_id"] = session_id
        captured["ns_id"] = namespace_id
        captured["max_turns"] = max_turns
        return [{"role": "user", "content": "旧问"},
                {"role": "assistant", "content": "旧答"}]
    monkeypatch.setattr(history_loader, "build_history_messages", fake_build)

    async def fake_run(**kw):
        captured["history_messages"] = kw.get("history_messages")
        from app.engine.agent_loop import AgentResult
        return AgentResult(
            final_answer="ok", iterations=1, stop_reason="end_turn",
            tool_trace=[], usage_total={"input_tokens": 0, "output_tokens": 0},
        )
    monkeypatch.setattr("app.api.query.run_agent_loop", fake_run)
    monkeypatch.setattr(
        "app.api.query._write_query_history", AsyncMock(return_value=42),
    )

    client = await make_client(role="super_admin", user_id=1)
    resp = await client.post("/api/query/stream", json={
        "namespace_id": ns.id, "question": "当前问题", "session_id": "sess-multi",
    })
    assert resp.status_code == 200, resp.text
    assert captured["session_id"] == "sess-multi"
    assert captured["ns_id"] == ns.id
    assert isinstance(captured["max_turns"], int)  # 来自 chat_config 或 DEFAULT fallback
    assert captured["history_messages"] == [
        {"role": "user", "content": "旧问"},
        {"role": "assistant", "content": "旧答"},
    ]
