"""Stage 3 知识层审核 API — proposed → canonical / rejected 状态机驱动."""

import hashlib
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._audit_helpers import (
    apply_amem_evolution,
    apply_entry_filters,
    apply_inline_edits,
    automaton_invalidate_safe,
    chroma_delete_safe,
    chroma_upsert_safe,
    paginate_count,
    resolve_ns_slug,
    sync_amem_chroma_deletes,
)
from app.auth import accessible_namespace_ids, assert_ns_access, require_admin_or_above
from app.config import settings
from app.db.metadata import get_db
from app.knowledge.audit import (
    detect_conflict_against_canonical,
    list_audit_logs,
    write_audit,
)
from app.models import KnowledgeEntry
from app.models.user import User
from app.schemas import (
    AuditApproveBody,
    AuditBatchAction,
    AuditBatchBody,
    AuditBatchOut,
    AuditLogOut,
    AuditQueueOut,
    AuditRejectBody,
    AuditRestoreBody,
    ConflictPreviewBody,
    ConflictPreviewOut,
    KnowledgeEntryOut,
)

router = APIRouter(tags=["audit"])
log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  GET /api/knowledge/audit/queue
# ════════════════════════════════════════════

@router.get("/api/knowledge/audit/queue", response_model=AuditQueueOut)
async def list_audit_queue(
    namespace_id: int | None = Query(None),
    entry_type: str | None = Query(None),
    status: str | None = Query(None),
    source: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    size: int = Query(
        default=settings.audit_page_size_default,
        ge=1,
        le=settings.audit_page_size_max,
    ),
    admin: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
) -> AuditQueueOut:
    """待审/已审知识条目队列 — 按 created_at desc.

    过滤维度: namespace_id / entry_type / status / source / q.
    status 不传则不过滤 (返回所有 status 行); 显式传值则按 status 精确过滤.
    q 关键词匹配 content + description + payload 三字段 OR (大小写无关).
    分页: page (1-based) + size (默认/上限受 IS_AUDIT_PAGE_SIZE_* 控制).
    """
    # ── ns 作用域收窄 (Phase 3.3): super_admin 全量; 余按 accessible 集合 ──
    allowed = await accessible_namespace_ids(db, admin)
    if namespace_id is not None:
        if allowed is not None and namespace_id not in allowed:
            raise HTTPException(403, f"No access to namespace {namespace_id}")

    stmt = apply_entry_filters(
        select(KnowledgeEntry),
        namespace_id=namespace_id, entry_type=entry_type,
        status=status, source=source, q=q,
    )
    if namespace_id is None and allowed is not None:
        stmt = stmt.where(KnowledgeEntry.namespace_id.in_(allowed))
    total = await paginate_count(db, stmt)
    rows = (await db.scalars(
        stmt.order_by(KnowledgeEntry.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
    )).all()

    return AuditQueueOut(
        items=[KnowledgeEntryOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        size=size,
    )


# ════════════════════════════════════════════
#  内部 helper — supersede 链 (audit-only, 非 ChromaDB)
# ════════════════════════════════════════════

async def _supersede_old_entries(
    db: AsyncSession,
    new_entry_id: int,
    supersede_ids: list[int],
    actor_id: int | None,
) -> list[KnowledgeEntry]:
    """将 supersede_ids 中的旧 canonical 转 superseded, 写 audit_log.

    返回真正被 supersede 的旧 entry 列表 (供调用方做 ChromaDB 同步).
    跳过: 不存在 / status != canonical 的 id (log.info 留痕方便排查).
    """
    superseded: list[KnowledgeEntry] = []
    for old_id in supersede_ids:
        old = await db.get(KnowledgeEntry, old_id)
        if old is None:
            log.info("[audit] supersede skip: entry id=%d 不存在", old_id)
            continue
        if old.status != "canonical":
            log.info(
                "[audit] supersede skip: entry id=%d status=%s 非 canonical",
                old_id, old.status,
            )
            continue
        old.status = "superseded"
        old.is_superseded = True
        old.superseded_by = new_entry_id
        await write_audit(
            db, entry_id=old.id, action="supersede",
            from_status="canonical", to_status="superseded",
            actor_id=actor_id,
        )
        superseded.append(old)
    return superseded


# ════════════════════════════════════════════
#  POST /api/knowledge/audit/{entry_id}/approve
# ════════════════════════════════════════════

@router.post(
    "/api/knowledge/audit/{entry_id}/approve",
    response_model=KnowledgeEntryOut,
)
async def approve_entry(
    entry_id: int,
    body: AuditApproveBody,
    admin: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeEntry:
    """审核通过 proposed → canonical, 可选 inline 编辑 + supersede 旧条目.

    状态机: 仅接受 proposed; 其他状态返 422 invalid_state_transition.
    ChromaDB 同步在 SQLite commit 后 best-effort 进行 (失败仅 log).
    """
    entry = await db.get(KnowledgeEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "知识条目不存在")
    await assert_ns_access(db, admin, entry.namespace_id)
    if entry.status != "proposed":
        raise HTTPException(
            422,
            f"invalid_state_transition: {entry.status} -> canonical",
        )

    apply_inline_edits(entry, body.edits)
    entry.status = "canonical"
    entry.reviewed_by_id = admin.id
    entry.reviewed_at = datetime.now()
    await write_audit(
        db, entry_id=entry.id, action="approve",
        from_status="proposed", to_status="canonical",
        actor_id=admin.id, reason=body.reason,
        diff=body.edits or {},
    )

    superseded = await _supersede_old_entries(
        db, entry.id, body.supersede_ids, admin.id,
    )

    # ── Stage 2 抓手 D: A-MEM 演化触发 ──
    await apply_amem_evolution(db, entry, admin.id)

    await db.commit()
    await db.refresh(entry)

    # ── ChromaDB 同步 (commit 后, derived data 失败不阻) ──
    slug = await resolve_ns_slug(db, entry.namespace_id)
    # Phase 3 C2: rule/route_hint 主+hq_* 由 hq_writer 全权管, 跳过 chroma_upsert_safe
    if entry.entry_type not in {"rule", "route_hint", "example"}:
        await chroma_upsert_safe(slug, entry)

    # ── Phase 3: 统一 hq_writer (approve 路径) ──
    if entry.entry_type in {"rule", "route_hint", "example"}:
        from app.knowledge.hq_writer import rewrite_hq_for_entry
        try:
            await rewrite_hq_for_entry(db, slug, entry)
            await db.commit()
        except Exception as e:  # noqa: BLE001
            log.warning("[approve] hq_writer fail entry=%d: %s", entry.id, e)

    # ── A-MEM 演化: ChromaDB 删除 (commit 后 best-effort) ──
    await sync_amem_chroma_deletes(db, entry)

    for old in superseded:
        old_slug = await resolve_ns_slug(db, old.namespace_id)
        await chroma_delete_safe(old_slug, old)

    # ── AC 自动机失效 + 重建 ──
    await automaton_invalidate_safe(db, entry.namespace_id, entry.entry_type)

    return entry


# ════════════════════════════════════════════
#  POST /api/knowledge/audit/{entry_id}/reject
# ════════════════════════════════════════════

@router.post(
    "/api/knowledge/audit/{entry_id}/reject",
    response_model=KnowledgeEntryOut,
)
async def reject_entry(
    entry_id: int,
    body: AuditRejectBody,
    admin: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeEntry:
    """审核拒绝 proposed/canonical → rejected, reason 必填.

    状态机: 仅接受 proposed/canonical; 其他状态返 422.
    若 from_status=canonical, 同步从 ChromaDB 删向量 (best-effort).
    """
    entry = await db.get(KnowledgeEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "知识条目不存在")
    await assert_ns_access(db, admin, entry.namespace_id)
    if entry.status not in ("proposed", "canonical"):
        raise HTTPException(
            422,
            f"invalid_state_transition: {entry.status} -> rejected",
        )

    old_status = entry.status
    entry.status = "rejected"
    entry.reviewed_by_id = admin.id
    entry.reviewed_at = datetime.now()
    await write_audit(
        db, entry_id=entry.id, action="reject",
        from_status=old_status, to_status="rejected",
        actor_id=admin.id, reason=body.reason,
    )
    await db.commit()
    await db.refresh(entry)

    # ── ChromaDB 同步 — canonical → rejected 才需要删向量 ──
    if old_status == "canonical":
        slug = await resolve_ns_slug(db, entry.namespace_id)
        await chroma_delete_safe(slug, entry)
        # ── AC 自动机失效 + 重建 ──
        await automaton_invalidate_safe(db, entry.namespace_id, entry.entry_type)

    return entry


# ════════════════════════════════════════════
#  POST /api/knowledge/audit/batch
#  批量审核 — atomic + confirm_token 防误操作
# ════════════════════════════════════════════

def _compute_batch_confirm_token(sorted_ids: list[int], count: int) -> str:
    """sha256(f'batch:{sorted_ids}:{count}')[:16] — 防状态漂移.

    sorted_ids 必须已排序去重, 否则同一批操作会算出不同 token.
    """
    payload = f"batch:{','.join(map(str, sorted_ids))}:{count}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def _apply_action_atomic(
    db: AsyncSession,
    action: AuditBatchAction,
    admin: User,
) -> tuple[KnowledgeEntry | None, str | None, list[KnowledgeEntry]]:
    """单 action 应用 (不 commit). 返 (主 entry, sync_op, superseded_list).

    sync_op ∈ {'upsert', 'delete', None} — 给调用方 commit 后做 ChromaDB.
    raises ValueError 如果状态机非法 / entry 不存在 — 调用方 rollback.
    """
    entry = await db.get(KnowledgeEntry, action.entry_id)
    if entry is None:
        raise ValueError(f"entry_not_found: id={action.entry_id}")
    await assert_ns_access(db, admin, entry.namespace_id)
    now = datetime.now()

    if action.action == "approve":
        if entry.status != "proposed":
            raise ValueError(f"invalid_state: {entry.status} -> canonical")
        apply_inline_edits(entry, action.edits)
        entry.status = "canonical"
        entry.reviewed_by_id = admin.id
        entry.reviewed_at = now
        await write_audit(
            db, entry_id=entry.id, action="approve",
            from_status="proposed", to_status="canonical",
            actor_id=admin.id, reason=action.reason,
            diff=action.edits or {},
        )
        superseded = await _supersede_old_entries(
            db, entry.id, action.supersede_ids, admin.id,
        )
        return entry, "upsert", superseded

    # action == "reject" (Pydantic pattern 已限定)
    if entry.status not in ("proposed", "canonical"):
        raise ValueError(f"invalid_state: {entry.status} -> rejected")
    old_status = entry.status
    entry.status = "rejected"
    entry.reviewed_by_id = admin.id
    entry.reviewed_at = now
    await write_audit(
        db, entry_id=entry.id, action="reject",
        from_status=old_status, to_status="rejected",
        actor_id=admin.id, reason=action.reason,
    )
    sync_op = "delete" if old_status == "canonical" else None
    return entry, sync_op, []


def _validate_batch_confirm_token(body: AuditBatchBody, count: int) -> None:
    """超阈值时强制 confirm_token, 不通过抛 422 (不通过的两类: 缺 / 错)."""
    if count <= settings.bulk_op_require_confirm_above:
        return
    sorted_ids = sorted({a.entry_id for a in body.actions})
    expected = _compute_batch_confirm_token(sorted_ids, count)
    if not body.confirm_token:
        raise HTTPException(422, detail={
            "error": "confirm_token_required",
            "expected_token": expected,
            "affected_count": count,
        })
    if body.confirm_token != expected:
        raise HTTPException(422, detail={
            "error": "confirm_token_mismatch",
            "expected_token": expected,
        })


async def _sync_batch_chromadb(
    db: AsyncSession,
    sync_ops: list[tuple[KnowledgeEntry, str]],
) -> None:
    """commit 后批量 ChromaDB 同步 (best-effort, 失败仅 log)."""
    for entry, op in sync_ops:
        slug = await resolve_ns_slug(db, entry.namespace_id)
        if op == "upsert":
            await chroma_upsert_safe(slug, entry)
        elif op == "delete":
            await chroma_delete_safe(slug, entry)


@router.post("/api/knowledge/audit/batch", response_model=AuditBatchOut)
async def audit_batch(
    body: AuditBatchBody,
    admin: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
) -> AuditBatchOut:
    """批量审核 — 单事务 all-or-nothing, 超阈值强制 confirm_token.

    阈值由 IS_BULK_OP_REQUIRE_CONFIRM_ABOVE 控制 (默认 100).
    actions 任一非法状态 → 全部 rollback + 422.
    成功后 ChromaDB best-effort 同步 (失败仅 log).
    """
    count = len(body.actions)
    _validate_batch_confirm_token(body, count)

    sync_ops: list[tuple[KnowledgeEntry, str]] = []
    success_ids: list[int] = []
    try:
        for action in body.actions:
            entry, sync_op, superseded = await _apply_action_atomic(db, action, admin)
            if entry is None:
                continue
            success_ids.append(entry.id)
            if sync_op:
                sync_ops.append((entry, sync_op))
            sync_ops.extend((old, "delete") for old in superseded)
        await db.commit()
    except ValueError as e:
        await db.rollback()
        raise HTTPException(422, detail=str(e)) from e

    await _sync_batch_chromadb(db, sync_ops)

    # ── AC 自动机失效 + 重建 (批量中任一 terminology 变更即触发) ──
    has_terminology = any(
        entry.entry_type == "terminology" for entry, _ in sync_ops
    )
    if has_terminology:
        # 批量操作可能跨 namespace, 简化: 全量重建
        await automaton_invalidate_safe(db, None, "terminology")

    return AuditBatchOut(affected_count=count, success_ids=success_ids)


# ════════════════════════════════════════════
#  POST /api/knowledge/{entry_id}/restore
#  注: 路径前缀为 /knowledge/{id}/restore (反向状态机, 与 reject 对称),
#  不在 /audit/* 命名空间下 — 见 docs/.../03-audit-and-ux-design.md.
# ════════════════════════════════════════════

@router.post(
    "/api/knowledge/{entry_id}/restore",
    response_model=KnowledgeEntryOut,
)
async def restore_entry(
    entry_id: int,
    body: AuditRestoreBody,
    admin: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeEntry:
    """rejected → canonical 反向恢复, reason 必填.

    状态机: 仅接受 rejected; 其他状态返 422 invalid_state_transition.
    ChromaDB 同步在 SQLite commit 后 best-effort upsert (失败仅 log).
    """
    entry = await db.get(KnowledgeEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "知识条目不存在")
    await assert_ns_access(db, admin, entry.namespace_id)
    if entry.status != "rejected":
        raise HTTPException(
            422,
            f"invalid_state_transition: {entry.status} -> canonical",
        )

    entry.status = "canonical"
    entry.reviewed_by_id = admin.id
    entry.reviewed_at = datetime.now()
    await write_audit(
        db, entry_id=entry.id, action="restore",
        from_status="rejected", to_status="canonical",
        actor_id=admin.id, reason=body.reason,
    )
    await db.commit()
    await db.refresh(entry)

    # ── ChromaDB 同步 — restore 必 upsert (恢复 RAG 可见性) ──
    slug = await resolve_ns_slug(db, entry.namespace_id)
    await chroma_upsert_safe(slug, entry)

    # ── AC 自动机失效 + 重建 ──
    await automaton_invalidate_safe(db, entry.namespace_id, entry.entry_type)

    return entry


# ════════════════════════════════════════════
#  GET /api/knowledge/audit/{entry_id}/log
# ════════════════════════════════════════════

@router.get(
    "/api/knowledge/audit/{entry_id}/log",
    response_model=list[AuditLogOut],
)
async def list_entry_audit_log(
    entry_id: int,
    admin: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
) -> list[AuditLogOut]:
    """某 entry 的 audit_log 时间线 — 按 created_at asc 列出.

    entry 已 hard-delete 也允许查 (审计独立保留, 返 [] 而非 404),
    供 review UI 复盘已彻底删除的条目历史.
    """
    # ── ns 作用域 (Phase 3.3): entry 仍存在则按其 ns 校验; 已删则审计独立放行 ──
    entry = await db.get(KnowledgeEntry, entry_id)
    if entry is not None:
        await assert_ns_access(db, admin, entry.namespace_id)
    rows = await list_audit_logs(db, entry_id)
    return [AuditLogOut.model_validate(r) for r in rows]


# ════════════════════════════════════════════
#  POST /api/knowledge/audit/conflict-preview
#  Stage 3 Task 7 — 编辑表单实时冲突预览 (debounce 500ms)
# ════════════════════════════════════════════

@router.post(
    "/api/knowledge/audit/conflict-preview",
    response_model=ConflictPreviewOut,
)
async def preview_conflicts(
    body: ConflictPreviewBody,
    admin: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
) -> ConflictPreviewOut:
    """实时冲突预览 — 编辑表单内置, 不写库, 不动 ChromaDB.

    与 PUT /api/knowledge/{id} 编辑后冲突检测共用同一 LLM 路径
    (`detect_conflict_against_canonical`), 保证前后端 conflicts schema 完全对齐.
    entry_id 可选: 编辑场景传入排除自身, 新建场景留空全量比对.
    """
    if body.namespace_id is None:
        raise HTTPException(422, "namespace_id 必填")
    await assert_ns_access(db, admin, body.namespace_id)
    conflicts = await detect_conflict_against_canonical(
        db,
        namespace_id=body.namespace_id,
        entry_type=body.entry_type,
        content=body.content,
        exclude_entry_id=body.entry_id,
    )
    return ConflictPreviewOut(conflicts=conflicts)
