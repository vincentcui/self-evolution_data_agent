"""Phase 4 Task 4.1: agent_loop 主链路单一知识加载入口.

设计目标:
- 单次调用 → 一次性加载 critical (SQL) + 向量召回 (route_hint / 其他),
  统一返回 KnowledgeBundle (含 critical / vector_hits / route_hints_for_prompt 3 维度).
- terminology 锚点已独立走 AC 自动机精确匹配 (terminology_automaton.py), 不再经向量检索.
- agent_loop 主链路只关心"宏观知识包", 不必感知 critical 是 SQL / vector 是 ChromaDB
  的实现差异. Phase 4 Task 4.3 起所有调用方收敛到此入口.
- 失败兜底: timeout / SQL 异常 / ChromaDB 异常任一都降级返空 bundle, 不阻塞 pipeline.

数据流:
    load_all_knowledge(db, ns_id, ns_slug, question)
      └─ asyncio.wait_for(_load_inner, timeout=settings.knowledge_loader_timeout_secs)
           ├─ task1: _load_layer1_knowledge (SQL critical)
           └─ task2: retrieve_layer3 (ChromaDB, asyncio.to_thread 包同步)
              ↓
           分层批查 SQLite payload → RouteHintCandidate
              ↓
           KnowledgeBundle (含 to_prompt_sections() 渲染入口)
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from langfuse import observe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.tools._mongo_helpers import record_span_io
from app.knowledge.knowledge_retriever import KnowledgeHit, _retrieve_layer3
from app.models.knowledge_entry import KnowledgeEntry

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  注入数据结构 — agent_loop prompt 直消费
# ════════════════════════════════════════════

@dataclass
class TerminologyAnchor:
    """terminology payload 结构化 view (agent_loop prompt 直注入)."""
    term: str
    target: str              # 表名/集合名 (原 primary_collection)
    database: str            # 数据库名 (原 primary_database)
    db_type: str
    synonyms: list[str] = field(default_factory=list)
    source_collections: list[str] = field(default_factory=list)

    # 向后兼容属性 (旧代码可能读 primary_collection/primary_database)
    @property
    def primary_collection(self) -> str:
        return self.target

    @property
    def primary_database(self) -> str:
        return self.database


@dataclass
class RouteHintCandidate:
    """route_hint payload 结构化 view (agent_loop prompt 直注入)."""
    question_pattern: str
    collection_path: list[str]
    join_fields: list[dict] = field(default_factory=list)
    cost_strategy: str = "default"
    reason: str = ""


@dataclass
class KnowledgeBundle:
    """agent_loop 主链路单次调用的知识快照.

    三个字段语义:
      - critical:               critical tier 直加载文本 (SQL, 无 embedding)
      - vector_hits:            retrieve_layer3 原始召回 (含 entry_id 供回查)
      - route_hints_for_prompt: vector_hits 中 route_hint 类的结构化 view (k cap)

    注: terminology 锚点已独立走 AC 自动机 (terminology_automaton.py), 不再经此 bundle.
    """
    critical: list[str]
    vector_hits: list[KnowledgeHit]
    route_hints_for_prompt: list[RouteHintCandidate]

    def to_prompt_sections(self) -> dict[str, str]:
        """渲染 prompt section. route_hint 已摘除 — 方法类知识由 LLM 主动 lookup_knowledge 召回."""
        return {
            "critical_section": self._render_critical(),
            "anchors_section": "",  # terminology 走 AC 自动机, 由调用方注入
            "route_hints_section": "",  # route_hint 不再注入 (spec 2026-07-07)
        }

    # ── 渲染辅助 ──
    def _render_critical(self) -> str:
        if not self.critical:
            return ""
        lines = ["## 关键规则 (critical)"]
        lines.extend(f"- {c}" for c in self.critical)
        return "\n".join(lines)

# ════════════════════════════════════════════
#  内部加载器
# ════════════════════════════════════════════

def _empty_bundle() -> KnowledgeBundle:
    return KnowledgeBundle(
        critical=[], vector_hits=[],
        route_hints_for_prompt=[],
    )


async def _load_layer1_knowledge(
    db: AsyncSession, ns_id: int,
) -> list[str]:
    """tier=critical + status=canonical 的 KE → content list (SQL 直加载)."""
    stmt = (
        select(KnowledgeEntry.content)
        .where(KnowledgeEntry.namespace_id == ns_id)
        .where(KnowledgeEntry.tier == "critical")
        .where(KnowledgeEntry.status == "canonical")
    )
    res = await db.execute(stmt)
    return [r[0] for r in res.all() if r[0]]


async def batch_load_terminology(
    db: AsyncSession, entry_ids: list[int],
) -> list[TerminologyAnchor]:
    """按 entry_ids 顺序批量取 payload → TerminologyAnchor (保留召回顺序).

    公开接口: 供 query.py 在 AC 自动机匹配后加载 payload.
    """
    if not entry_ids:
        return []
    stmt = select(KnowledgeEntry.id, KnowledgeEntry.payload).where(
        KnowledgeEntry.id.in_(entry_ids)
    )
    res = await db.execute(stmt)
    by_id: dict[int, dict] = {}
    for row_id, payload_str in res.all():
        try:
            by_id[row_id] = json.loads(payload_str or "{}")
        except json.JSONDecodeError:
            log.warning("[loader] terminology ke=%d payload not JSON, skip", row_id)
            continue

    out: list[TerminologyAnchor] = []
    for eid in entry_ids:
        p = by_id.get(eid)
        if not p:
            continue
        try:
            out.append(TerminologyAnchor(
                term=p["term"],
                target=p.get("target") or p.get("primary_collection", ""),
                database=p.get("database") or p.get("primary_database", ""),
                db_type=p["db_type"],
                synonyms=list(p.get("synonyms") or []),
                source_collections=list(p.get("source_collections") or []),
            ))
        except KeyError as exc:
            log.warning("[loader] terminology ke=%d missing field %s, skip", eid, exc)
    return out


async def _batch_load_route_hints(
    db: AsyncSession, entry_ids: list[int],
) -> list[RouteHintCandidate]:
    """按 entry_ids 顺序批量取 payload → RouteHintCandidate (保留召回顺序)."""
    if not entry_ids:
        return []
    stmt = select(KnowledgeEntry.id, KnowledgeEntry.payload).where(
        KnowledgeEntry.id.in_(entry_ids)
    )
    res = await db.execute(stmt)
    by_id: dict[int, dict] = {}
    for row_id, payload_str in res.all():
        try:
            by_id[row_id] = json.loads(payload_str or "{}")
        except json.JSONDecodeError:
            log.warning("[loader] route_hint ke=%d payload not JSON, skip", row_id)
            continue

    out: list[RouteHintCandidate] = []
    for eid in entry_ids:
        p = by_id.get(eid)
        if not p:
            continue
        out.append(RouteHintCandidate(
            question_pattern=str(p.get("question_pattern", "")),
            collection_path=list(p.get("collection_path") or []),
            join_fields=list(p.get("join_fields") or []),
            cost_strategy=str(p.get("cost_strategy") or "default"),
            reason=str(p.get("reason") or ""),
        ))
    return out


async def _load_inner(
    db: AsyncSession, ns_id: int, ns_slug: str, question: str,
) -> KnowledgeBundle:
    """concurrent gather: critical SQL + vector retrieve, 然后分层加载 payload."""
    critical_task = asyncio.create_task(_load_layer1_knowledge(db, ns_id))
    # _retrieve_layer3 是同步函数, 用 to_thread 包装以并行
    vector_task = asyncio.create_task(asyncio.to_thread(
        _retrieve_layer3, ns_slug, question,
    ))
    critical, vector_hits = await asyncio.gather(critical_task, vector_task)

    # route_hint 不再预注入 system prompt — 改由 LLM 按需 lookup_knowledge 召回 (spec 2026-07-07)
    route_hints: list[RouteHintCandidate] = []

    bundle = KnowledgeBundle(
        critical=list(critical),
        vector_hits=list(vector_hits),
        route_hints_for_prompt=route_hints,
    )
    record_span_io(
        name="load_all_knowledge",
        input={
            "ns_id": ns_id,
            "ns_slug": ns_slug,
            "question": question[:200],
            "entry_types": ["route_hint"],
        },
        output={
            "critical_count": len(bundle.critical),
            "route_hint_count": len(bundle.route_hints_for_prompt),
            "route_hint_hit_ids": [],
        },
    )
    return bundle


# ════════════════════════════════════════════
#  公开入口
# ════════════════════════════════════════════

@observe(name="load_all_knowledge", as_type="chain", capture_input=False, capture_output=False)
async def load_all_knowledge(
    db: AsyncSession, ns_id: int, ns_slug: str, question: str,
) -> KnowledgeBundle:
    """agent_loop 主链路单一知识加载入口.

    增强非阻断: timeout / SQL / ChromaDB 任一异常均降级返空 bundle, 不抛.
    """
    timeout = settings.knowledge_loader_timeout_secs
    try:
        return await asyncio.wait_for(
            _load_inner(db, ns_id, ns_slug, question),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.warning(
            "[loader] timeout after %ss ns_id=%s question=%.40s",
            timeout, ns_id, question,
        )
        return _empty_bundle()
    except Exception:
        log.exception(
            "[loader] unexpected failure ns_id=%s question=%.40s",
            ns_id, question,
        )
        return _empty_bundle()
