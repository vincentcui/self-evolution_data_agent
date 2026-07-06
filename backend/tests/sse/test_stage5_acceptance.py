"""Stage 5 验收门 — 对齐 05-stage-roadmap.md §Stage 5 验收门."""
import asyncio
import json
import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.engine.agent_loop import AgentResult
from app.engine.sse_manager import register_sse_session, deregister_sse_session
from app.auth import get_current_user
from app.db.metadata import get_db


@pytest.fixture(autouse=True)
def override_deps(db_session, admin_user):
    async def _fake_db():
        yield db_session

    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.clear()


# ── 验收门 1: SSE 事件序列完整 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sse_events_include_all_required_types(test_namespace):
    """验证 SSE 流包含所有必要事件类型: agent_started / text_delta / tool_use /
    tool_result / agent_finished / final_answer."""
    fake_result = AgentResult(
        final_answer="42", iterations=1, stop_reason="end_turn",
        tool_trace=[{
            "name": "execute_batched_aggregate",
            "input": {},
            "output": {"row_count": 1},
            "status": "ok",
        }],
        usage_total={},
    )

    async def fake_agent(**kwargs):
        emit = kwargs["sse_emit"]
        await emit({"event": "agent_started", "data": {"trace_id": "acc1"}})
        await emit({"event": "text_delta", "data": {"delta": "Thinking..."}})
        await emit({"event": "tool_use", "data": {
            "tool_call_id": "tc1",
            "name": "execute_batched_aggregate",
            "input": {},
        }})
        await emit({"event": "tool_result", "data": {
            "tool_call_id": "tc1",
            "name": "execute_batched_aggregate",
            "output": "1 row",
            "status": "ok",
        }})
        await emit({"event": "agent_finished", "data": {
            "total_iterations": 1,
            "total_tool_calls": 1,
            "stop_reason": "end_turn",
        }})
        return fake_result

    with patch("app.api.query.run_agent_loop", fake_agent), \
         patch("app.api.query._write_query_history", AsyncMock(return_value=1)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            chunks = []
            async with ac.stream(
                "POST", "/api/query/stream",
                json={"namespace_id": test_namespace.id, "question": "test"},
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
                async for chunk in resp.aiter_text():
                    chunks.append(chunk)

    full = "".join(chunks)
    for event in ["agent_started", "text_delta", "tool_use", "tool_result", "agent_finished", "final_answer"]:
        assert f"event: {event}" in full, f"Missing SSE event: {event}\nFull: {full[:500]}"


# ── 验收门 2: correction_queue + stream/status 端点完整闭合 ─────────────────────
# 说明: ASGI 测试传输层不能可靠触发 GeneratorExit (httpx 客户端断连不等于 TCP close).
# 改为验证反向通道的完整闭合: SSE 注册 → /correct abort 入队 → /status 状态读取 → 注销.
# (G3 cancel 路径 < 1s 已由 tests/agent_loop/test_cancel_hooks.py 覆盖)

@pytest.mark.asyncio
async def test_correction_queue_and_status_endpoint(test_namespace):
    """验证 SSE session 反向通道完整闭合:
    1. 手动注册 session
    2. /correct abort 返回 200 并入队
    3. GET /status 显示 sse_connected=True
    4. 注销后 /status 显示 sse_connected=False
    """
    trace_id = "acceptance-status-test"
    event_q, correction_q = register_sse_session(trace_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 验证 abort 入队
            abort_resp = await ac.post(
                f"/api/query/stream/{trace_id}/correct",
                json={"correction_type": "abort", "step_id": ""},
            )
            assert abort_resp.status_code == 200
            queued = correction_q.get_nowait()
            assert queued["correction_type"] == "abort"

            # 验证 status 显示 sse_connected=True
            status_resp = await ac.get(f"/api/query/stream/{trace_id}/status")
            assert status_resp.status_code == 200
            status_body = status_resp.json()
            assert status_body["sse_connected"] is True
            assert status_body["trace_id"] == trace_id
    finally:
        deregister_sse_session(trace_id)

    # 注销后 sse_connected=False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        status_resp = await ac.get(f"/api/query/stream/{trace_id}/status")
    assert status_resp.json()["sse_connected"] is False


# ── 验收门 3: active_workers 管理 API 可用 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_active_workers_admin_api():
    """GET /api/query/active_workers 必须返回 200 并含 trace_ids 字段."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/query/active_workers")
    assert resp.status_code == 200
    body = resp.json()
    assert "trace_ids" in body
    assert "count" in body
    assert isinstance(body["trace_ids"], list)
