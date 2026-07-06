"""_session_cleanup 测试 — SSE 取消链路的被终止对话持久化.

回归 code-review important: write_cancelled_history 是 cancel 盲区的实际修复,
全 PR 最高风险代码, 此前零直测。覆盖纯函数 + trace 缺失边界 + 端到端落库。

隔离说明: _session_cleanup 用 app.db.metadata.async_session (独立 session + 真 commit,
绕过 test db fixture 的 SAVEPOINT 回滚)。测试 monkeypatch _new_db_session 指向 test 事务
session, 使内部 commit 落到 savepoint, 测试结束随事务回滚, 不污染真库。
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select

from app.api import _session_cleanup as sc
from app.models.agent_trace import AgentTrace
from app.models.query_history import QueryHistory

_SESSION_ID = "aaaaaaaa-0000-0000-0000-000000000001"


def _ok_exec_trace() -> list[dict]:
    """构造一条 execute_query status=ok 的 tool_trace."""
    return [
        {"name": "some_read_tool", "status": "ok", "output": {}},
        {
            "name": "execute_query",
            "status": "ok",
            "input": {"query": "SELECT id FROM orders"},
            "output": {
                "rows": [{"id": 1}, {"id": 2}],
                "columns": ["id"],
                "row_count": 2,
            },
        },
    ]


# ── 纯函数 (零 DB) ──

def test_last_exec_tool_extracts_last_ok():
    """从 tool_trace 提取最后一条成功 exec 的 query / rows / count."""
    out = sc._last_exec_tool_from_trace(_ok_exec_trace())
    assert out["row_count"] == 2
    assert out["rows_data"] == [{"id": 1}, {"id": 2}]
    assert out["columns_data"] == ["id"]
    assert "SELECT id FROM orders" in out["generated_query"]


def test_last_exec_tool_infers_columns_from_rows():
    """output 无 columns 时从首行 dict 键推断列名."""
    trace = [{
        "name": "execute_query", "status": "ok", "input": {},
        "output": {"rows": [{"a": 1, "b": 2}], "row_count": 1},
    }]
    out = sc._last_exec_tool_from_trace(trace)
    assert out["columns_data"] == ["a", "b"]


def test_last_exec_tool_empty_when_no_ok_exec():
    """无成功 exec (全失败 / 无 exec 工具) → 空默认值."""
    trace = [
        {"name": "execute_query", "status": "error", "output": {}},
        {"name": "fetch_schema", "status": "ok", "output": {}},
    ]
    out = sc._last_exec_tool_from_trace(trace)
    assert out == {
        "generated_query": "", "row_count": 0, "rows_data": [], "columns_data": [],
    }


def test_build_snapshot_shape():
    """cancelled result_snapshot 契约字段齐全 + error/stop_reason=cancelled."""
    snap = sc._build_cancelled_result_snapshot(
        session_id=_SESSION_ID,
        generated_query="SELECT 1",
        columns_data=["id"],
        rows_data=[{"id": 1}],
        row_count=1,
        tool_trace=_ok_exec_trace(),
    )
    assert snap["error"] == "cancelled"
    assert snap["stop_reason"] == "cancelled"
    assert snap["final_answer"] == "(对话已被终止)"
    assert snap["rendered_row_count"] == 1
    assert snap["iterations"] == len(_ok_exec_trace())


# ── 端到端 (monkeypatch 路由到 test 事务) ──

@pytest.fixture
def _route_cleanup_to_test_db(db, monkeypatch):
    """把 _session_cleanup 的独立 session 工厂改指向 test 事务 session (不关闭)."""
    @asynccontextmanager
    async def _cm():
        yield db  # 事务归 db fixture 管, 此处不 close 不 rollback

    monkeypatch.setattr(sc, "_new_db_session", lambda: _cm())
    return db


@pytest.mark.asyncio
async def test_write_cancelled_history_persists_partial_progress(
    _route_cleanup_to_test_db,
):
    """AgentTrace 存在 → 写一条 cancelled QueryHistory, 含部分执行过程."""
    db = _route_cleanup_to_test_db
    db.add(AgentTrace(
        trace_id="trace-cancel-1",
        session_id=_SESSION_ID,
        namespace_id=None,
        user_query="统计订单数",
        trace_json=json.dumps({"tool_trace": _ok_exec_trace()}),
    ))
    await db.commit()

    entry_id = await sc.write_cancelled_history(
        trace_id="trace-cancel-1",
        namespace_id=7,
        session_id=_SESSION_ID,
        question="统计订单数",
    )
    assert entry_id is not None

    row = await db.scalar(
        select(QueryHistory).where(QueryHistory.id == entry_id)
    )
    assert row is not None
    assert row.error == "cancelled"
    assert row.session_id == _SESSION_ID
    assert row.row_count == 2
    assert "SELECT id FROM orders" in row.generated_query
    snapshot = json.loads(row.result_snapshot)
    assert snapshot["stop_reason"] == "cancelled"
    assert snapshot["final_answer"] == "(对话已被终止)"


@pytest.mark.asyncio
async def test_write_cancelled_history_when_trace_missing(
    _route_cleanup_to_test_db,
):
    """AgentTrace 缺失 (取消发生在首个 trace 落库前) → 仍写最小 cancelled 记录."""
    db = _route_cleanup_to_test_db

    entry_id = await sc.write_cancelled_history(
        trace_id="trace-does-not-exist",
        namespace_id=7,
        session_id=_SESSION_ID,
        question="早退问题",
    )
    assert entry_id is not None

    row = await db.scalar(
        select(QueryHistory).where(QueryHistory.id == entry_id)
    )
    assert row.error == "cancelled"
    assert row.row_count == 0
    assert row.generated_query == ""
    # 未写入多余行
    total = await db.scalar(
        select(func.count()).select_from(QueryHistory)
        .where(QueryHistory.session_id == _SESSION_ID)
    )
    assert total == 1
