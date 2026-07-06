"""Schema Canonical REST API — 通用 schema 真相源管理.

端点:
- GET  /api/namespaces/{ns_id}/schema-canonical          列出全部 (可选 ?db_type=mysql)
- GET  /api/namespaces/{ns_id}/schema-canonical/{id}     单条详情
- PATCH /api/namespaces/{ns_id}/schema-canonical/{id}    编辑 (fields description / description)
- DELETE /api/namespaces/{ns_id}/schema-canonical/{id}   删除 (含关联清理)
- POST /api/namespaces/{ns_id}/schema-canonical/refresh  触发 MySQL introspect 刷新
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_or_above, require_ns_manage
from app.db.metadata import get_db
from app.knowledge.canonical_audit import write_canonical_audit_log
from app.knowledge.schema_canonical import (
    list_schema_canonicals,
    refresh_mysql_canonicals,
)
from app.models import SchemaCanonicalObject
from app.models.base import local_now
from app.models.enum_binding_conflict import EnumBindingConflict
from app.models.schema_canonical_candidate import SchemaCanonicalCandidate
from app.models.schema_canonical_conflict import SchemaCanonicalConflict
from app.models.user import User
from app.schemas.relationship import RelationshipEntry

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/namespaces/{ns_id}/schema-canonical", tags=["schema-canonical"])


# ── delete helpers ──


def _build_canonical_snapshot(obj: SchemaCanonicalObject) -> dict:
    """快照 SCO 全字段, 确保 FK ondelete=SET NULL 后审计日志仍有完整记录."""
    return {
        "id": obj.id,
        "namespace_id": obj.namespace_id,
        "db_type": obj.db_type,
        "database": obj.database,
        "target": obj.target,
        "fields_json": obj.fields_json,
        "indexes_json": obj.indexes_json,
        "description": obj.description,
        "purpose_detail": obj.purpose_detail,
        "reviewed": obj.reviewed,
        "sample_count": obj.sample_count,
        "source": obj.source,
        "relationships_json": obj.relationships_json,
        "sample_values_json": obj.sample_values_json,
        "user_locked": obj.user_locked,
        "created_at": obj.created_at.isoformat() if obj.created_at else None,
        "updated_at": obj.updated_at.isoformat() if obj.updated_at else None,
    }


async def _orphan_candidates(
    db: AsyncSession, ns_id: int, obj: SchemaCanonicalObject
) -> None:
    """将关联 candidate 标为 orphaned (表已删, 不再可 promote)."""
    from sqlalchemy import or_
    await db.execute(
        sa_update(SchemaCanonicalCandidate)
        .where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.db_type == obj.db_type,
            SchemaCanonicalCandidate.database == obj.database,
            SchemaCanonicalCandidate.target == obj.target,
            or_(
                SchemaCanonicalCandidate.status == "active",
                SchemaCanonicalCandidate.status == "pending",
                SchemaCanonicalCandidate.status == "in_conflict",
                SchemaCanonicalCandidate.status == "superseding",
            ),
        )
        .values(status="orphaned")
    )


async def _cleanup_enum_binding_conflicts(
    db: AsyncSession, ns_id: int, sco_id: int
) -> None:
    """清理悬空 enum_binding_conflicts (field_canonical_id 无 FK)."""
    await db.execute(
        sa_delete(EnumBindingConflict).where(
            EnumBindingConflict.namespace_id == ns_id,
            EnumBindingConflict.field_canonical_id == sco_id,
        )
    )


async def _close_open_conflicts(
    db: AsyncSession, ns_id: int, obj: SchemaCanonicalObject, user: User
) -> None:
    """关闭关联的 open conflict → resolved (按元组匹配, 无 FK)."""
    await db.execute(
        sa_update(SchemaCanonicalConflict)
        .where(
            SchemaCanonicalConflict.namespace_id == ns_id,
            SchemaCanonicalConflict.db_type == obj.db_type,
            SchemaCanonicalConflict.database == obj.database,
            SchemaCanonicalConflict.target == obj.target,
            SchemaCanonicalConflict.status == "open",
        )
        .values(
            status="resolved",
            resolved_by=user.id,
            resolved_at=local_now(),
            resolution_reason="table deleted",
        )
    )


# ── Response schemas ──

class SchemaCanonicalOut(BaseModel):
    id: int
    namespace_id: int
    db_type: str
    database: str
    target: str
    fields: list[dict]
    indexes: list[dict]
    description: str
    purpose_detail: str
    sample_count: int
    source: str
    relationships: list[dict] = []
    user_locked: bool = False

    @classmethod
    def from_orm(cls, obj: SchemaCanonicalObject) -> "SchemaCanonicalOut":
        try:
            fields = json.loads(obj.fields_json or "[]")
        except json.JSONDecodeError:
            fields = []
        try:
            indexes = json.loads(obj.indexes_json or "[]")
        except json.JSONDecodeError:
            indexes = []
        try:
            relationships = json.loads(obj.relationships_json or "[]")
            relationships = [{k: v for k, v in r.items() if k != "sources"} for r in relationships]
        except json.JSONDecodeError:
            relationships = []
        return cls(
            id=obj.id,
            namespace_id=obj.namespace_id,
            db_type=obj.db_type,
            database=obj.database,
            target=obj.target,
            fields=fields,
            indexes=indexes,
            description=obj.description or "",
            purpose_detail=obj.purpose_detail or "",
            sample_count=obj.sample_count,
            source=obj.source or "introspect",
            relationships=relationships,
            user_locked=obj.user_locked,
        )


class SchemaCanonicalPatch(BaseModel):
    description: str | None = None
    purpose_detail: str | None = None
    fields: list[dict] | None = None  # 用户编辑字段 description
    relationships: list[RelationshipEntry] | None = None  # pydantic-validated

    @field_validator("relationships", mode="before")
    @classmethod
    def _ensure_dicts(cls, v):
        """Allow plain dict input — coercing through RelationshipEntry."""
        if v is None:
            return None
        return [RelationshipEntry(**r) if isinstance(r, dict) else r for r in v]


# ── Endpoints ──

@router.get("")
async def list_canonicals(
    ns_id: int,
    db_type: str | None = Query(None),
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> list[SchemaCanonicalOut]:
    """列出 namespace 下全部 schema canonical."""
    rows = await list_schema_canonicals(db, ns_id, db_type=db_type)
    return [SchemaCanonicalOut.from_orm(r) for r in rows]


@router.get("/{sco_id}")
async def get_canonical(
    ns_id: int,
    sco_id: int,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> SchemaCanonicalOut:
    """获取单条 schema canonical 详情."""
    obj = await db.get(SchemaCanonicalObject, sco_id)
    if not obj or obj.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")
    return SchemaCanonicalOut.from_orm(obj)


@router.patch("/{sco_id}")
async def patch_canonical(
    ns_id: int,
    sco_id: int,
    body: SchemaCanonicalPatch,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> SchemaCanonicalOut:
    """编辑 schema canonical (description / fields description).

    修订 #3: 任何 PATCH 都自动标 user_locked=True, 防止下次 promote 覆盖人工编辑.
    """
    obj = await db.get(SchemaCanonicalObject, sco_id)
    if not obj or obj.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")

    if body.description is not None:
        obj.description = body.description
    if body.purpose_detail is not None:
        obj.purpose_detail = body.purpose_detail
    if body.fields is not None:
        obj.fields_json = json.dumps(body.fields, ensure_ascii=False)
    if body.relationships is not None:
        obj.relationships_json = json.dumps(
            [{**r.model_dump(), "sources": ["manual"]} for r in body.relationships],
            ensure_ascii=False,
        )

    # 修订 #3: auto-lock on patch
    obj.user_locked = True
    obj.updated_at = local_now()

    await write_canonical_audit_log(
        db, namespace_id=ns_id, action="user_lock",
        canonical_id=sco_id, actor_id=user.id,
        reason="auto_lock_on_patch",
    )
    await db.commit()
    await db.refresh(obj)
    return SchemaCanonicalOut.from_orm(obj)


@router.delete("/{sco_id}")
async def delete_canonical(
    ns_id: int,
    sco_id: int,
    user: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除单条 schema canonical 对象.

    同事务依次处理: 审计快照 → orphan candidate → 清理 enum binding →
    关闭 conflict → 物理删除 SCO.
    """
    obj = await db.get(SchemaCanonicalObject, sco_id)
    if not obj or obj.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")

    target_info = f"[{obj.db_type}] {obj.database}/{obj.target}"

    # 1. 审计快照 (必须在 FK ondelete=SET NULL 之前写入)
    before = _build_canonical_snapshot(obj)
    await write_canonical_audit_log(
        db, namespace_id=ns_id, action="canonical_deleted",
        canonical_id=sco_id, actor_id=user.id,
        before=before, reason=f"manual delete: {target_info}",
    )

    # 2-4. 清理关联数据
    await _orphan_candidates(db, ns_id, obj)
    await _cleanup_enum_binding_conflicts(db, ns_id, sco_id)
    await _close_open_conflicts(db, ns_id, obj, user)

    # 5. 物理删除
    await db.delete(obj)
    await db.commit()
    return {"ok": True, "deleted_id": sco_id}


@router.post("/refresh")
async def refresh_canonicals(
    ns_id: int,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """触发 MySQL introspect 刷新 (重新从 INFORMATION_SCHEMA 拉取)."""
    from app.models import Namespace
    ns = await db.get(Namespace, ns_id)
    if not ns:
        raise HTTPException(404, "namespace not found")

    count = await refresh_mysql_canonicals(db, ns_id, ns.slug)
    await db.commit()
    return {"refreshed": count, "namespace_id": ns_id}
