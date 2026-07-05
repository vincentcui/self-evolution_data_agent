"""Session CRUD API — 对话会话管理.

审计策略: session 生命周期事件通过结构化日志记录 (best-effort).
若未来需要不可变审计链，可升级为 SessionAuditLog 表 + migration.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import assert_ns_access, get_current_user
from app.db.metadata import get_db
from app.models.query_history import QueryHistory
from app.models.session import Session
from app.models.user import User
from app.schemas import SessionCreate, SessionOut, SessionUpdate

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions", tags=["sessions"])


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
    """列出当前用户在指定命名空间下的会话，按更新时间倒序，最多 50 条."""
    result = await db.execute(
        select(Session)
        .where(
            Session.namespace_id == namespace_id,
            Session.created_by == _user.id,
        )
        .order_by(Session.updated_at.desc())
        .limit(50)
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
    from uuid import UUID

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

    级联删除关联的 query_history: BulkOperationGuard 豁免 —
    单会话作用域内的数据完整性级联, 非用户面批量操作.
    """
    from uuid import UUID

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

    # 级联删除关联的 query_history 记录，避免孤儿数据
    sid_str = str(sid)
    cascade_result = await db.execute(
        delete(QueryHistory).where(QueryHistory.session_id == sid_str)
    )
    await db.delete(session)
    await db.commit()
    log.info(
        "[audit] session_delete session_id=%s namespace_id=%d user_id=%d "
        "cascaded_history_rows=%d",
        session.id, session.namespace_id, user.id, cascade_result.rowcount,
    )
