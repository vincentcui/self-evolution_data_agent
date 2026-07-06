"""
知识库管理 API
支持手动添加 (全局/指定命名空间) + Git 仓库管理 + 解析报告 + 澄清
"""

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._audit_helpers import (
    automaton_invalidate_safe,
    chroma_delete_safe,
    resolve_ns_slug,
)
from app.auth import assert_ns_access, require_admin_or_above, require_ns_manage
from app.config import settings
from app.db.metadata import get_db
from app.knowledge.audit import detect_conflict_against_canonical, write_audit
from app.knowledge.intake import (
    CONFLICT_CANDIDATE_LIMIT,
    IntakeLLMError,
    detect_conflicts,
    propose_split,
    refine_knowledge,
)
from app.knowledge.knowledge_retriever import (
    delete_knowledge_entry,
    parse_entry_payload,
    upsert_knowledge_entry,
)
from app.models import (
    GitRepo,
    KnowledgeEntry,
    Namespace,
    RepoDataSourceMapping,
)
from app.models.user import User
from app.schemas import (
    BatchStatus,
    ConflictItemOut,
    EditCanonicalBody,
    EditCanonicalOut,
    GitRepoCreate,
    GitRepoOut,
    KnowledgeEntryCreate,
    KnowledgeEntryCreateResponse,
    KnowledgeEntryDraft,
    KnowledgeEntryOut,
    KnowledgeEntryUpdate,
    ParseReportOut,
    RepoListResponse,
    RepoMappingCreate,
    RepoMappingOut,
)
from app.schemas.knowledge_payload import parse_payload

router = APIRouter(tags=["knowledge"])
log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  手动知识条目 — 支持全局 + 命名空间范围
# ════════════════════════════════════════════

@router.get("/api/namespaces/{ns_id}/knowledge", response_model=list[KnowledgeEntryOut])
async def list_knowledge(
    ns_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """列出命名空间专属 + 全局知识"""
    result = await db.execute(
        select(KnowledgeEntry).where(
            (KnowledgeEntry.namespace_id == ns_id)
            | (KnowledgeEntry.namespace_id.is_(None))
        ).order_by(KnowledgeEntry.created_at.desc())
    )
    return result.scalars().all()


@router.post(
    "/api/knowledge",
    response_model=KnowledgeEntryCreateResponse,
    status_code=201,
    responses={409: {"model": KnowledgeEntryCreateResponse}},
)
async def create_knowledge(
    body: KnowledgeEntryCreate,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """
    录入知识条目: refine → overflow check → conflict detection → persist.

    - namespace_id=None: 全局知识
    - tier=critical + refine 后超长: 409 + split_candidates (自动拆分建议)
    - 冲突检测非阻断, 候选冲突通过 response.conflicts 告警, 由前端决策

    Phase 1c Task 1.5 — terminology 通道走统一闸门 upsert_terminology_with_validation,
    跳过 refine/conflict 既有路径; 闸门做 schema/db_type/唯一键三重校验, 失败 422.
    """
    if body.namespace_id is None:
        raise HTTPException(422, "namespace_id 必填")
    await assert_ns_access(db, actor, body.namespace_id)
    ns = await db.get(Namespace, body.namespace_id)
    if not ns:
        raise HTTPException(404, "命名空间不存在")

    # ── Phase 1c: terminology 通道走统一闸门 ──
    if body.entry_type == "terminology":
        if body.namespace_id is None or body.payload is None:
            raise HTTPException(
                422, "terminology 需要 namespace_id + payload (走闸门 schema 校验)",
            )
        from app.knowledge.terminology_intake import upsert_terminology_with_validation
        ke = await upsert_terminology_with_validation(
            db, ns_id=body.namespace_id, payload_dict=body.payload,
            source="manual",
            raw_input=body.raw_input or body.content,
            evidence=body.evidence or {},
        )
        if ke is None:
            raise HTTPException(
                422, "terminology validation_failed_or_conflict_pending",
            )
        await db.commit()
        await db.refresh(ke)
        return KnowledgeEntryCreateResponse(
            entry=KnowledgeEntryOut.model_validate(ke),
        )

    refined = await asyncio.to_thread(
        refine_knowledge, body.entry_type, body.content, body.tier,
    )

    # ── tier=critical 且超长 → 不落库, 返回拆分候选 (409) ──
    if refined.overflow:
        try:
            raw_candidates = await asyncio.to_thread(propose_split, body.content)
        except IntakeLLMError as e:
            raise HTTPException(503, str(e))
        # 过滤仍然 overflow 的候选 (LLM 不当拆分)
        usable: list[KnowledgeEntryDraft] = []
        for c in raw_candidates:
            if c.overflow:
                log.warning("[knowledge] 丢弃超长 split 候选: %r", c.refined[:40])
                continue
            usable.append(KnowledgeEntryDraft(refined=c.refined, description=c.description))
        return JSONResponse(
            status_code=409,
            content=KnowledgeEntryCreateResponse(
                overflow=True, split_candidates=usable,
            ).model_dump(mode="json"),
        )

    # ── 冲突检测 — 同 namespace + 全局 + 同 entry_type ──
    existing_rows = (await db.execute(
        select(KnowledgeEntry).where(
            (
                (KnowledgeEntry.namespace_id == body.namespace_id)
                | (KnowledgeEntry.namespace_id.is_(None))
            ),
            KnowledgeEntry.entry_type == body.entry_type,
            KnowledgeEntry.is_superseded.is_(False),
        ).limit(CONFLICT_CANDIDATE_LIMIT)
    )).scalars().all()
    existing = [{"id": e.id, "content": e.content} for e in existing_rows]
    try:
        report = await asyncio.to_thread(detect_conflicts, refined.refined, existing)
    except IntakeLLMError as e:
        raise HTTPException(503, str(e))

    entry = KnowledgeEntry(
        namespace_id=body.namespace_id,
        entry_type=body.entry_type,
        tier=body.tier,
        content=refined.refined,
        raw_input=body.content,
        description=refined.description,
        source="manual",
        # Stage 1: 手工录入默认 proposed (待人审进 RAG); 走 PATCH status=canonical 通过审核
        status="proposed",
        payload=json.dumps(body.payload, ensure_ascii=False) if body.payload else None,
        refined_at=datetime.now(),
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    # ── 向量化录入 — manual proposed 不入池 (status 过滤), 留待审核 PATCH 时再上 ──
    ns_slug = ns.slug if ns is not None else None
    try:
        await asyncio.to_thread(
            upsert_knowledge_entry,
            slug=ns_slug or "",
            entry_id=entry.id,
            content=entry.content,
            tier=entry.tier,
            namespace_id=entry.namespace_id,
            entry_type=entry.entry_type,
            status=entry.status,
            payload=parse_entry_payload(entry.payload),
        )
    except Exception as e:
        log.warning("[knowledge] 向量化失败 entry_id=%d: %s", entry.id, e)

    return KnowledgeEntryCreateResponse(
        entry=KnowledgeEntryOut.model_validate(entry),
        conflicts=[
            ConflictItemOut(
                existing_id=i.existing_id, reason=i.reason, suggested=i.suggested,
            )
            for i in report.items
        ],
    )


_TERMINAL_STATES = ("superseded", "rejected")


@router.get("/api/knowledge/{entry_id}", response_model=KnowledgeEntryOut)
async def get_knowledge_entry(
    entry_id: int,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """获取单条知识条目详情 — Stage 2 Task D RelatedEntryDetailModal 使用."""
    entry = await db.get(KnowledgeEntry, entry_id)
    if not entry:
        raise HTTPException(404, "知识条目不存在")
    await assert_ns_access(db, actor, entry.namespace_id)
    return entry


@router.delete("/api/knowledge/{entry_id}", status_code=204)
async def delete_knowledge(
    entry_id: int,
    mode: str = Query("soft", pattern=r"^(soft|hard)$"),
    reason: str = Query(..., min_length=1),
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Stage 3 Task 6 — 升级版删除: soft (默认, 软删) / hard (物理删, 受 settings 开关).

    soft 行为:
        - proposed → 物理删 (无价值保留) + audit reject (entry_id 在 commit 前留痕)
        - canonical → status=rejected + ChromaDB delete + audit reject
        - 终态 (superseded / rejected) → 422 already_terminal_state
    hard 行为:
        - settings.knowledge_hard_delete_enabled=False → 403 hard_delete_disabled
        - 启用时: audit hard_delete (commit 前) + ChromaDB delete + 物理删
    """
    entry = await db.get(KnowledgeEntry, entry_id)
    if not entry:
        raise HTTPException(404, "知识条目不存在")
    await assert_ns_access(db, actor, entry.namespace_id)

    if mode == "hard":
        return await _delete_hard(db, entry, actor.id, reason)
    return await _delete_soft(db, entry, actor.id, reason)


async def _delete_soft(
    db: AsyncSession, entry: KnowledgeEntry, actor_id: int, reason: str,
) -> Response:
    if entry.status in _TERMINAL_STATES:
        raise HTTPException(422, f"already_terminal_state: {entry.status}")

    from_status = entry.status
    slug = await resolve_ns_slug(db, entry.namespace_id)

    await write_audit(
        db, entry_id=entry.id, action="reject",
        from_status=from_status, to_status="rejected",
        actor_id=actor_id, reason=reason,
    )

    if from_status == "proposed":
        # proposed 无 ChromaDB 向量 (status 过滤), 跳 ChromaDB 直接物理删
        await db.delete(entry)
    else:
        # canonical → 软删: status=rejected + ChromaDB 清向量
        entry.status = "rejected"
        await chroma_delete_safe(slug, entry)

    await db.commit()

    # ── AC 自动机失效 + 重建 (仅 canonical → rejected 需要) ──
    if from_status == "canonical":
        await automaton_invalidate_safe(db, entry.namespace_id, entry.entry_type)

    return Response(status_code=204)


async def _delete_hard(
    db: AsyncSession, entry: KnowledgeEntry, actor_id: int, reason: str,
) -> Response:
    if not settings.knowledge_hard_delete_enabled:
        raise HTTPException(
            403, "hard_delete_disabled: 启用 IS_KNOWLEDGE_HARD_DELETE_ENABLED=true",
        )

    old_status = entry.status
    ns_id = entry.namespace_id
    entry_type = entry.entry_type
    slug = await resolve_ns_slug(db, ns_id)
    await write_audit(
        db, entry_id=entry.id, action="hard_delete",
        from_status=old_status, to_status="rejected",
        actor_id=actor_id, reason=reason,
    )
    await chroma_delete_safe(slug, entry)
    await db.delete(entry)
    await db.commit()

    # ── AC 自动机失效 + 重建 (仅 canonical 被删时需要) ──
    if old_status == "canonical":
        await automaton_invalidate_safe(db, ns_id, entry_type)

    return Response(status_code=204)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ PATCH ChromaDB 同步 helper — Stage 2 Task 5 抽离, 行为对齐原嵌入版 ║
# ╚══════════════════════════════════════════════════════════════════╝
async def _sync_chromadb_after_patch(
    *,
    slug: str,
    entry: KnowledgeEntry,
    old_tier: str,
    old_status: str,
    content_changed: bool,
    db: AsyncSession | None = None,
) -> None:
    """PATCH 后 ChromaDB 同步 + AC 自动机失效.

    Phase 3 修订: rule / route_hint 类主+hq_* 同步统一由 hq_writer 接管,
    此处跳过 (避免 chroma_upsert_safe 写主向量后被 hq_writer 覆盖的冗余写).

    转移矩阵:
        new_tier=critical                       → ChromaDB delete (不进 RAG)
        new_status≠canonical (old=canonical)    → ChromaDB delete
        new_status=canonical, new_tier=normal,
            且 (status/tier/content 任一变化)    → upsert
        其他                                    → no-op

    失败仅 log.warning, 不阻 PATCH 主流程 — derived data 失败不侵蚀真相源.
    """
    # Phase 3: rule / route_hint chroma sync 由 hq_writer 全权管
    if entry.entry_type in {"rule", "route_hint"}:
        # 仅 AC 自动机失效 (rule/route_hint 不触发 AC, 但保持接口一致)
        if db is not None and (content_changed or old_status != entry.status):
            await automaton_invalidate_safe(db, entry.namespace_id, entry.entry_type)
        return

    new_tier = entry.tier
    new_status = entry.status
    tier_changed = new_tier != old_tier
    status_changed = new_status != old_status

    needs_delete = (
        (tier_changed and new_tier == "critical")
        or (status_changed and old_status == "canonical" and new_status != "canonical")
    )
    needs_upsert = (
        new_status == "canonical"
        and new_tier == "normal"
        and (
            (status_changed and old_status != "canonical")
            or (tier_changed and old_tier == "critical")
            or content_changed
        )
    )

    if needs_delete:
        try:
            await asyncio.to_thread(
                delete_knowledge_entry,
                slug=slug,
                entry_id=entry.id,
                namespace_id=entry.namespace_id,
                entry_type=entry.entry_type,
            )
        except Exception as e:
            log.warning("[knowledge] PATCH 向量删除失败 id=%d: %s", entry.id, e)
    elif needs_upsert:
        try:
            await asyncio.to_thread(
                upsert_knowledge_entry,
                slug=slug,
                entry_id=entry.id,
                content=entry.content,
                tier=entry.tier,
                namespace_id=entry.namespace_id,
                entry_type=entry.entry_type,
                status=entry.status,
                payload=parse_entry_payload(entry.payload),
            )
        except Exception as e:
            log.warning("[knowledge] PATCH 向量 upsert 失败 id=%d: %s", entry.id, e)

    # ── AC 自动机失效 + 重建 ──
    if (needs_delete or needs_upsert or content_changed) and db is not None:
        await automaton_invalidate_safe(db, entry.namespace_id, entry.entry_type)


@router.patch("/api/knowledge/{entry_id}", response_model=KnowledgeEntryOut)
async def patch_knowledge(
    entry_id: int,
    body: KnowledgeEntryUpdate,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """
    通用编辑: content / tier / description / status 均可选.

    向量同步委托 `_sync_chromadb_after_patch` (Stage 2 Task 5 抽离), 转移矩阵详见 helper docstring.
    """
    entry = await db.get(KnowledgeEntry, entry_id)
    if not entry:
        raise HTTPException(404, "知识条目不存在")
    await assert_ns_access(db, actor, entry.namespace_id)

    ns_slug = ""
    if entry.namespace_id is not None:
        ns = await db.get(Namespace, entry.namespace_id)
        if ns:
            ns_slug = ns.slug

    old_tier = entry.tier
    old_status = entry.status
    content_changed = body.content is not None and body.content != entry.content

    if body.content is not None:
        entry.content = body.content
    if body.tier is not None:
        entry.tier = body.tier
    if body.description is not None:
        entry.description = body.description
    # ── status 直传 ──
    if body.status is not None:
        entry.status = body.status

    await db.commit()
    await db.refresh(entry)

    await _sync_chromadb_after_patch(
        slug=ns_slug,
        entry=entry,
        old_tier=old_tier,
        old_status=old_status,
        content_changed=content_changed,
        db=db,
    )
    return entry


# ╔══════════════════════════════════════════════════════════════════╗
# ║ PUT /api/knowledge/{id} — Stage 3 Task 5 升级版编辑                ║
# ║ 与 PATCH 并存兼容旧前端; PUT 走 audit + payload 校验 + 编辑后冲突   ║
# ╚══════════════════════════════════════════════════════════════════╝
@router.put("/api/knowledge/{entry_id}", response_model=EditCanonicalOut)
async def edit_knowledge(
    entry_id: int,
    body: EditCanonicalBody,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
) -> EditCanonicalOut:
    """编辑知识条目 (proposed/canonical/superseded), 写 audit_log + 编辑后冲突检测.

    状态机: rejected 禁止编辑 (422); 编辑不切 status, 仅改 content/payload/tier.
    payload 走 parse_payload(entry_type) 严格 schema 校验, 失败 422 含 ValidationError.
    audit_log: action=edit, from/to_status 同值, diff={before, after}, reason 必填.
    ChromaDB: 复用 `_sync_chromadb_after_patch` 转移矩阵 (status 不变, 仅 content 变化触发 upsert).
    编辑后冲突: canonical + content 变化时调 `detect_conflict_against_canonical` (Task 7 落 LLM).
    """
    entry = await db.get(KnowledgeEntry, entry_id)
    if not entry:
        raise HTTPException(404, "知识条目不存在")
    await assert_ns_access(db, actor, entry.namespace_id)
    if entry.status not in ("canonical", "proposed", "superseded"):
        raise HTTPException(
            422, f"invalid_state_transition: {entry.status} 禁止编辑",
        )

    if body.payload is not None:
        try:
            parse_payload(entry.entry_type, body.payload)
        except Exception as e:
            raise HTTPException(422, f"payload_validation_failed: {e}") from e

    before = {
        "content": entry.content,
        "payload": entry.payload,
        "tier": entry.tier,
        "hypothetical_queries_json": entry.hypothetical_queries_json,
    }
    content_changed = body.content is not None and body.content != entry.content
    old_tier = entry.tier
    old_status = entry.status

    if body.content is not None:
        entry.content = body.content
    if body.tier is not None:
        entry.tier = body.tier
    if body.payload is not None:
        entry.payload = json.dumps(body.payload, ensure_ascii=False)

    # ── 主字段 commit (content/payload/tier 变更先落库) ──
    await db.commit()
    await db.refresh(entry)

    ns_slug = ""
    if entry.namespace_id is not None:
        ns = await db.get(Namespace, entry.namespace_id)
        if ns:
            ns_slug = ns.slug
    await _sync_chromadb_after_patch(
        slug=ns_slug, entry=entry,
        old_tier=old_tier, old_status=old_status,
        content_changed=content_changed,
        db=db,
    )

    # ── Phase 3: HQ 重写双路径 ──
    from app.knowledge.hq_writer import rewrite_hq_for_entry
    manual_hqs = body.hypothetical_queries

    if manual_hqs is not None:
        try:
            await rewrite_hq_for_entry(db, ns_slug or "__global__", entry, manual_hqs=manual_hqs)
            await db.commit()
            await db.refresh(entry)
        except Exception as e:  # noqa: BLE001
            log.warning("[edit] hq_writer manual fail entry=%d: %s", entry.id, e)
    elif (
        content_changed
        and entry.entry_type in {"rule", "route_hint"}
        and entry.status == "canonical"
    ):
        try:
            await rewrite_hq_for_entry(db, ns_slug or "__global__", entry, manual_hqs=None)
            await db.commit()
            await db.refresh(entry)
        except Exception as e:  # noqa: BLE001
            log.warning("[edit] hq_writer auto fail entry=%d: %s", entry.id, e)

    # ── audit_log: 延迟到 HQ 改写后, 保证 diff 含 HQ 变化 (spec C5) ──
    after = {
        "content": entry.content,
        "payload": entry.payload,
        "tier": entry.tier,
        "hypothetical_queries_json": entry.hypothetical_queries_json,
    }
    await write_audit(
        db, entry_id=entry.id, action="edit",
        from_status=old_status, to_status=entry.status,
        actor_id=actor.id, reason=body.reason,
        diff={"before": before, "after": after},
    )
    await db.commit()

    conflicts: list[ConflictItemOut] = []
    if entry.status == "canonical" and content_changed:
        conflicts = await detect_conflict_against_canonical(
            db, entry.namespace_id, entry.entry_type, entry.content,
            exclude_entry_id=entry.id,
        )
    return EditCanonicalOut(
        entry=KnowledgeEntryOut.model_validate(entry),
        conflicts=conflicts,
    )


@router.post("/api/knowledge/{entry_id}/supersede", response_model=KnowledgeEntryOut)
async def supersede_knowledge(
    entry_id: int,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """软下线: status=superseded + is_superseded=True + 从 ChromaDB 删向量. 不删 DB 行, 保留审计."""
    entry = await db.get(KnowledgeEntry, entry_id)
    if not entry:
        raise HTTPException(404, "知识条目不存在")
    await assert_ns_access(db, actor, entry.namespace_id)

    ns_slug = ""
    if entry.namespace_id is not None:
        ns = await db.get(Namespace, entry.namespace_id)
        if ns:
            ns_slug = ns.slug

    entry.is_superseded = True
    entry.status = "superseded"  # Stage 1: 状态机统一驱动 RAG 进出
    await db.commit()
    await db.refresh(entry)

    try:
        await asyncio.to_thread(
            delete_knowledge_entry,
            slug=ns_slug,
            entry_id=entry.id,
            namespace_id=entry.namespace_id,
            entry_type=entry.entry_type,
        )
    except Exception as e:
        log.warning("[knowledge] supersede 向量删除失败 id=%d: %s", entry.id, e)

    # ── AC 自动机失效 + 重建 ──
    await automaton_invalidate_safe(db, entry.namespace_id, entry.entry_type)

    return entry


# ════════════════════════════════════════════
#  Git 仓库
# ════════════════════════════════════════════

@router.get("/api/namespaces/{ns_id}/repos", response_model=RepoListResponse)
async def list_repos(
    ns_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(GitRepo).where(GitRepo.namespace_id == ns_id).order_by(GitRepo.created_at.desc())
    )
    repos = result.scalars().all()
    out = [_enrich_repo_out(r) for r in repos]

    # ── 诊断日志: 有活跃 worker 时记录 API 响应快照 ──
    active = [o for o in out if o.get("worker_id")]
    if active:
        summary = " | ".join(
            f'{o["url"].rsplit("/",1)[-1]}:wid={o["worker_id"][:8]},p={o["progress"]}%,s={o["parse_status"]}'
            for o in active
        )
        log.info("[repos-poll] ns=%d active=%d [%s]", ns_id, len(active), summary)

    # ── 全量清场窗口: worker 尚未落库 worker_id, 靠 batch_status 暴露中间态,
    #    驱动前端轮询启动 + 横幅提示, worker 起来后 worker_id 接管 (见 batch_parse_repos) ──
    from app.engine.repo_worker import is_rebuilding

    batch_status = None
    if is_rebuilding(ns_id):
        batch_status = BatchStatus(active=True, progress="", message="清场中，准备重新解析…")

    return RepoListResponse(
        repos=[GitRepoOut.model_validate(o) for o in out],
        batch_status=batch_status,
    )


@router.post("/api/namespaces/{ns_id}/repos", response_model=GitRepoOut, status_code=201)
async def add_repo(
    ns_id: int,
    body: GitRepoCreate,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    ns = await db.get(Namespace, ns_id)
    if not ns:
        raise HTTPException(404, "命名空间不存在")
    repo = GitRepo(namespace_id=ns_id, url=body.url, branch=body.branch,
                   profile_id=body.profile_id)
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return _enrich_repo_out(repo)


@router.patch("/api/namespaces/{ns_id}/repos/{repo_id}", response_model=GitRepoOut)
async def patch_repo(
    ns_id: int,
    repo_id: int,
    body: dict,  # 兼容 Pydantic 严格模式, 手动校验
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """PATCH GitRepo — 当前仅支持更新 profile_id."""
    repo = await db.get(GitRepo, repo_id)
    if not repo or repo.namespace_id != ns_id:
        raise HTTPException(404, "Git repo 不存在")

    allowed = {"profile_id"}
    for k, v in body.items():
        if k not in allowed:
            raise HTTPException(400, f"PATCH 不支持字段: {k}")
        if k == "profile_id":
            if v is not None and not isinstance(v, int):
                raise HTTPException(400, "profile_id 必须为整数或 null")
            repo.profile_id = v

    await db.commit()
    await db.refresh(repo)
    return _enrich_repo_out(repo)


@router.delete("/api/namespaces/{ns_id}/repos/{repo_id}", status_code=204)
async def delete_repo(
    ns_id: int,
    repo_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """删除 Git 仓库 — 外键约束会级联删除相关 mappings 和 knowledge entries"""
    repo = await db.get(GitRepo, repo_id)
    if not repo or repo.namespace_id != ns_id:
        raise HTTPException(404, "仓库不存在")
    if repo.worker_id:
        raise HTTPException(409, "仓库正在解析中, 无法删除")
    # orphan hook: 在 CASCADE 删除前标记关联 candidate 为 orphaned
    from app.knowledge.candidate_cleanup import orphan_candidates_for_repo
    await orphan_candidates_for_repo(db, repo_id)
    await db.delete(repo)
    await db.commit()


@router.post("/api/namespaces/{ns_id}/repos/{repo_id}/parse")
async def parse_repo(
    ns_id: int,
    repo_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """触发异步解析 — 返回 worker_id, 通过 progress 端点查进度"""
    from app.engine.repo_worker import start_parse_worker

    repo = await db.get(GitRepo, repo_id)
    if not repo or repo.namespace_id != ns_id:
        raise HTTPException(404, "仓库不存在")
    if repo.worker_id:
        raise HTTPException(409, "该仓库正在解析中")

    worker_id = await start_parse_worker(repo_id, ns_id)
    return {"worker_id": worker_id, "message": "解析已启动"}



@router.get("/api/namespaces/{ns_id}/repos/{repo_id}/progress")
async def get_repo_progress(
    ns_id: int,
    repo_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """查询解析进度"""
    repo = await db.get(GitRepo, repo_id)
    if not repo or repo.namespace_id != ns_id:
        raise HTTPException(404, "仓库不存在")
    await db.refresh(repo)
    return {
        "parse_status": repo.parse_status,
        "progress": repo.progress,
        "progress_message": repo.progress_message,
        "worker_id": repo.worker_id,
    }


@router.post("/api/namespaces/{ns_id}/repos/batch-parse")
async def batch_parse_repos(
    ns_id: int,
    force: bool = False,
    admin: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """
    批量解析仓库

    增量模式 (force=False): 仅解析 pending/error 的 repo
    全量模式 (force=True):  清空命名空间全部知识库数据后重新解析所有 repo
    """
    from app.engine.repo_worker import start_parse_worker

    # ── 获取待解析的 repos ──
    if force:
        result = await db.execute(
            select(GitRepo).where(
                GitRepo.namespace_id == ns_id,
                GitRepo.worker_id == "",
            )
        )
    else:
        result = await db.execute(
            select(GitRepo).where(
                GitRepo.namespace_id == ns_id,
                GitRepo.parse_status.in_(["pending", "error"]),
                GitRepo.worker_id == "",
            )
        )
    repos = result.scalars().all()

    if not repos:
        msg = "无待解析仓库" if not force else "无可解析仓库"
        return {"started": 0, "workers": [], "message": msg}

    ns = await db.get(Namespace, ns_id)
    if not ns:
        raise HTTPException(404, "Namespace not found")

    # ═══════════════════════════════════════════
    #  全量模式: 清空命名空间全部知识库数据
    #  语义承诺 — force=True 等于"从零重建", 不留任何残余
    #  Stage 2 Task 3: KE 部分走 BulkOpGuard (audit + 人类编辑兜底)
    #
    #  异步执行: 清场可能耗时 >60s (BulkOpGuard 逐条 ChromaDB 删除),
    #  放到后台 task 避免 HTTP 超时.
    #
    #  可见性: 清场阶段 repo.worker_id 仍为空, 靠 worker_id 无法感知.
    #  故同步标记 _rebuilding_namespaces (在 create_task 之前), list_repos
    #  据此回填 batch_status.active, 前端轮询立即启动, worker 起来后无缝接管.
    # ═══════════════════════════════════════════
    if force:
        from app.engine.repo_worker import clear_rebuilding, mark_rebuilding

        repo_ids = [r.id for r in repos]

        async def _force_rebuild_task():
            """后台: 清场 → 启动 workers (串行化, 避免 SQLite 并发锁)"""
            from app.db.metadata import async_session as _async_session
            started_any = False
            try:
                async with _async_session() as clean_db:
                    mongo_stats = await _clean_namespace_mongo_canonical(clean_db, ns_id)
                    log.info(
                        "[batch-parse] 全量清理完成 ns=%s mongo=%s",
                        ns.slug, mongo_stats,
                    )
                for rid in repo_ids:
                    try:
                        await start_parse_worker(rid, ns_id)
                        started_any = True
                    except Exception as e:
                        log.error("[batch-parse] worker 启动失败 repo_id=%d: %s", rid, e)
            except Exception as e:
                log.error("[batch-parse] 全量清理异常 ns=%s: %s", ns.slug, e, exc_info=True)
            finally:
                # 清理所有权: 成功派发的 worker 各自在落库 worker_id 后 clear_rebuilding,
                # 由 worker_id 无缝接管可见性 — 此处「不能」抢在 worker 落库前清, 否则制造
                # 「标记已清 + worker_id 未落库」的盲窗, race 复发.
                # 仅当一个 worker 都没起来 (清场异常 / 全部启动失败) 时兜底清标记,
                # 防 batch_status 永久卡在 active.
                if not started_any:
                    clear_rebuilding(ns_id)

        # ── 同步标记必须先于 create_task: 保证 HTTP 响应返回时标记已就位 ──
        mark_rebuilding(ns_id)
        asyncio.create_task(_force_rebuild_task())

        return {
            "started": len(repos),
            "workers": [],
            "message": f"清场中，{len(repos)} 个解析任务将在清场完成后自动启动",
        }
    else:
        # 增量模式: MongoDB 由 trainer 内部 clear_repo_data 按 repo 精准清空
        pass

    # ── 启动所有 workers ──
    workers = []
    for repo in repos:
        wid = await start_parse_worker(repo.id, ns_id)
        workers.append({"repo_id": repo.id, "worker_id": wid})

    return {
        "started": len(workers),
        "workers": workers,
        "message": f"已启动 {len(workers)} 个解析任务"
    }


@router.post("/api/namespaces/{ns_id}/repos/{repo_id}/cancel")
async def cancel_parse(
    ns_id: int,
    repo_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """取消正在进行的解析 (含 terminology 阶段).

    精确度: main worker (repo.worker_id) 与 terminology worker
    (terminology_worker_key(repo_id)) 各自独立 cancel; 只要任一方还在跑就允许触发.
    """
    from app.engine.repo_worker import cancel_worker, is_worker_running
    from app.knowledge.trainer_terminology_stage import terminology_worker_key

    repo = await db.get(GitRepo, repo_id)
    if not repo or repo.namespace_id != ns_id:
        raise HTTPException(404, "仓库不存在")

    term_key = terminology_worker_key(repo_id)
    term_running = is_worker_running(term_key)
    if not repo.worker_id and not term_running:
        raise HTTPException(400, "该仓库未在解析中")

    cancelled = cancel_worker(repo.worker_id) if repo.worker_id else False
    term_cancelled = cancel_worker(term_key)

    if cancelled:
        repo.parse_status = "error"
        repo.error_message = "用户取消"
        repo.worker_id = ""
        repo.progress = 0
        repo.progress_message = ""
        await db.commit()
    elif repo.worker_id:
        # worker 已不在内存 (崩溃/已结束), 清理残留的 worker_id
        repo.worker_id = ""
        repo.progress_message = ""
        await db.commit()
    return {"cancelled": cancelled, "terminology_cancelled": term_cancelled}


# ════════════════════════════════════════════
#  仓库 ↔ 数据源映射
# ════════════════════════════════════════════

@router.get(
    "/api/namespaces/{ns_id}/repos/{repo_id}/mappings",
    response_model=list[RepoMappingOut],
)
async def list_repo_mappings(
    ns_id: int,
    repo_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RepoDataSourceMapping).where(RepoDataSourceMapping.repo_id == repo_id)
    )
    return result.scalars().all()


@router.post(
    "/api/namespaces/{ns_id}/repos/{repo_id}/mappings",
    response_model=RepoMappingOut,
    status_code=201,
)
async def add_repo_mapping(
    ns_id: int,
    repo_id: int,
    body: RepoMappingCreate,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    repo = await db.get(GitRepo, repo_id)
    if not repo or repo.namespace_id != ns_id:
        raise HTTPException(404, "仓库不存在")

    mapping = RepoDataSourceMapping(repo_id=repo_id, datasource_id=body.datasource_id)
    db.add(mapping)
    await db.commit()
    await db.refresh(mapping)
    return mapping


@router.delete("/api/namespaces/{ns_id}/repos/{repo_id}/mappings/{mapping_id}", status_code=204)
async def delete_repo_mapping(
    ns_id: int,
    repo_id: int,
    mapping_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    mapping = await db.get(RepoDataSourceMapping, mapping_id)
    if not mapping or mapping.repo_id != repo_id:
        raise HTTPException(404, "映射不存在")
    await db.delete(mapping)
    await db.commit()


# ════════════════════════════════════════════
#  解析报告 + 澄清
# ════════════════════════════════════════════

@router.get(
    "/api/namespaces/{ns_id}/repos/{repo_id}/report",
    response_model=ParseReportOut,
)
async def get_parse_report(
    ns_id: int,
    repo_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """查看历史解析报告"""
    repo = await db.get(GitRepo, repo_id)
    if not repo or repo.namespace_id != ns_id:
        raise HTTPException(404, "仓库不存在")
    if not repo.parse_report:
        raise HTTPException(404, "暂无解析报告")
    return ParseReportOut(**json.loads(repo.parse_report))


async def _clean_namespace_mongo_canonical(db: AsyncSession, ns_id: int) -> dict:
    """步骤 3: schema canonical 三表清空 (Object + Candidate + Conflict)

    全量重建语义承诺 "从零重建, 不留任何残余" — 必须同时清三表:
    - SchemaCanonicalObject:    promote 后落地的 canonical 数据
    - SchemaCanonicalCandidate: 解析期写入的候选 (UPSERT by value_hash, 但旧 repo
                                  删除的字段产出的孤儿 candidate 永不被覆盖, 会污染
                                  新一轮 promote 9 分支判断, 制造假冲突)
    - SchemaCanonicalConflict:  candidate 派生的冲突队列 (candidate 清空后必须同清,
                                  否则悬挂引用)

    审计追溯: canonical_candidates_audit_log 已记录全部 auto_extract 事件, 是不可变
    历史, 不随 candidate 一起清.
    """
    from app.models import (
        SchemaCanonicalCandidate,
        SchemaCanonicalConflict,
        SchemaCanonicalObject,
    )

    stats = {
        "schema_canonical_objects": 0,
        "schema_canonical_candidates": 0,
        "schema_canonical_conflicts": 0,
    }

    # 顺序: conflict → candidate → object (从派生表到主表, 避免悬挂引用)
    conf_res = await db.execute(
        delete(SchemaCanonicalConflict).where(
            SchemaCanonicalConflict.namespace_id == ns_id,
        )
    )
    stats["schema_canonical_conflicts"] = getattr(conf_res, "rowcount", 0) or 0

    cand_res = await db.execute(
        delete(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
        )
    )
    stats["schema_canonical_candidates"] = getattr(cand_res, "rowcount", 0) or 0

    sco_res = await db.execute(
        delete(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == ns_id,
        )
    )
    stats["schema_canonical_objects"] = getattr(sco_res, "rowcount", 0) or 0

    if any(stats.values()):
        await db.commit()
        log.info(
            "[clean] schema canonical 清空 sco=%d cand=%d conf=%d",
            stats["schema_canonical_objects"],
            stats["schema_canonical_candidates"],
            stats["schema_canonical_conflicts"],
        )

    return stats



def _enrich_repo_out(repo: GitRepo) -> dict:
    """GitRepo ORM → GitRepoOut 兼容 dict (含 has_report, completeness_score)"""
    data = {
        "id": repo.id,
        "url": repo.url,
        "branch": repo.branch,
        "parse_status": repo.parse_status,
        "error_message": repo.error_message,
        "created_at": repo.created_at,
        "parsed_at": repo.parsed_at,
        "has_report": bool(repo.parse_report),
        "completeness_score": 0,
        "worker_id": repo.worker_id,
        "progress": repo.progress,
        "progress_message": repo.progress_message,
        "profile_id": repo.profile_id,
    }
    if repo.parse_report:
        try:
            report_data = json.loads(repo.parse_report)
            data["completeness_score"] = report_data.get("completeness_score", 0)
        except json.JSONDecodeError:
            pass
    return data


