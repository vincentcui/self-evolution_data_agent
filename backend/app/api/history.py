"""查询历史 API"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_ns_access
from app.db.metadata import get_db
from app.models import QueryHistory
from app.models.user import User
from app.schemas import QueryHistoryOut

router = APIRouter(prefix="/api/namespaces", tags=["history"])


@router.get("/{ns_id}/history", response_model=list[QueryHistoryOut])
async def list_history(
    ns_id: int,
    limit: int = 50,
    session_id: str | None = None,
    user: User = Depends(require_ns_access),
    db: AsyncSession = Depends(get_db),
):
    """
    查询历史记录 — 需要命名空间访问权限
    - Admin 跳过检查
    - User 需要 user_namespace_access 表授权
    - 可选 session_id 过滤
    """
    stmt = select(QueryHistory).where(QueryHistory.namespace_id == ns_id)
    if session_id:
        stmt = stmt.where(QueryHistory.session_id == session_id)
    result = await db.execute(
        stmt.order_by(QueryHistory.created_at.asc()).limit(limit)
    )
    return result.scalars().all()
