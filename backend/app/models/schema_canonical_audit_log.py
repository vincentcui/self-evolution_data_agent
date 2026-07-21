"""Schema canonical audit log — candidate→canonical 流转全程留痕.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/02-data-model.md §2.3
与 KnowledgeAuditLog 隔离 (不同语义), 共 16 个 action.
"""
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import LOCAL_NOW, Base

SchemaAuditAction = Literal[
    "auto_extract", "auto_promote", "auto_supersede",
    "conflict_open_diff", "conflict_open_semantic",
    "conflict_reclassified",
    "conflict_resolve_keep_a", "conflict_resolve_keep_b",
    "conflict_resolve_merge", "conflict_resolve_reject",
    "user_confirm", "user_correct", "user_ignore",
    "user_lock", "user_unlock",
    "skipped_user_locked", "invalid_transition_attempt",
    # Phase 2 enum binding actions
    "field_enum_manual_bind", "field_enum_manual_unbind",
    "field_sample_collected",
    # Phase 2 enum sync worker actions
    "enum_dict_auto_rebind", "enum_dict_value_sync",
    "enum_dict_unbind_cascade", "enum_sync_failed",
    "enum_dict_deleted",
    "canonical_deleted",
]


class SchemaCanonicalAuditLog(Base):
    __tablename__ = "schema_canonical_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), index=True
    )

    candidate_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("schema_canonical_candidates.id", ondelete="SET NULL"), nullable=True
    )
    conflict_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("schema_canonical_conflicts.id", ondelete="SET NULL"), nullable=True
    )
    canonical_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("schema_canonical_objects.id", ondelete="SET NULL"), nullable=True
    )

    action: Mapped[str] = mapped_column(String(40))
    field_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)
