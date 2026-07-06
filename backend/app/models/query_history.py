"""查询历史 + 多轮对话 session"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW


class QueryHistory(Base):
    __tablename__ = "query_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(ForeignKey("namespaces.id", ondelete="CASCADE"))
    session_id: Mapped[str] = mapped_column(String(36), index=True)  # UUID, 同一轮对话共享
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)  # 用户原文 / 生成的 SQL / 追问
    generated_query: Mapped[str] = mapped_column(Text, default="")  # SQL 或 MongoDB query JSON
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    result_snapshot: Mapped[str] = mapped_column(Text, default="")  # JSON blob: 完整查询结果快照
    feedback_rating: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 'like'|'dislike'|None
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
