"""Session ORM 模型 — 对话会话持久化.

每个会话绑定一个命名空间, 用户可在同一命名空间下创建、切换、重命名、删除会话.
删除会话时 API 层手动级联删除关联 QueryHistory，不依赖 DB 层 FK 约束.
"""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import LOCAL_NOW, Base, local_now


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), default="新会话")
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=LOCAL_NOW, default=local_now,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=LOCAL_NOW, onupdate=local_now,
    )
