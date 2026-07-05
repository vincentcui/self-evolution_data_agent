"""Stage 2 抓手 E — _persist_trace 单元测试.

直接调用 _persist_trace 验证 AgentTrace 行正确写入.
使用 db_session fixture (SAVEPOINT rollback 隔离).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from bson import ObjectId
from bson.timestamp import Timestamp as BsonTimestamp
from sqlalchemy import select

from app.engine.agent_loop import _persist_trace
from app.models import AgentTrace


@pytest.mark.asyncio
async def test_completed_trace_persisted(db_session):
    """_persist_trace status=completed 正确写入."""
    await _persist_trace(
        db=db_session,
        trace_id="persist-test-completed",
        namespace_id=None,
        user_query="本月订单数",
        tool_trace=[{"name": "lookup_knowledge", "input": {}, "output": {}}],
        reflection_log=[{"confidence": 0.9, "reason": "ok"}],
        status="completed",
    )

    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "persist-test-completed")
    )).scalar_one()
    assert row.status == "completed"
    assert row.user_query == "本月订单数"
    assert '"lookup_knowledge"' in row.trace_json
    assert "confidence" in row.reflection_log_json


@pytest.mark.asyncio
async def test_cancelled_trace_persisted(db_session):
    """_persist_trace status=cancelled 正确写入."""
    await _persist_trace(
        db=db_session,
        trace_id="persist-test-cancelled",
        namespace_id=None,
        user_query="取消的查询",
        tool_trace=[],
        reflection_log=[],
        status="cancelled",
    )

    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "persist-test-cancelled")
    )).scalar_one()
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_failed_trace_persisted(db_session):
    """_persist_trace status=failed 正确写入."""
    await _persist_trace(
        db=db_session,
        trace_id="persist-test-failed",
        namespace_id=None,
        user_query="失败的查询",
        tool_trace=[{"name": "fetch_schema", "input": {}, "output": "error"}],
        reflection_log=[],
        status="failed",
    )

    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "persist-test-failed")
    )).scalar_one()
    assert row.status == "failed"
    assert "fetch_schema" in row.trace_json


# ════════════════════════════════════════════
#  回归: BSON Timestamp / datetime / Decimal / ObjectId 等非 JSON 原生类型
#  根因: tool_trace 含 mongo driver 原始返回, json.dumps 默认抛 TypeError →
#  agent_traces 表常年空 (production RDS 实测 0 行).
#  修复: _persist_trace 两处 json.dumps 加 default=str.
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bson_timestamp_in_tool_output_serializes(db_session):
    """tool_trace 嵌入 bson.Timestamp 不再抛 'not JSON serializable'."""
    bson_ts = BsonTimestamp(1779776405, 1)  # 2026-05-26 06:20:05 UTC
    await _persist_trace(
        db=db_session,
        trace_id="persist-test-bson-timestamp",
        namespace_id=None,
        user_query="带 BSON Timestamp 的查询",
        tool_trace=[{
            "name": "execute_query",
            "input": {"target": "c_product", "mode": "single"},
            "output": {"rows": [{"_id": ObjectId(), "ts": bson_ts}]},
            "status": "ok",
        }],
        reflection_log=[],
        status="completed",
    )
    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "persist-test-bson-timestamp")
    )).scalar_one()
    assert row.status == "completed"
    assert "execute_query" in row.trace_json


@pytest.mark.asyncio
async def test_datetime_and_decimal_in_reflection_serializes(db_session):
    """reflection_log 含 datetime / Decimal 不再抛序列化错误."""
    await _persist_trace(
        db=db_session,
        trace_id="persist-test-datetime-decimal",
        namespace_id=None,
        user_query="带 datetime/Decimal 的反思",
        tool_trace=[],
        reflection_log=[{
            "confidence": Decimal("0.85"),
            "at": datetime(2026, 5, 26, 13, 32, 19, tzinfo=timezone.utc),
            "reason": "ok",
        }],
        status="completed",
    )
    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "persist-test-datetime-decimal")
    )).scalar_one()
    assert row.status == "completed"
    assert "0.85" in row.reflection_log_json
    assert "2026-05-26" in row.reflection_log_json


# ════════════════════════════════════════════
#  A2: trace_json 结构裁剪, 永远合法 JSON (不再 [:200000] 硬切)
# ════════════════════════════════════════════

def _big_execute_query_trace() -> list[dict]:
    """464 行 execute_query (复刻 trace 56ac6b9f iter21 场景)."""
    return [{
        "id": "tc_21",
        "name": "execute_query",
        "status": "ok",
        "input": {"target": "orders", "query": {"sql": "SELECT * FROM orders"}, "mode": "single"},
        "output": {
            "rows": [{"id": i, "agreement_execution": f"x{i}"} for i in range(464)],
            "row_count": 464,
            "truncated": False,
            "columns": ["id", "agreement_execution"],
        },
    }]


@pytest.mark.asyncio
async def test_persist_trace_big_rows_produces_valid_json(db_session, monkeypatch):
    """464 行落库后 trace_json 可 json.loads, 不再被 [:200000] 腰斩.

    464 行 execute_query 序列化后仅约 20KB, 低于默认 200_000 字节预算线不会
    触发结构裁剪; 调小 agent_trace_max_json_bytes 强制走裁剪分支, 复刻生产
    大 trace (schema/多轮结果累积后) 实际触发场景.
    """
    import json as _json

    from app.engine import agent_loop as al
    monkeypatch.setattr(al.settings, "agent_trace_max_json_bytes", 200)

    await _persist_trace(
        db=db_session,
        trace_id="test-trace-big-rows",
        namespace_id=None,
        user_query="big rows test",
        tool_trace=_big_execute_query_trace(),
        reflection_log=[],
        status="completed",
    )
    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "test-trace-big-rows")
    )).scalar_one()
    # 核心断言: 不再 JSONDecodeError
    parsed = _json.loads(row.trace_json)
    tt = parsed["tool_trace"]
    assert len(tt) == 1
    assert tt[0]["output"]["trace_rows_truncated"] is True
    assert tt[0]["output"]["trace_rows_kept"] == 20
    assert tt[0]["output"]["row_count"] == 464


@pytest.mark.asyncio
async def test_persist_trace_small_trace_full_rows(db_session):
    """小 trace 不触发裁剪, rows 完整保留."""
    import json as _json

    small_trace = [{
        "id": "tc_1", "name": "execute_query", "status": "ok",
        "input": {}, "output": {"rows": [{"id": 1}, {"id": 2}], "row_count": 2},
    }]
    await _persist_trace(
        db=db_session, trace_id="test-trace-small",
        namespace_id=None, user_query="small",
        tool_trace=small_trace, reflection_log=[], status="completed",
    )
    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "test-trace-small")
    )).scalar_one()
    parsed = _json.loads(row.trace_json)
    assert len(parsed["tool_trace"][0]["output"]["rows"]) == 2
    assert "trace_rows_truncated" not in parsed["tool_trace"][0]["output"]


@pytest.mark.asyncio
async def test_persist_trace_reflection_not_sliced(db_session):
    """reflection_log_json 不再 [:N] 硬切, 原样序列化."""
    import json as _json

    big_reflection = [{"iter": i, "confidence": 0.5, "reason": "x" * 200} for i in range(500)]
    await _persist_trace(
        db=db_session, trace_id="test-trace-refl",
        namespace_id=None, user_query="refl",
        tool_trace=[], reflection_log=big_reflection, status="completed",
    )
    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "test-trace-refl")
    )).scalar_one()
    parsed = _json.loads(row.reflection_log_json)
    assert len(parsed) == 500  # 没被腰斩
