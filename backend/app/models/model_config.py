"""模型配置持久化 ORM — 对应 model_configs 表."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.crypto import EncryptedString
from app.models.base import LOCAL_NOW, Base


class ModelConfig(Base):
    """LLM / Embedding 模型配置，支持多厂商、热切换."""

    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # ── 厂商与接入 ────────────────────────────────────────────
    provider: Mapped[str] = mapped_column(String(64))
    """厂商标识，如 openai / deepseek / qwen / siliconflow / custom"""

    base_url: Mapped[str] = mapped_column(String(512))
    """API 基础地址，如 https://api.openai.com"""

    api_key: Mapped[str] = mapped_column(EncryptedString)
    """API 密钥，Fernet 加密存储（读写对 Python 层透明）"""

    model_name: Mapped[str] = mapped_column(String(128))
    """模型名称，如 gpt-4o / deepseek-chat / text-embedding-v4"""

    # ── 类型与参数 ────────────────────────────────────────────
    model_type: Mapped[str] = mapped_column(String(20), default="CHAT")
    """CHAT | EMBEDDING"""

    temperature: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True, default=0.0)
    """温度（0-2），仅 CHAT 有效"""

    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=12288)
    """最大输出 Token，仅 CHAT 有效"""

    # ── 路径覆盖（兼容非标准厂商）────────────────────────────
    completions_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    """Chat 路径，默认 /v1/chat/completions"""

    embeddings_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    """Embedding 路径，默认 /v1/embeddings"""

    # ── HTTP 代理 ──────────────────────────────────────────────
    proxy_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    proxy_host: Mapped[str | None] = mapped_column(String(256), nullable=True)
    proxy_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    proxy_password: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)

    # ── 协议 ─────────────────────────────────────────────────
    protocol: Mapped[str] = mapped_column(String(32), default="openai")
    """协议标识：openai | anthropic"""

    # ── 状态 ─────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    """同类型同时只有一条 is_active=True"""

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    """逻辑删除"""

    # ── 审计 ─────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
