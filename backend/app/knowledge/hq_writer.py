"""统一 HQ writer — SQL hypothetical_queries_json + ChromaDB hq_* 子向量同步.

三触发点共用:
  1. api/audit.py::approve_entry (proposed → canonical)
  2. api/knowledge.py::edit_entry — content 变化 + entry_type ∈ {rule, route_hint}
  3. api/knowledge.py::edit_entry — body.hypothetical_queries 手改

manual_hqs=None: LLM 重生 (auto)
manual_hqs=[...]: 用户手改, 直接落库不重验

不在内部 commit. caller 负责事务边界.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.hypothetical_queries import generate_hq_with_validation
from app.knowledge.knowledge_retriever import rewrite_hq_subvectors
from app.models.knowledge_entry import KnowledgeEntry

log = logging.getLogger(__name__)


async def build_terminology_lookup(
    db: AsyncSession,
    namespace_id: int | None,
    collections: list[str],
) -> dict[str, list[str]]:
    """从 KnowledgeEntry (entry_type='terminology', status='canonical', is_superseded=False)
    反查每个 collection 的 term + synonyms 列表, 给 text_includes_collections lenient 模式用.

    返回: {collection: [collection_name, term, syn1, syn2, ...]}, 每个 collection 至少含自身名.
    """
    out: dict[str, list[str]] = {c: [c] for c in collections}
    if not collections:
        return out

    rows = (await db.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.entry_type == "terminology",
            KnowledgeEntry.status == "canonical",
            KnowledgeEntry.is_superseded.is_(False),
            (
                (KnowledgeEntry.namespace_id == namespace_id)
                | (KnowledgeEntry.namespace_id.is_(None))
            ),
        )
    )).scalars().all()

    for ke in rows:
        try:
            payload = json.loads(ke.payload or "{}")
        except json.JSONDecodeError:
            continue
        coll = payload.get("primary_collection")
        if coll not in out:
            continue
        term = payload.get("term")
        syns = payload.get("synonyms") or []
        if term:
            out[coll].append(term)
        for s in syns:
            if s:
                out[coll].append(s)
    return out


async def rewrite_hq_for_entry(
    db: AsyncSession,
    slug: str,
    entry: KnowledgeEntry,
    *,
    manual_hqs: list[str] | None = None,
) -> list[str]:
    """统一 HQ writer.

    Modes:
      - manual_hqs=None: 自动 (LLM + 严格 covered_path 校验 + terminology_lookup)
      - manual_hqs=[...]: 手改 (跳过 LLM 与校验)

    返回最终落库的 hq 文本列表.
    不在内部 commit — caller 负责事务边界.
    """
    if entry.entry_type not in {"rule", "route_hint"}:
        return []

    if manual_hqs is None:
        route_path = _extract_route_path(entry)
        # B3 修订: 真接线 terminology_lookup, lenient 模式下同义词扩展生效
        terminology_lookup: dict[str, list[str]] | None = None
        if route_path:
            terminology_lookup = await build_terminology_lookup(
                db, entry.namespace_id, route_path,
            )
        hqs = await asyncio.to_thread(
            generate_hq_with_validation,
            entry.content,
            entry_type=entry.entry_type,
            route_collection_path=route_path,
            terminology_lookup=terminology_lookup,
        )
        from app.engine.model_registry import registry

        model_label = (
            registry.chat_config.get("model_name", "unknown")
            if registry.chat_config
            else "unknown"
        )
    else:
        hqs = [s.strip() for s in manual_hqs if s and s.strip()]
        model_label = "manual"

    # ── 1. SQL hypothetical_queries_json ──
    entry.hypothetical_queries_json = json.dumps([
        {
            "q": q,
            "generated_at": datetime.now().isoformat(),
            "model": model_label,
        }
        for q in hqs
    ], ensure_ascii=False)
    await db.flush()

    # ── 2. ChromaDB best-effort + audit_log on failure (SF-3) ──
    try:
        await asyncio.to_thread(
            rewrite_hq_subvectors,
            slug=slug,
            entry_id=entry.id,
            entry_type=entry.entry_type,
            tier=entry.tier,
            namespace_id=entry.namespace_id,
            content=entry.content,
            hq_list=hqs,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[hq_writer] chroma rewrite fail entry=%d: %s", entry.id, e)
        try:
            from app.knowledge.audit import write_audit
            await write_audit(
                db, entry_id=entry.id, action="chroma_sync_failed",
                from_status=entry.status, to_status=entry.status,
                actor_id=None,
                reason=f"hq rewrite chroma fail: {str(e)[:200]}",
            )
        except Exception:  # noqa: BLE001
            pass

    return hqs


def _extract_route_path(entry: KnowledgeEntry) -> list[str] | None:
    """从 entry.payload 提取 collection_path (用于 route_hint covered_path 校验)."""
    try:
        payload = json.loads(entry.payload or "{}")
    except json.JSONDecodeError:
        return None
    cp = payload.get("collection_path")
    return cp if isinstance(cp, list) else None
