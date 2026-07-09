"""
工作台首页汇总 — 我的空间卡片 + 统计数字 + 最近使用会话
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import accessible_namespace_ids, get_current_user
from app.db.metadata import get_db
from app.models import DataSource, Namespace
from app.models.git_repo import GitRepo
from app.models.knowledge_entry import KnowledgeEntry
from app.models.model_config import ModelConfig
from app.models.schema_canonical_object import SchemaCanonicalObject
from app.models.session import Session
from app.models.user import User
from app.schemas import (
    WorkbenchNamespaceCardOut,
    WorkbenchRecentSessionOut,
    WorkbenchSummaryOut,
)

router = APIRouter(prefix="/api/workbench", tags=["workbench"])

# 最近使用会话展示条数上限 (与图1"最近使用"面板对齐)
_RECENT_SESSION_LIMIT = 10  # noqa: hardcode


@router.get("/summary", response_model=WorkbenchSummaryOut)
async def get_workbench_summary(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """工作台首页汇总 — 我的空间(权限过滤) + ready/pending 统计 + 最近会话。"""
    allowed = await accessible_namespace_ids(db, user)

    ns_stmt = select(Namespace).order_by(Namespace.created_at.desc())
    if allowed is not None:
        ns_stmt = ns_stmt.where(Namespace.id.in_(allowed))
    namespaces = (await db.execute(ns_stmt)).scalars().all()
    ns_ids = [ns.id for ns in namespaces]

    # ── 全局是否有已激活 CHAT 模型 (namespace_id IS NULL 的兜底配置) ──
    global_chat_active = await db.scalar(
        select(func.count()).select_from(ModelConfig).where(
            ModelConfig.model_type == "CHAT",
            ModelConfig.namespace_id.is_(None),
            ModelConfig.is_active.is_(True),
            ModelConfig.is_deleted.is_(False),
        )
    )
    has_global_chat = (global_chat_active or 0) > 0

    # ── 各空间是否有自己的已激活 CHAT 模型 (空间级配置) ──
    ns_chat_active_ids: set[int] = set()
    if ns_ids:
        ns_chat_rows = (await db.execute(
            select(ModelConfig.namespace_id).where(
                ModelConfig.model_type == "CHAT",
                ModelConfig.namespace_id.in_(ns_ids),
                ModelConfig.is_active.is_(True),
                ModelConfig.is_deleted.is_(False),
            )
        )).scalars().all()
        ns_chat_active_ids = set(ns_chat_rows)

    # ── 全局是否有已激活 EMBEDDING 模型 (readiness 五项判定之一, 与 namespace 无关) ──
    embedding_active = await db.scalar(
        select(func.count()).select_from(ModelConfig).where(
            ModelConfig.model_type == "EMBEDDING",
            ModelConfig.is_active.is_(True),
            ModelConfig.is_deleted.is_(False),
        )
    )
    has_embedding_key = (embedding_active or 0) > 0

    # ── 按 namespace 分组统计: 数据源数 / SCO 数(schema 已采集判据) ──
    ds_counts: dict[int, int] = {}
    schema_counts: dict[int, int] = {}
    knowledge_counts: dict[int, int] = {}
    session_counts: dict[int, int] = {}
    git_total_counts: dict[int, int] = {}
    git_parsed_counts: dict[int, int] = {}

    if ns_ids:
        ds_rows = (await db.execute(
            select(DataSource.namespace_id, func.count()).where(
                DataSource.namespace_id.in_(ns_ids)
            ).group_by(DataSource.namespace_id)
        )).all()
        ds_counts = {r[0]: r[1] for r in ds_rows}

        schema_rows = (await db.execute(
            select(SchemaCanonicalObject.namespace_id, func.count()).where(
                SchemaCanonicalObject.namespace_id.in_(ns_ids)
            ).group_by(SchemaCanonicalObject.namespace_id)
        )).all()
        schema_counts = {r[0]: r[1] for r in schema_rows}

        knowledge_rows = (await db.execute(
            select(KnowledgeEntry.namespace_id, func.count()).where(
                KnowledgeEntry.namespace_id.in_(ns_ids),
                KnowledgeEntry.status == "canonical",
            ).group_by(KnowledgeEntry.namespace_id)
        )).all()
        knowledge_counts = {r[0]: r[1] for r in knowledge_rows}

        # 会话数 — 仅统计当前用户自己创建的会话, 与"最近会话"面板同一口径
        session_rows = (await db.execute(
            select(Session.namespace_id, func.count()).where(
                Session.namespace_id.in_(ns_ids),
                Session.created_by == user.id,
            ).group_by(Session.namespace_id)
        )).all()
        session_counts = {r[0]: r[1] for r in session_rows}

        git_total_rows = (await db.execute(
            select(GitRepo.namespace_id, func.count()).where(
                GitRepo.namespace_id.in_(ns_ids)
            ).group_by(GitRepo.namespace_id)
        )).all()
        git_total_counts = {r[0]: r[1] for r in git_total_rows}

        git_parsed_rows = (await db.execute(
            select(GitRepo.namespace_id, func.count()).where(
                GitRepo.namespace_id.in_(ns_ids),
                GitRepo.parse_status == "parsed",
            ).group_by(GitRepo.namespace_id)
        )).all()
        git_parsed_counts = {r[0]: r[1] for r in git_parsed_rows}

    cards: list[WorkbenchNamespaceCardOut] = []
    ready_count = 0
    for ns in namespaces:
        has_datasource = ds_counts.get(ns.id, 0) > 0
        has_valid_schema = schema_counts.get(ns.id, 0) > 0
        ready = has_datasource and has_valid_schema and (has_global_chat or ns.id in ns_chat_active_ids)
        if ready:
            ready_count += 1
        cards.append(WorkbenchNamespaceCardOut(
            id=ns.id,
            name=ns.name,
            slug=ns.slug,
            description=ns.description,
            created_at=ns.created_at,
            ready=ready,
            datasource_count=ds_counts.get(ns.id, 0),
            session_count=session_counts.get(ns.id, 0),
            git_parsed_count=git_parsed_counts.get(ns.id, 0),
            git_total_count=git_total_counts.get(ns.id, 0),
            knowledge_count=knowledge_counts.get(ns.id, 0),
            has_embedding_key=has_embedding_key,
        ))

    # ── 最近使用 — 当前用户跨空间最近会话 (按 updated_at desc) ──
    recent_sessions: list[WorkbenchRecentSessionOut] = []
    if ns_ids:
        ns_name_by_id = {ns.id: ns.name for ns in namespaces}
        recent_rows = (await db.execute(
            select(Session).where(
                Session.namespace_id.in_(ns_ids),
                Session.created_by == user.id,
            ).order_by(Session.updated_at.desc()).limit(_RECENT_SESSION_LIMIT)
        )).scalars().all()
        recent_sessions = [
            WorkbenchRecentSessionOut(
                id=str(s.id),
                namespace_id=s.namespace_id,
                namespace_name=ns_name_by_id.get(s.namespace_id, ""),
                title=s.title,
                updated_at=s.updated_at,
            )
            for s in recent_rows
        ]

    total_session_count = sum(session_counts.values())

    return WorkbenchSummaryOut(
        accessible_count=len(namespaces),
        ready_count=ready_count,
        pending_count=len(namespaces) - ready_count,
        recent_session_count=total_session_count,
        namespaces=cards,
        recent_sessions=recent_sessions,
    )
