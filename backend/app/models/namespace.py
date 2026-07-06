"""
命名空间 + 数据源
命名空间是隔离域, 数据源是连接凭证.

Stage 1 Task 14: NamespaceRule 已废弃, 规则统一走 KnowledgeEntry[entry_type=rule].
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.crypto import EncryptedString
from app.models.base import Base, LOCAL_NOW, local_now


class Namespace(Base):
    __tablename__ = "namespaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=LOCAL_NOW, default=local_now,
    )

    # ── 关联 ──
    datasources: Mapped[list["DataSource"]] = relationship(
        back_populates="namespace", cascade="all, delete-orphan"
    )


class DataSource(Base):
    """数据源连接配置"""
    __tablename__ = "datasources"

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(ForeignKey("namespaces.id", ondelete="CASCADE"))
    db_type: Mapped[str] = mapped_column(String(20))  # mysql | oracle | mongodb
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int]
    database: Mapped[str] = mapped_column(String(100))
    username: Mapped[str] = mapped_column(String(100))
    password: Mapped[str] = mapped_column(EncryptedString)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    db_profile_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=LOCAL_NOW, default=local_now,
    )

    namespace: Mapped["Namespace"] = relationship(back_populates="datasources")
