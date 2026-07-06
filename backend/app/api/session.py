"""Session CRUD API — 对话会话管理.

审计策略: session 生命周期事件通过结构化日志记录 (best-effort).
若未来需要不可变审计链，可升级为 SessionAuditLog 表 + migration.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import assert_ns_access, get_current_user
from app.config import settings
from app.db.metadata import get_db
from app.models.agent_trace import AgentTrace
from app.models.query_history import QueryHistory
from app.models.session import Session
from app.models.user import User
from app.schemas import SessionCreate, SessionOut, SessionUpdate

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions", tags=["sessions"])


async def _get_owned_session(db: AsyncSession, session_id: str, user: User) -> Session:
    """解析 session_id、加载会话、校验归属，返回会话对象.

    非法 UUID / 不存在 → 404；非创建者 → 403。rename 与 delete 共用此守卫.
    """
    try:
        sid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="会话不存在")

    result = await db.execute(select(Session).where(Session.id == sid))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.created_by != user.id:
        raise HTTPException(status_code=403, detail="无权操作此会话")
    return session


@router.post("", response_model=SessionOut, status_code=201)
async def create_session(
    body: SessionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建会话，标题默认为'新会话'."""
    await assert_ns_access(db, user, body.namespace_id)
    session = Session(
        namespace_id=body.namespace_id,
        created_by=user.id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    log.info(
        "[audit] session_create session_id=%s namespace_id=%d user_id=%d",
        session.id, body.namespace_id, user.id,
    )
    return session


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    namespace_id: int,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户在指定命名空间下的会话，按更新时间倒序 (上限 IS_SESSION_LIST_MAX)."""
    result = await db.execute(
        select(Session)
        .where(
            Session.namespace_id == namespace_id,
            Session.created_by == _user.id,
        )
        .order_by(Session.updated_at.desc())
        .limit(settings.session_list_max)
    )
    return result.scalars().all()


@router.patch("/{session_id}", response_model=SessionOut)
async def rename_session(
    session_id: str,
    body: SessionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """重命名会话。仅创建者可操作."""
    session = await _get_owned_session(db, session_id, user)
    old_title = session.title
    session.title = body.title
    await db.commit()
    await db.refresh(session)
    log.info(
        "[audit] session_rename session_id=%s old_title=%r new_title=%r user_id=%d",
        session.id, old_title, body.title, user.id,
    )
    return session


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除会话（硬删）。仅创建者可操作.

    同事务级联删除同 session_id 的 query_history + agent_traces，杜绝孤儿数据:
    二者的 session_id 均由 query.py 写入同一会话 UUID (QueryHistory 与 AgentTrace
    共享该键)。BulkOperationGuard 豁免 — 单会话作用域内的完整性级联, 非用户面批量操作.
    """
    session = await _get_owned_session(db, session_id, user)
    # 删除前捕获审计字段, 日志语句无需在 delete 后访问已删对象
    sid_str = str(session.id)
    ns_id = session.namespace_id

    hist_result = await db.execute(
        delete(QueryHistory).where(QueryHistory.session_id == sid_str)
    )
    trace_result = await db.execute(
        delete(AgentTrace).where(AgentTrace.session_id == sid_str)
    )
    await db.delete(session)
    await db.commit()
    log.info(
        "[audit] session_delete session_id=%s namespace_id=%d user_id=%d "
        "cascaded_history_rows=%d cascaded_trace_rows=%d",
        sid_str, ns_id, user.id,
        int(getattr(hist_result, "rowcount", 0)),
        int(getattr(trace_result, "rowcount", 0)),
    )
