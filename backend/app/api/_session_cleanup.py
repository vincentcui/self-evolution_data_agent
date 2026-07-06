"""会话清理 — SSE 取消链路恢复 & 被终止对话持久化.

query.py 取消事件的检查点守卫 (checkpoint guard) 和 cancelled history 写入逻辑,
从 query.py 抽离以控制模块尺寸在 800 行内.
"""

import json
import logging

from sqlalchemy import select as sa_select

from app.db.metadata import async_session as _new_db_session
from app.engine.tools.registry import EXEC_TOOLS
from app.models import QueryHistory
from app.models.agent_trace import AgentTrace

log = logging.getLogger(__name__)


def _last_exec_tool_from_trace(tool_trace: list[dict]) -> dict:
    """从 tool_trace 中提取最后一条成功执行的 SQL.

    Returns {generated_query, row_count, rows_data, columns_data}.
    """
    for tr in reversed(tool_trace):
        if tr.get("name") in EXEC_TOOLS and tr.get("status") == "ok":
            out = tr.get("output") or {}
            rows_data = out.get("rows", [])
            columns_data = out.get("columns", [])
            if not columns_data and rows_data and isinstance(rows_data[0], dict):
                columns_data = list(rows_data[0].keys())
            return {
                "generated_query": json.dumps(
                    tr.get("input", {}), ensure_ascii=False, default=str,
                ),
                "row_count": int(
                    out.get("row_count", 0) or out.get("count", 0) or 0
                ),
                "rows_data": rows_data,
                "columns_data": columns_data,
            }
    return {"generated_query": "", "row_count": 0, "rows_data": [], "columns_data": []}


async def _extract_cancelled_trace_data(trace_id: str) -> dict:
    """从 AgentTrace 中提取被取消对话的工具执行数据.

    返回 dict: tool_trace, generated_query, row_count, rows_data, columns_data.
    AgentTrace 不存在时返回空数据 (最小记录模式).
    """
    empty: dict = {
        "tool_trace": [],
        "generated_query": "",
        "row_count": 0,
        "rows_data": [],
        "columns_data": [],
    }

    async with _new_db_session() as new_db:
        result = await new_db.execute(
            sa_select(AgentTrace).where(AgentTrace.trace_id == trace_id)
        )
        trace_row = result.scalar_one_or_none()
        if trace_row is None:
            log.warning(
                "_extract_cancelled_trace_data AgentTrace not found trace=%s",
                trace_id,
            )
            return empty

        trace_data = json.loads(trace_row.trace_json or "{}")
        tool_trace = trace_data.get("tool_trace", [])
        exec_data = _last_exec_tool_from_trace(tool_trace)

        return {
            "tool_trace": tool_trace,
            **exec_data,
        }


def _build_cancelled_result_snapshot(
    *,
    session_id: str,
    generated_query: str,
    columns_data: list[str],
    rows_data: list[dict],
    row_count: int,
    tool_trace: list[dict],
) -> dict:
    """构建 cancelled 状态的 result_snapshot — 前端恢复部分工具执行过程."""
    return {
        "session_id": session_id,
        "history_id": 0,
        "needs_clarification": False,
        "clarification_message": "",
        "generated_query": generated_query,
        "columns": columns_data,
        "rows": rows_data,
        "row_count": row_count,
        "chart_type": "table",
        "category_column": "",
        "chart_option": {},
        "truncated": False,
        "rendered_row_count": len(rows_data),
        "total_row_count": len(rows_data),
        "performance_warning": "",
        "error": "cancelled",
        "clarification_questions": [],
        "pending_id": 0,
        "final_answer": "(对话已被终止)",
        "iterations": len(tool_trace),
        "stop_reason": "cancelled",
        "tool_trace": tool_trace,
    }


async def _build_cancelled_history_entry(
    *,
    namespace_id: int,
    session_id: str,
    question: str,
    data: dict,
) -> int | None:
    """构建并持久化一条 cancelled 状态的 QueryHistory 记录."""
    snapshot = _build_cancelled_result_snapshot(
        session_id=session_id,
        generated_query=data["generated_query"],
        columns_data=data["columns_data"],
        rows_data=data["rows_data"],
        row_count=data["row_count"],
        tool_trace=data["tool_trace"],
    )

    async with _new_db_session() as new_db:
        entry = QueryHistory(
            namespace_id=namespace_id,
            session_id=session_id,
            role="assistant",
            content=question,
            generated_query=data["generated_query"],
            row_count=data["row_count"],
            error="cancelled",
            result_snapshot=json.dumps(snapshot, ensure_ascii=False, default=str),
        )
        new_db.add(entry)
        await new_db.commit()
        await new_db.refresh(entry)
        return entry.id


async def write_cancelled_history(
    *,
    trace_id: str,
    namespace_id: int,
    session_id: str,
    question: str,
) -> int | None:
    """被取消/终止的对话也写入 query_history，刷新后可查看部分过程."""
    data = await _extract_cancelled_trace_data(trace_id)
    entry_id = await _build_cancelled_history_entry(
        namespace_id=namespace_id,
        session_id=session_id,
        question=question,
        data=data,
    )
    log.info(
        "write_cancelled_history done trace=%s history_id=%s tools=%d",
        trace_id, entry_id, len(data["tool_trace"]),
    )
    return entry_id
