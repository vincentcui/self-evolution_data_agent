"""全局 Git Token 配置中心 — DB 存储的加密 Git 访问令牌"""
from datetime import datetime
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.crypto import EncryptedString
from app.models.base import Base, local_now


class GitTokenConfig(Base):
    __tablename__ = "git_token_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    token: Mapped[str] = mapped_column(EncryptedString, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=local_now)
    updated_at: Mapped[datetime | None] = mapped_column(default=None)
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
