"""模型配置审计日志 ORM."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import LOCAL_NOW, Base


class ModelConfigAuditLog(Base):
    __tablename__ = "model_config_audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    config_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(40))
    model_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
