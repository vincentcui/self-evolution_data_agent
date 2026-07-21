"""Schema canonical conflict — 多候选不一致时的人工解决工作流.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/02-data-model.md §2.2
普通候选以字段为 conflict scope；relationship 追加目标库、目标表与关系类型。
这样同一源字段可保存多条指向不同目标的关系，同时每个逻辑关系仍只有一个 open conflict。
"""
import json
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import LOCAL_NOW, Base

ConflictType = Literal["field_value", "semantic_equivalent"]
ConflictStatus = Literal["open", "resolved"]
ResolutionChoice = Literal["keep_a", "keep_b", "merge", "reject_all"]


def build_conflict_scope(
    field_key: tuple[str, str, str, str, str],
    candidate_value: dict[str, Any] | None = None,
) -> str:
    """返回稳定的 conflict identity hash。

    relationship 与 canonical relationship 的去重键保持一致：to_field 是质量信息，
    不参与身份判定；不同 to_target / relation_type 的关系可独立共存。
    """
    scope_parts: list[Any] = list(field_key)
    if field_key[-1] == "relationship" and candidate_value is not None:
        scope_parts.extend(
            (
                candidate_value.get("to_db_type"),
                candidate_value.get("to_database"),
                candidate_value.get("to_target"),
                candidate_value.get("relation_type"),
            )
        )
    encoded = json.dumps(scope_parts, ensure_ascii=False, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


class SchemaCanonicalConflict(Base):
    __tablename__ = "schema_canonical_conflicts"
    __table_args__ = (
        Index("idx_conflict_open", "namespace_id", "status"),
        # 仅 status='open' 行参与唯一约束；relationship 的 scope 包含目标身份。
        Index(
            "uq_one_open_conflict_per_field",
            "namespace_id", "conflict_scope",
            unique=True,
            postgresql_where=text("status = 'open'"),
            sqlite_where=text("status = 'open'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), index=True
    )

    db_type: Mapped[str] = mapped_column(String(16))
    database: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(200))
    field_path: Mapped[str] = mapped_column(String(200), default="")
    candidate_kind: Mapped[str] = mapped_column(String(32))
    conflict_scope: Mapped[str] = mapped_column(String(64))

    conflict_type: Mapped[str] = mapped_column(String(32))
    candidate_ids_json: Mapped[str] = mapped_column(Text)
    candidates_snapshot_json: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(16), default="open")
    resolution_choice: Mapped[str | None] = mapped_column(String(20), nullable=True)
    resolution_value_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
