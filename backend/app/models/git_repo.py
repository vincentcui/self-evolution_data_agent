"""Git 仓库配置"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.crypto import EncryptedString
from app.models.base import Base, LOCAL_NOW


class GitRepo(Base):
    __tablename__ = "git_repos"

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(ForeignKey("namespaces.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(500))
    branch: Mapped[str] = mapped_column(String(100), default="master")
    local_path: Mapped[str] = mapped_column(Text, default="")
    # parse_status 取代原 status 列, 含义不变
    parse_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | cloning | parsing | parsed | error
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    parse_report: Mapped[str] = mapped_column(Text, default="")
    # Phase 3 新增: 后台 worker 追踪
    worker_id: Mapped[str] = mapped_column(String(36), default="")  # 活跃 worker UUID
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    progress_message: Mapped[str] = mapped_column(Text, default="")  # 当前阶段描述
    # Decomposer routing P0: 词典刷新状态追踪
    # 取值: pending | ok | partial | failed | unknown_error
    term_refresh_status: Mapped[str] = mapped_column(String(20), default="pending")
    term_refresh_stats_json: Mapped[str] = mapped_column(Text, default="{}")  # RefreshReport.to_dict() JSON
    # ── agentic extractor profile (可选纠偏) ──
    profile_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("extractor_profiles.id", ondelete="SET NULL"), nullable=True
    )
    # ── 仓库级 Git Token (EncryptedString 加密存储) ──
    git_token: Mapped[str] = mapped_column(EncryptedString, default="")
