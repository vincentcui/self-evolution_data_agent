"""Stage 2 抓手 E — agent_traces 列表 / 详情 / 批量提炼 API."""

from __future__ import annotations

import asyncio
import json as _json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    ROLE_SUPER_ADMIN,
    accessible_namespace_ids,
    assert_ns_access,
    require_admin_or_above,
    role_at_least,
)
from app.config import settings
from app.db.metadata import get_db
from app.models import AgentTrace
from app.models.base import local_now
from app.models.user import User

router = APIRouter(tags=["agent-traces"])
log = logging.getLogger(__name__)


@router.get("/api/agent-traces")
async def list_traces(
    namespace_id: int | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """列表 agent_traces, 支持 namespace_id / status 过滤, 分页."""
    allowed = await accessible_namespace_ids(db, actor)
    stmt = select(AgentTrace).order_by(AgentTrace.created_at.desc())
    if namespace_id is not None:
        if allowed is not None and namespace_id not in allowed:
            raise HTTPException(403, f"No access to namespace {namespace_id}")
        stmt = stmt.where(AgentTrace.namespace_id == namespace_id)
    elif allowed is not None:
        stmt = stmt.where(AgentTrace.namespace_id.in_(allowed))
    if status:
        stmt = stmt.where(AgentTrace.status == status)
    stmt = stmt.offset((page - 1) * size).limit(size)
    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for r in rows:
        # tool_call_count: 解析 trace_json 取 tool_trace 列表长度
        tcc = 0
        trace_damaged = False
        if r.trace_json:
            try:
                trace_data = _json.loads(r.trace_json)
                if isinstance(trace_data, list):
                    tcc = len(trace_data)
                elif isinstance(trace_data, dict):
                    tt = trace_data.get("tool_trace")
                    tcc = len(tt) if isinstance(tt, list) else 0
            except (_json.JSONDecodeError, TypeError):
                tcc = None
                trace_damaged = True
        out.append({
            "id": r.id,
            "trace_id": r.trace_id,
            "namespace_id": r.namespace_id,
            "user_query": r.user_query,
            "status": r.status,
            "refined_at": r.refined_at.isoformat() if r.refined_at else None,
            "created_at": r.created_at.isoformat(),
            "tool_call_count": tcc,
            "trace_damaged": trace_damaged,
        })
    return out


@router.get("/api/agent-traces/{trace_id}")
async def get_trace_detail(
    trace_id: str,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """获取单条 trace 详情 (含完整 trace_json + reflection_log_json)."""
    row = (await db.execute(
        select(AgentTrace).where(AgentTrace.trace_id == trace_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "trace 不存在")
    if row.namespace_id is None:
        # 无 ns 的历史 trace 仅 super_admin 可见
        if not role_at_least(actor, ROLE_SUPER_ADMIN):
            raise HTTPException(403, "No access")
    else:
        await assert_ns_access(db, actor, row.namespace_id)

    # ── tool_trace_compact: trace_json.tool_trace → compact_tool_call 只读投影 ──
    # 唯一真相源是 trace_json.tool_trace; compact 是纯投影, 不碰 reflection
    # (reflection 合并放前端, 保持 compact_tool_call 与提取器 inflection_points 产线解耦).
    from app.knowledge.trace_compression import compact_tool_call

    tool_trace_raw: list = []
    trace_damaged = False
    try:
        tj = _json.loads(row.trace_json) if row.trace_json else {}
        raw = tj.get("tool_trace") if isinstance(tj, dict) else None
        if isinstance(raw, list):
            tool_trace_raw = raw
    except (ValueError, TypeError):
        tool_trace_raw = []
        trace_damaged = True
    tool_trace_compact = [compact_tool_call(i, c) for i, c in enumerate(tool_trace_raw)]

    return {
        "id": row.id,
        "trace_id": row.trace_id,
        "namespace_id": row.namespace_id,
        "user_query": row.user_query,
        "trace_json": row.trace_json,
        "reflection_log_json": row.reflection_log_json,
        "tool_trace_compact": tool_trace_compact,
        "trace_damaged": trace_damaged,
        "status": row.status,
        "refined_at": row.refined_at.isoformat() if row.refined_at else None,
        "refined_summary": row.refined_summary,
        "created_at": row.created_at.isoformat(),
    }


# ════════════════════════════════════════════
#  POST /api/agent-traces/refine — 批量提炼
# ════════════════════════════════════════════


class RefineRequest(BaseModel):
    trace_ids: list[str]


class RefineOut(BaseModel):
    proposed_count: int
    proposed_ke_ids: list[int]


@router.post("/api/agent-traces/refine", response_model=RefineOut)
async def refine_traces_endpoint(
    body: RefineRequest,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """批量提炼 agent traces → 产 proposed KE 入待审池."""
    if len(body.trace_ids) > settings.agent_trace_refine_batch_max:
        raise HTTPException(
            422, f"batch 上限 {settings.agent_trace_refine_batch_max}",
        )
    rows = (await db.execute(
        select(AgentTrace).where(
            AgentTrace.trace_id.in_(body.trace_ids),
            AgentTrace.status == "completed",
        )
    )).scalars().all()
    if not rows:
        return RefineOut(proposed_count=0, proposed_ke_ids=[])

    # ── ns 作用域 (Phase 3.7): 对涉及的所有 ns 逐一校验 (任一越权 403) ──
    for nid in {r.namespace_id for r in rows}:
        if nid is None:
            if not role_at_least(actor, ROLE_SUPER_ADMIN):
                raise HTTPException(403, "No access")
        else:
            await assert_ns_access(db, actor, nid)

    # ── 解析 namespace ─ refine 走 save_knowledge 需要 (ns_id, ns_slug). ──
    #    所有 trace 同 namespace_id (前端只在单 ns 视角发起批量提炼).      ──
    from app.models.namespace import Namespace
    ns_id = rows[0].namespace_id
    ns_slug: str | None = None
    if ns_id is not None:
        ns = (await db.execute(
            select(Namespace).where(Namespace.id == ns_id)
        )).scalar_one_or_none()
        ns_slug = ns.slug if ns else None

    # ── 拉 critical rules 注入 trace_refiner 已知禁区, 防 LLM 重复总结 ──
    from sqlalchemy import select as _select

    from app.knowledge.trace_refiner import refine_traces
    from app.models.knowledge_entry import KnowledgeEntry
    critical_rules: list[str] = []
    if ns_id is not None:
        critical_stmt = (
            _select(KnowledgeEntry.content)
            .where(KnowledgeEntry.namespace_id == ns_id)
            .where(KnowledgeEntry.tier == "critical")
            .where(KnowledgeEntry.status == "canonical")
        )
        critical_rules = [
            r[0] for r in (await db.execute(critical_stmt)).all() if r[0]
        ]

    payload = [
        {
            "trace_id": r.trace_id,
            "user_query": r.user_query,
            "trace_json": r.trace_json,
            "reflection_log_json": r.reflection_log_json,
        }
        for r in rows
    ]
    proposed = await asyncio.to_thread(refine_traces, payload, critical_rules)

    # ── Phase 2: allowlist 过滤 LLM payload + trace_extractor 补机械字段 ──
    from app.knowledge.trace_extractor import (
        derive_cost_strategy,
        extract_collections,
        extract_db_context,
        extract_final_pipeline,
        extract_join_fields,
        extract_join_keys,
        normalize_query_plan,
    )

    # LLM 语义字段 allowlist — 多塞字段静默丢弃
    llm_allowed_fields: dict[str, set[str]] = {
        "terminology": {"term", "primary_collection", "synonyms",
                        "primary_field", "source_collections"},
        "instance_alias": {"alias", "canonical_name", "target_id", "id_field"},
        "route_hint": {"question_pattern", "reason", "avoid_path"},
        "rule": {"rule_text", "rule_kind", "applies_to_collections",
                 "priority", "evidence"},
        "example": {"question_pattern", "result_summary",
                    "collections", "join_keys", "final_query_plan"},
    }

    trace_by_id: dict[str, "AgentTrace"] = {r.trace_id: r for r in rows}

    for p in proposed:
        # ── allowlist 过滤 LLM payload ──
        allowed = llm_allowed_fields.get(p.entry_type, set())
        if allowed:
            p.payload = {k: v for k, v in (p.payload or {}).items() if k in allowed}

        # ── code 补机械字段 ──
        src = trace_by_id.get(p.source_trace_id)
        if src is None and rows:
            # fallback: LLM 没返 source_trace_id 时用第一条 trace
            src = rows[0]
        if src is None:
            continue

        try:
            tool_trace = (
                _json.loads(src.trace_json or "{}") or {}
            ).get("tool_trace") or []
            if isinstance(tool_trace, list) is False:
                tool_trace = []
        except (_json.JSONDecodeError, TypeError):
            tool_trace = []

        collections = extract_collections(tool_trace)
        db_type, database = extract_db_context(tool_trace)

        if p.entry_type == "terminology":
            if db_type:
                p.payload["db_type"] = db_type
            if database:
                p.payload["primary_database"] = database
            # primary_collection LLM 已产语义判断, 缺则用 trace 第一个集合兜底
            if not p.payload.get("primary_collection") and collections:
                p.payload["primary_collection"] = collections[0]
        elif p.entry_type == "instance_alias":
            if database:
                p.payload["target_database"] = database
            if not p.payload.get("target_collection") and collections:
                p.payload["target_collection"] = collections[0]
        elif p.entry_type == "example":
            qplan = normalize_query_plan(tool_trace)
            if qplan is not None:
                p.payload["final_query_plan"] = qplan
            if collections:
                p.payload["collections"] = collections
            joins = extract_join_keys(qplan)
            if joins:
                p.payload["join_keys"] = joins
        elif p.entry_type == "route_hint":
            if collections:
                p.payload["collection_path"] = collections
            p.payload["cost_strategy"] = derive_cost_strategy(tool_trace)
            final_pipeline = extract_final_pipeline(tool_trace)
            joins = extract_join_fields(final_pipeline)
            if joins:
                p.payload["join_fields"] = joins
        elif p.entry_type == "rule":
            if collections:
                p.payload.setdefault("applies_to_collections", collections)

    # ── 收口到 save_knowledge 接口: 抓手 D 演化 + terminology 唯一键闸门 + ──
    #     instance_alias schema 校验 — 同一治理路径与 agent 自学等价 (spec     ──
    #     02-stage2-pull-reinforcement.md 写入治理表第 3 / 4 行同列要求).     ──
    from app.engine.tools.knowledge_tools import save_knowledge

    async def _noop_sse(_evt: dict) -> None:
        return None

    new_ids: list[int] = []
    ns_slug_for_save = ns_slug or ""
    for p in proposed:
        try:
            ret = await save_knowledge(
                db=db,
                namespace_id=ns_id,
                ns_slug=ns_slug_for_save,
                sse_emit=_noop_sse,
                entry_type=p.entry_type,
                content=p.content,
                payload=p.payload,
                evidence=p.evidence,
                tier="normal",
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[refine] save_knowledge 写入异常, 跳过该提案: type=%s reason=%s",
                p.entry_type, e,
            )
            await db.rollback()
            continue

        # save_knowledge 校验/冲突失败时不返回 entry_id, 跳过.
        if not isinstance(ret, dict) or "entry_id" not in ret:
            log.info(
                "[refine] proposal skipped by save_knowledge: type=%s reason=%r",
                p.entry_type, ret.get("reason") if isinstance(ret, dict) else ret,
            )
            continue
        new_ids.append(int(ret["entry_id"]))

    # 标 traces 为 refined
    for r in rows:
        r.status = "refined"
        r.refined_at = local_now()
        r.refined_summary = _json.dumps({
            "proposed_ke_ids": new_ids,
            "count": len(proposed),
        }, ensure_ascii=False)
    await db.commit()

    return RefineOut(proposed_count=len(proposed), proposed_ke_ids=new_ids)
