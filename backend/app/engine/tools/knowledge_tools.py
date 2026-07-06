"""
知识层 agent tool — lookup_knowledge / save_knowledge

职责:
- lookup_knowledge: 读路径, 向量检索知识 → JSON-safe dict 列表
                    (返 content + entry_type + status + tier + distance, agent 用于决策)
- save_knowledge:   写路径, 写 KnowledgeEntry(source=agent_learn, status=proposed) 入待审池
                    proposed 不进 RAG (upsert 路径 skip 非 canonical), 由人工审核通过后再 upsert
"""
from __future__ import annotations

import asyncio
import json
import logging

from langfuse import observe
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.knowledge.intake import VALID_ENTRY_TYPES
from app.knowledge.knowledge_loader import load_all_knowledge
from app.knowledge.knowledge_retriever import KnowledgeHit, _retrieve_layer3
from app.knowledge.recall_payload_compactor import compact_payload_for_recall
from app.models import KnowledgeEntry

from ._mongo_helpers import record_span_io as _record_span_io

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  读路径 — agent 看 content 决策检索结果是否相关
# ════════════════════════════════════════════

@observe(name="tool.lookup_knowledge")
async def lookup_knowledge(
    *, db: AsyncSession, namespace_id: int | None, ns_slug: str,
    query: str, types: list[str] | None = None, k: int | None = None,
) -> list[dict]:
    """Agent 检索 5 类知识. 返 JSON-safe dict 列表.

    types=None 时召回全类型共 k 条; types 非空时每个 type 各召回 k 条, 并发检索后合并去重.
    k=None 走 settings.knowledge_retrieve_default_k.

    性能: embedding 预计算一次, 多 type 并发检索 (asyncio.gather + to_thread).
    """
    from app.engine.embedding import get_embedding_function

    ns = ns_slug if ns_slug else "__global__"
    cap = k if k is not None else settings.knowledge_retrieve_default_k

    # 预计算 embedding (一次 DashScope HTTP), 所有 type 复用
    ef = get_embedding_function()
    query_vec: list[float] = await asyncio.to_thread(lambda: ef([query])[0])  # type: ignore[assignment]

    if types:
        # 动态 k: type 越多每个 type 取越少, 避免召回结果过多
        if k is not None:
            per_type_k = k
        elif len(types) == 1:
            per_type_k = cap
        elif len(types) <= 3:
            per_type_k = 3
        else:
            per_type_k = 2
        # 每个 type 独立检索 per_type_k 条, 并发执行, 合并去重
        async def _retrieve_one_type(entry_type: str) -> list[KnowledgeHit]:
            return await asyncio.to_thread(
                _retrieve_layer3, ns, query,
                entry_types=[entry_type],
                k_normal=per_type_k,
                query_embedding=query_vec,
            )

        results_per_type = await asyncio.gather(
            *[_retrieve_one_type(t) for t in types]
        )
        seen: set[int] = set()
        all_hits: list[KnowledgeHit] = []
        for type_hits in results_per_type:
            for h in type_hits:
                if h.entry_id not in seen:
                    seen.add(h.entry_id)
                    all_hits.append(h)
        hits = all_hits
    else:
        # 全类型混合检索
        raw_hits: list[KnowledgeHit] = await asyncio.to_thread(
            _retrieve_layer3, ns, query,
            entry_types=None,
            k_normal=cap,
            query_embedding=query_vec,
        )
        seen = set()
        hits = []
        for h in raw_hits:
            if h.entry_id not in seen:
                seen.add(h.entry_id)
                hits.append(h)
        hits = hits[:cap]

    # ── 批量回查 payload (KnowledgeHit.payload 在 _retrieve_layer3 层是 None,
    #    ChromaDB metadata 不存 dict, 此处按 entry_id SELECT IN 注入) ──
    if hits:
        from sqlalchemy import select as _sel
        from app.models import KnowledgeEntry as _KE
        entry_ids = [h.entry_id for h in hits]
        stmt = _sel(_KE.id, _KE.payload).where(_KE.id.in_(entry_ids))
        rows = (await db.execute(stmt)).all()
        payload_map: dict[int, dict] = {}
        for row in rows:
            try:
                payload_map[row.id] = json.loads(row.payload) if row.payload else {}
            except json.JSONDecodeError:
                log.warning(
                    "lookup_knowledge payload JSON parse fail entry_id=%s payload=%r",
                    row.id, row.payload,
                )
                payload_map[row.id] = {}
    else:
        payload_map = {}

    result = [
        {
            "content": h.content,
            "entry_type": h.entry_type,
            "status": h.status,
            "distance": h.distance,
            "tier": h.tier,
            "payload": compact_payload_for_recall(
                h.entry_type, payload_map.get(h.entry_id, {}),
            ),
        }
        for h in hits
    ]
    _record_span_io(
        input={"ns_slug": ns_slug, "query": query, "types": types, "k": k},
        output={"hit_count": len(result)},
    )

    # ── Stage 2 抓手 B: 反馈环入栈 (复用项目现有 trace_id_var contextvar) ──
    try:
        from app.engine.recall_window import window_record
        from app.logging_config import trace_id_var
        tid = trace_id_var.get(None)
        if tid and tid != "-":
            window_record(tid, [h.entry_id for h in hits])
    except Exception as e:  # noqa: BLE001
        log.warning("[lookup_knowledge] recall_window 写失败 (best-effort): %s", e)

    return result


# ════════════════════════════════════════════
#  写路径 — 入待审池 (proposed), 通过审核后才进 RAG
# ════════════════════════════════════════════

@observe(name="tool.save_knowledge")
async def save_knowledge(
    *, db: AsyncSession, namespace_id: int | None, ns_slug: str,
    sse_emit,
    entry_type: str, content: str, payload: dict,
    evidence: dict, tier: str = "normal",
) -> dict:
    """Agent 自学 → 写待审池 (stage to session, caller commits).

    Tool 只 db.add + db.flush 拿 id, 不 commit — 事务边界留给调用方
    (agent loop 或 API endpoint 可多 tool 原子提交).

    entry_type 走 5 类宪章校验, 拼错抛 ValueError.
    source 固定 settings.agent_learn_source ('agent_learn'), status=proposed.
    proposed 不进 RAG (由 upsert_knowledge_entry 的 status != canonical 自动 skip,
    这里不调 upsert, 留给审核通过后走 approve 端点 upsert).

    Phase 1c Task 1.5 — terminology 走统一闸门:
        upsert_terminology_with_validation 做 schema/db_type/唯一键三重校验,
        失败返 {"success": False, "reason": "validation_failed_or_conflict_pending"}
        让 agent loop 上层感知, 不静默吞错.
    """
    if entry_type not in VALID_ENTRY_TYPES:
        raise ValueError(
            f"entry_type {entry_type!r} 不在 6 类宪章 {sorted(VALID_ENTRY_TYPES)}"
        )

    # ── Phase 1c: terminology 走统一闸门 ──
    if entry_type == "terminology":
        if namespace_id is None:
            log.warning("agent save_knowledge terminology 需要 namespace_id ns=%s", ns_slug)
            return {"success": False, "reason": "terminology_requires_namespace_id"}
        from app.knowledge.terminology_intake import upsert_terminology_with_validation
        # cast: settings.agent_learn_source 类型是 str, 闸门要求 Source Literal,
        # 二者值同源 ('agent_learn'), pyright 类型收窄用 cast 显式表达不变式.
        from typing import cast as _cast
        from app.models.knowledge_entry import KnowledgeSource as _IntakeSource
        ke = await upsert_terminology_with_validation(
            db, ns_id=namespace_id, payload_dict=payload,
            source=_cast(_IntakeSource, settings.agent_learn_source),
            raw_input=content, evidence=evidence,
        )
        if ke is None:
            log.warning(
                "agent save_knowledge terminology rejected ns=%s payload_term=%r",
                ns_slug, (payload or {}).get("term"),
            )
            _record_span_io(
                input={"entry_type": entry_type, "ns_slug": ns_slug, "tier": tier,
                       "content_preview": content[:100]},
                output={"success": False, "reason": "validation_failed_or_conflict_pending"},
            )
            return {"success": False, "reason": "validation_failed_or_conflict_pending"}
        log.info("agent save_knowledge terminology ns=%s id=%s", ns_slug, ke.id)
        _record_span_io(
            input={"entry_type": entry_type, "ns_slug": ns_slug, "tier": tier,
                   "content_preview": content[:100]},
            output={"entry_id": ke.id, "status": ke.status},
        )
        # ── P0-3 emit knowledge_proposed ──
        await sse_emit({"event": "knowledge_proposed", "data": {
            "entry_id": ke.id,
            "entry_type": entry_type,
            "preview": content[:80],
        }})
        return {"entry_id": ke.id, "status": ke.status}

    # ── instance_alias 走 schema 校验, 不进自动机 ──
    if entry_type == "instance_alias":
        from app.knowledge.instance_alias_intake import (
            InstanceAliasValidationError,
            validate_instance_alias_payload,
        )
        try:
            validated = validate_instance_alias_payload(payload)
        except InstanceAliasValidationError as e:
            log.warning(
                "agent save_knowledge instance_alias rejected ns=%s reason=%r payload=%r",
                ns_slug, str(e), payload,
            )
            _record_span_io(
                input={"entry_type": entry_type, "ns_slug": ns_slug, "tier": tier,
                       "content_preview": content[:100]},
                output={"success": False, "reason": "validation_failed", "detail": str(e)},
            )
            return {"success": False, "reason": "validation_failed", "detail": str(e)}

        ke = KnowledgeEntry(
            namespace_id=namespace_id, entry_type="instance_alias",
            content=content, tier=tier, status="proposed",
            source=settings.agent_learn_source,
            payload=json.dumps(validated, ensure_ascii=False),
            evidence_json=json.dumps(evidence, ensure_ascii=False),
        )
        db.add(ke)
        await db.flush()
        log.info(
            "agent save_knowledge instance_alias ns=%s id=%s alias=%r",
            ns_slug, ke.id, validated["alias"],
        )
        _record_span_io(
            input={"entry_type": entry_type, "ns_slug": ns_slug, "tier": tier,
                   "content_preview": content[:100]},
            output={"entry_id": ke.id, "status": "proposed"},
        )
        await sse_emit({"event": "knowledge_proposed", "data": {
            "entry_id": ke.id,
            "entry_type": "instance_alias",
            "preview": content[:80],
        }})
        return {"entry_id": ke.id, "status": "proposed"}

    # ── rule / route_hint / example 走 parse_payload 闸门 (Phase 1) ──
    # 闸门只校验不重写 payload — 防 Pydantic 默认值 (cost_strategy='default'/
    # rule_kind='business_constraint') 在 LLM 漏字段时被静默注入,
    # 掩盖 Phase 2 trace_extractor code 抽路径的真实值.
    if entry_type in {"rule", "route_hint", "example"}:
        from pydantic import ValidationError
        from app.schemas.knowledge_payload import parse_payload
        try:
            parse_payload(entry_type, payload)  # 仅校验, 不 model_dump 覆盖
        except ValidationError as e:
            log.warning(
                "agent save_knowledge %s rejected ns=%s schema_error=%s",
                entry_type, ns_slug, e.errors()[:3],
            )
            _record_span_io(
                input={"entry_type": entry_type, "ns_slug": ns_slug,
                       "tier": tier, "content_preview": content[:100]},
                output={"success": False, "reason": "validation_failed",
                        "detail": str(e.errors()[:3])},
            )
            return {"success": False, "reason": "validation_failed",
                    "detail": str(e.errors()[:3])}

    # ── 其他 4 类 entry_type — 既有路径不动 ──
    ke = KnowledgeEntry(
        namespace_id=namespace_id, entry_type=entry_type, content=content,
        tier=tier, status="proposed", source=settings.agent_learn_source,
        payload=json.dumps(payload, ensure_ascii=False),
        evidence_json=json.dumps(evidence, ensure_ascii=False),
    )
    db.add(ke)
    await db.flush()  # 仅 flush 拿 autoincrement id, 不 commit (调用方管事务)
    log.info("agent save_knowledge ns=%s type=%s id=%s", ns_slug, entry_type, ke.id)

    # ── Stage 2 抓手 D: A-MEM 入库即演化 ──
    if settings.amem_enabled and entry_type in {"rule", "route_hint", "example"}:
        try:
            from datetime import datetime as _dt

            from app.knowledge.knowledge_retriever import _retrieve_layer3
            from app.knowledge.relations import detect_relations

            neighbors_hits = await asyncio.to_thread(
                _retrieve_layer3, ns_slug, content,
                entry_types=[entry_type],
                k_normal=settings.amem_neighbor_k,
            )
            # 排除自身 (虽然 status=proposed 不应在 RAG, 防御性)
            neighbors = [
                {"id": h.entry_id, "content": h.content}
                for h in neighbors_hits if h.entry_id != ke.id
            ]
            related = await asyncio.to_thread(detect_relations, content, neighbors)
            non_indep = [
                {
                    "related_entry_id": r.related_entry_id,
                    "relation": r.relation,
                    "llm_reason": r.llm_reason,
                    "detected_at": _dt.now().isoformat(),
                }
                for r in related if r.relation != "independent"
            ]
            if non_indep:
                ke.related_entry_ids_json = json.dumps(non_indep, ensure_ascii=False)
        except Exception as e:
            # 演化失败退化为普通 proposed (现状), 不阻业务
            log.warning("[save_knowledge] amem fail ke=%d: %s", ke.id, e)

    _record_span_io(
        input={"entry_type": entry_type, "ns_slug": ns_slug, "tier": tier,
                "content_preview": content[:100]},
        output={"entry_id": ke.id, "status": "proposed"},
    )
    # ── P0-3 emit knowledge_proposed ──
    await sse_emit({"event": "knowledge_proposed", "data": {
        "entry_id": ke.id,
        "entry_type": entry_type,
        "preview": content[:80],
    }})
    return {"entry_id": ke.id, "status": "proposed"}
