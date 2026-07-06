"""Stage 3 Task 8 — proposed 自动过期后台任务 (RED → GREEN, 真实 SQLite)."""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.knowledge.audit import list_audit_logs
from app.knowledge.auto_expire import (
    expire_stale_proposed,
    proposed_auto_expire_loop,
)
from app.models import KnowledgeEntry


# ──────────────────────────────────────────────────────────────────────
# Case 1: 未到期 proposed 不动
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_not_expired_yet_no_change(db_session):
    entry = KnowledgeEntry(
        entry_type="terminology",
        content="新术语",
        source="manual",
        status="proposed",
    )
    db_session.add(entry)
    await db_session.commit()

    n = await expire_stale_proposed(db_session)

    assert n == 0
    refreshed = await db_session.scalar(
        select(KnowledgeEntry).where(KnowledgeEntry.id == entry.id)
    )
    assert refreshed.status == "proposed"


# ──────────────────────────────────────────────────────────────────────
# Case 2: 超期 proposed → rejected + audit_log 一条
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_expired_proposed_marked_rejected_with_audit(db_session):
    stale = KnowledgeEntry(
        entry_type="rule",
        content="超期 proposed",
        source="manual",
        status="proposed",
        created_at=datetime.now() - timedelta(days=31),
    )
    db_session.add(stale)
    await db_session.commit()

    n = await expire_stale_proposed(db_session)

    assert n == 1
    refreshed = await db_session.scalar(
        select(KnowledgeEntry).where(KnowledgeEntry.id == stale.id)
    )
    assert refreshed.status == "rejected"

    logs = await list_audit_logs(db_session, entry_id=stale.id)
    assert len(logs) == 1
    log = logs[0]
    assert log.action == "expire"
    assert log.from_status == "proposed"
    assert log.to_status == "rejected"
    assert log.actor_id is None
    assert log.reason == "auto-expired"


# ──────────────────────────────────────────────────────────────────────
# Case 3: 后台 loop 异常被隔离 — 异常一次后下轮仍能跑
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_loop_exception_isolated_from_event_loop(monkeypatch, db_session):
    call_count = {"n": 0}

    async def boom(*_a, **_kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated db failure")
        # 第二轮起返回真实 session 上下文
        from app.db.metadata import async_session as real

        return real()

    # monkeypatch 模块级 async_session 引用
    import app.knowledge.auto_expire as mod

    # 仿 async context manager: 第一次进入即抛, 第二次返回 real session
    class _Wrap:
        def __init__(self):
            self.real_cm = None

        async def __aenter__(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated db failure")
            from app.db.metadata import async_session as real

            self.real_cm = real()
            return await self.real_cm.__aenter__()

        async def __aexit__(self, *exc):
            if self.real_cm is not None:
                return await self.real_cm.__aexit__(*exc)
            return False

    monkeypatch.setattr(mod, "async_session", lambda: _Wrap())

    task = asyncio.create_task(proposed_auto_expire_loop(interval_secs=0))
    try:
        await asyncio.sleep(0.3)
        assert not task.done(), f"loop 不应退出, 异常应被隔离; exc={task.exception() if task.done() else None}"
        assert call_count["n"] >= 2, "异常后下一轮应继续"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
