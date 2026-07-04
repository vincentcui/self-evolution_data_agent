"""Feedback API — 答案喜欢/不喜欢反馈."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import assert_ns_access, get_current_user
from app.db.metadata import get_db
from app.models.query_history import QueryHistory
from app.models.user import User
from app.schemas import FeedbackCreate

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", status_code=201)
async def submit_feedback(
    body: FeedbackCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """提交答案反馈。重复调用 → UPDATE 不重复创建."""
    if body.rating not in ("like", "dislike"):
        raise HTTPException(status_code=422, detail="rating 必须为 'like' 或 'dislike'")

    result = await db.execute(
        select(QueryHistory).where(QueryHistory.id == body.history_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="历史记录不存在")

    # 校验用户对该记录所属空间有访问权限
    await assert_ns_access(db, user, entry.namespace_id)

    entry.feedback_rating = body.rating
    await db.commit()

    return {"status": "ok", "rating": body.rating}
