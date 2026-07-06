"""
知识向量检索层 — ns_{slug}_knowledge ChromaDB 集合的读写接口

职责:
- upsert: 录入/更新单条 KnowledgeEntry 向量
- delete: 删除单条向量
- _retrieve_layer3: 分层召回 → list[KnowledgeHit] (entry_type 隔离 + 配置驱动 k 值)
  Phase 4 Task 4.2 起改为模块私有 — 唯一公共调用方为 knowledge_loader.load_all_knowledge,
  agent_loop 主链路必须经 KnowledgeBundle 走单一入口, 不得直接召回.

命名约定: doc_id = f"ke_{entry_id}"，确保幂等 upsert
"""

import logging
from dataclasses import dataclass

from app.config import settings
from app.models.knowledge_entry import KnowledgeStatus
from app.schemas.knowledge_payload import TerminologyPayload

log = logging.getLogger(__name__)

# ── 全局命名空间 sentinel ──────────────────────
GLOBAL_NS_SLUG = "__global__"
"""全局知识集合 slug — namespace_id IS NULL 时落在 ns___global___knowledge."""


def make_doc_id(entry_id: int) -> str:
    """KnowledgeEntry → ChromaDB doc_id, 跨写入点统一约定."""
    return f"ke_{entry_id}"


# ── 检索结果结构 ──────────────────────────────
@dataclass
class KnowledgeHit:
    """_retrieve_layer3 单条召回结果.

    payload 字段说明:
    - 当前实现暂返 None — ChromaDB metadata 不存 payload (Stage 1 决策, 因 metadata
      value 须为 str/int/float/bool, dict 序列化后体积膨胀且失去结构语义).
    - Stage 4 agent loop tool 真用 payload 时, 由调用方按 entry_id 回查 SQLite.
    """
    entry_id: int               # 从 doc_id "ke_N" 解析
    content: str
    entry_type: str             # 从 metadata 读
    status: str                 # 从 metadata 读, 默认应为 canonical
    payload: dict | None        # 见 docstring
    distance: float             # ChromaDB raw distance — 越小越相似. 调用方需自行
                                # 转换为相似度 (e.g. 1 / (1 + distance)) 才能用作 score.
                                # 失败回 0.0.
    tier: str                   # 从 metadata 读 (critical / normal)
    namespace_id: int | None    # 从 metadata 读, -1 sentinel → None


# ════════════════════════════════════════════
#  公共工具
# ════════════════════════════════════════════


def parse_entry_payload(raw: str | None) -> dict | None:
    """KnowledgeEntry.payload (JSON Text 列) → dict | None.

    解析失败 (脏数据 / 旧格式 / 空串) → log.warning 后返 None,
    terminology 多向量分支会显式跳过.
    """
    if not raw:
        return None
    import json
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except (json.JSONDecodeError, TypeError) as e:
        log.warning(
            "[knowledge_retriever] payload 非合法 JSON, 跳过 (raw[:60]=%r): %s",
            raw[:60], e,
        )
        return None


# ════════════════════════════════════════════
#  写操作
# ════════════════════════════════════════════

def upsert_knowledge_entry(
    slug: str,
    entry_id: int,
    content: str,
    *,
    tier: str = "normal",
    namespace_id: int | None = None,
    entry_type: str = "terminology",
    status: KnowledgeStatus = "canonical",
    payload: dict | None = None,
) -> None:
    """
    将一条 KnowledgeEntry 写入 ns_{slug}_knowledge 向量集合

    Stage 1 升级:
    - 仅 status=canonical 才 upsert; proposed / superseded / rejected 跳过
    - metadata 增加 status 字段, 检索可过滤
    - critical tier 仍跳过 (由 _load_layer1_knowledge 直接 SQL 加载)
    - 后 4 个有默认值参数改 keyword-only (PEP 8 ≤5 positional + 调用安全)

    Phase 1b 升级:
    - terminology 走多向量 (term + synonyms 各占 1 vec, 共享 entry_id metadata),
      由 ``terminology_vectors`` 模块负责; 函数顶部分支跳出, 不走单向量路径
    - 状态退离 canonical → 多向量分支始终先 delete 旧向量再判断是否入库,
      保证 canonical → rejected / superseded 的退场路径同样清空 ke_{id}_*

    幂等: ChromaDB upsert — 相同 doc_id 覆盖更新.
    全局知识 (namespace_id=None) 写入 slug=__global__ 的集合.
    """
    # ════════════════════════════════════════════════════════
    #  terminology 多向量分支 (Phase 1b Task 1.4)
    # ════════════════════════════════════════════════════════
    if entry_type == "terminology":
        from app.knowledge.terminology_vectors import (
            delete_terminology_vectors,
            upsert_terminology_vectors,
        )

        # 始终先删旧向量 — 覆盖更新 / status 退离均一致
        delete_terminology_vectors(
            slug=slug, entry_id=entry_id, namespace_id=namespace_id,
        )
        if status != "canonical" or tier == "critical":
            log.debug(
                "[upsert] terminology skip ke=%d status=%s tier=%s",
                entry_id, status, tier,
            )
            return None
        if payload is None:
            log.warning(
                "[upsert] terminology ke=%d 无 payload, 多向量入库跳过", entry_id,
            )
            return None
        upsert_terminology_vectors(
            slug=slug,
            entry_id=entry_id,
            payload=TerminologyPayload(**payload),
            namespace_id=namespace_id,
        )
        return None

    # ── 既有单向量路径 (其他 4 类 entry_type) ───────────────

    # ── Phase 0 (2026-05-26): rule / route_hint 不再内嵌 HyQE 分支 ──
    # HQ 生成 + ChromaDB hq_* 子向量写入统一由 hq_writer.rewrite_hq_for_entry 管理.
    # 此处 rule / route_hint 走与 example 相同的单向量路径.

    if status != "canonical":
        log.debug(
            "knowledge upsert skip non-canonical id=ke_%d status=%s",
            entry_id, status,
        )
        return None

    if tier == "critical":
        log.debug("knowledge upsert skip critical id=ke_%d — loaded via SQL", entry_id)
        return None

    from app.engine.registry import get_knowledge_collection

    target_slug = slug if namespace_id is not None else GLOBAL_NS_SLUG
    coll = get_knowledge_collection(target_slug)
    doc_id = make_doc_id(entry_id)

    # Phase 2 P2.T13: example 类型用 question + nl_paraphrases 拼接作为索引内容
    # example: build index from question_pattern (new) or question (old) + paraphrases
    index_content = content
    if entry_type == "example" and payload:
        from app.knowledge.knowledge_content import build_example_content
        index_content = build_example_content(payload)

    coll.upsert(
        ids=[doc_id],
        documents=[index_content],
        metadatas=[{
            "tier": tier,
            "entry_type": entry_type,
            "status": status,
            "entry_id": entry_id,
            "namespace_id": namespace_id if namespace_id is not None else -1,
        }],
    )
    log.debug(
        "knowledge upsert ok id=%s slug=%s tier=%s status=%s",
        doc_id, target_slug, tier, status,
    )
    return None


def rewrite_hq_subvectors(
    slug: str,
    entry_id: int,
    *,
    entry_type: str,
    tier: str,
    namespace_id: int | None,
    content: str,
    hq_list: list[str],
) -> None:
    """重写 entry 的 ChromaDB 主+hq 子向量.

    步骤:
      1. delete by entry_id (清主 + 全部 hq_*)
      2. upsert 主 doc_id (content) + 新 hq_* (hq_list)

    复用现有 doc_id pattern. 与 upsert_knowledge_entry 行为一致,
    仅入参显式传 hq_list (不调 LLM).
    """
    from app.engine.registry import get_knowledge_collection

    target_slug = slug if namespace_id is not None else GLOBAL_NS_SLUG
    coll = get_knowledge_collection(target_slug)

    try:
        coll.delete(where={"entry_id": entry_id})
    except Exception as e:
        log.warning("[hq_rewrite] delete old fail ke=%d: %s", entry_id, e)

    doc_id = make_doc_id(entry_id)
    base_meta = {
        "tier": tier,
        "entry_type": entry_type,
        "status": "canonical",
        "entry_id": entry_id,
        "namespace_id": namespace_id if namespace_id is not None else -1,
    }
    ids = [doc_id] + [f"{doc_id}_hq_{i}" for i in range(len(hq_list))]
    documents = [content] + hq_list
    metadatas = [
        {**base_meta, "is_hypothetical": False, "hq_index": -1},
        *[
            {**base_meta, "is_hypothetical": True, "hq_index": i}
            for i in range(len(hq_list))
        ],
    ]
    coll.upsert(ids=ids, documents=documents, metadatas=metadatas)  # type: ignore[arg-type]
    log.debug("[hq_rewrite] entry=%d hq_count=%d", entry_id, len(hq_list))


def delete_knowledge_entry(
    slug: str,
    entry_id: int,
    namespace_id: int | None = None,
    *,
    entry_type: str | None = None,
) -> None:
    """从向量集合中删除一条 KnowledgeEntry.

    Phase 1b 升级:
        entry_type="terminology" → 多向量删除 (where entry_id 扫净 ke_{id}_*)
        entry_type=None / 其他 → 单向量删除 ke_{entry_id} (既有路径)

    异常分级 (让上层 try/except 真正记账):
        - 集合不存在 → 幂等场景 (没人写入过), 静默通过
        - 集合存在但 coll.delete 抛错 → 真 ChromaDB 故障, 抛 RuntimeError
          让上层 (bulk_guard / api/knowledge.py) 决定补救路径.

    全局知识 (namespace_id IS NULL) 从 __global__ 集合删除.
    """
    # ── terminology 多向量分支 (Phase 1b Task 1.4) ─────────
    if entry_type == "terminology":
        from app.knowledge.terminology_vectors import delete_terminology_vectors

        delete_terminology_vectors(
            slug=slug, entry_id=entry_id, namespace_id=namespace_id,
        )
        return

    # ── Stage 2 抓手 A: rule / route_hint 多向量分支 (HyQE) ──
    if entry_type in {"rule", "route_hint"}:
        from app.engine.embedding import get_embedding_function
        from app.engine.registry import get_chroma_client

        target_slug = slug if namespace_id is not None else GLOBAL_NS_SLUG
        coll_name = f"ns_{target_slug}_knowledge"
        client = get_chroma_client()
        try:
            coll = client.get_collection(
                coll_name,
                embedding_function=get_embedding_function(),  # type: ignore[arg-type]
            )
        except Exception:
            log.debug("knowledge delete hyqe skip — collection missing slug=%s", target_slug)
            return
        try:
            coll.delete(where={"entry_id": entry_id})
        except Exception as e:
            log.warning("knowledge delete hyqe fail ke=%d: %s", entry_id, e)
            raise
        return

    # ── 既有单向量路径 (其他 entry_type / None) ────────────
    from app.engine.embedding import get_embedding_function
    from app.engine.registry import get_chroma_client

    target_slug = slug if namespace_id is not None else GLOBAL_NS_SLUG
    coll_name = f"ns_{target_slug}_knowledge"
    client = get_chroma_client()

    # ── Stage 1: 集合不存在 → 幂等通过 (没人写入过, 删什么) ─────
    try:
        coll = client.get_collection(
            coll_name,
            embedding_function=get_embedding_function(),  # type: ignore[arg-type]
        )
    except Exception as exc:
        log.warning(
            "knowledge delete skip — collection missing coll=%s err=%s",
            coll_name, exc,
        )
        return

    # ── Stage 2: 集合存在但 delete 失败 → 真故障, 让上层 try/except 兜 ──
    coll.delete(ids=[make_doc_id(entry_id)])
    log.debug("knowledge delete ok id=%s coll=%s", make_doc_id(entry_id), coll_name)


# ════════════════════════════════════════════
#  分层召回 — KnowledgeHit + entry_types 隔离 + 配置驱动 k
# ════════════════════════════════════════════

def _build_where(
    *,
    tier: str | None,
    status: str,
    entry_types: list[str] | None,
) -> dict:
    """构造 ChromaDB where 子句.

    多条件必须用 $and 包装 (ChromaDB 单条件直传 dict, 多条件须显式 $and).
    """
    conds: list[dict] = [{"status": {"$eq": status}}]
    if tier is not None:
        conds.append({"tier": {"$eq": tier}})
    if entry_types:
        conds.append({"entry_type": {"$in": list(entry_types)}})

    if len(conds) == 1:
        return conds[0]
    return {"$and": conds}


def _hit_from_query(
    res: dict, doc_idx: int,
) -> KnowledgeHit | None:
    """ChromaDB query 单行结果 → KnowledgeHit, 任一关键字段缺失则返 None.

    entry_id 缺失或为 0 → 返 None (防御性: 0 是合法 sentinel, 入 seen_ids 会污染去重).
    """
    def _col(key: str) -> list:
        return (res.get(key) or [[]])[0]

    docs, metas, dists = _col("documents"), _col("metadatas"), _col("distances")
    if doc_idx >= len(docs) or not docs[doc_idx]:
        return None

    meta = metas[doc_idx] if doc_idx < len(metas) else {}
    entry_id_raw = meta.get("entry_id")
    if not entry_id_raw:
        # metadata 缺 entry_id 的伪 doc, 跳过 (污染 seen_ids 防御)
        return None
    raw_ns = meta.get("namespace_id", -1)
    return KnowledgeHit(
        entry_id=int(entry_id_raw),
        content=docs[doc_idx],
        entry_type=str(meta.get("entry_type", "")),
        status=str(meta.get("status", "")),
        payload=None,  # 见 KnowledgeHit.payload docstring
        distance=float(dists[doc_idx]) if doc_idx < len(dists) else 0.0,
        tier=str(meta.get("tier", "")),
        namespace_id=None if raw_ns == -1 else int(raw_ns),
    )


def _retrieve_layer3(
    slug: str,
    question: str,
    *,
    entry_types: list[str] | None = None,
    status: str = "canonical",
    k_critical: int | None = None,
    k_normal: int | None = None,
    query_embedding: list[float] | None = None,
) -> list[KnowledgeHit]:
    """
    分层召回知识 (返回结构化 KnowledgeHit) — 模块私有 (Phase 4 Task 4.2).

    主链路调用方必须走 ``app.knowledge.knowledge_loader.load_all_knowledge``,
    不允许直接调用本函数; 单元测试与 knowledge_loader 是仅有的合法导入方.

    Layer 1 — ns_{slug}_knowledge critical tier (命名空间专属, 优先)
    Layer 2 — ns_{slug}_knowledge normal tier   (命名空间专属, 次要)
    Layer 3 — ns___global___knowledge          (全局知识兜底)

    参数:
        entry_types: None=不过滤; 非空则按 5 类宪章隔离
            (terminology / instance_alias / rule / example / route_hint)
        status:      防御性二次过滤 (默认 canonical, 与 upsert 入闸保持一致)
        k_critical:  None → settings.knowledge_retrieve_critical_n
        k_normal:    None → settings.knowledge_retrieve_normal_n
        query_embedding: 预计算的 embedding 向量, 非 None 时跳过内部 embedding 计算

    返回: 按 critical → ns_normal → global 顺序去重后的 KnowledgeHit 列表.
    """
    from app.engine.embedding import get_embedding_function
    from app.engine.registry import get_chroma_client

    k_crit = k_critical if k_critical is not None else settings.knowledge_retrieve_critical_n
    k_norm = k_normal if k_normal is not None else settings.knowledge_retrieve_normal_n

    client = get_chroma_client()
    ef = get_embedding_function()
    seen_ids: set[int] = set()
    result: list[KnowledgeHit] = []

    # ── 预计算 embedding: 外部传入则复用, 否则内部计算一次 ──
    query_vec = query_embedding if query_embedding is not None else ef([question])[0]

    def _query_layer(coll_name: str, tier: str | None, n: int) -> list[KnowledgeHit]:
        try:
            coll = client.get_collection(
                coll_name, embedding_function=ef,  # type: ignore[arg-type]
            )
            count = coll.count() or 1
            res = coll.query(
                query_embeddings=[query_vec],  # type: ignore[arg-type]
                n_results=min(n, count),
                where=_build_where(tier=tier, status=status, entry_types=entry_types),
            )
            docs = (res.get("documents") or [[]])[0]  # type: ignore[union-attr]
            hits: list[KnowledgeHit] = []
            for i in range(len(docs)):
                hit = _hit_from_query(res, i)  # type: ignore[arg-type]
                if hit is not None:
                    hits.append(hit)
            return hits
        except Exception as exc:
            log.warning("knowledge retrieve failed coll=%s err=%s", coll_name, exc, exc_info=True)
            return []

    ns_coll = f"ns_{slug}_knowledge"
    global_coll = f"ns_{GLOBAL_NS_SLUG}_knowledge"

    # ── 三层召回数据驱动: (collection, tier_filter, k) ──
    layers = [
        (ns_coll,     "critical", k_crit),  # Layer 1: namespace critical
        (ns_coll,     "normal",   k_norm),  # Layer 2: namespace normal
        (global_coll, None,       k_norm),  # Layer 3: global (tier 不限)
    ]
    for coll_name, tier, n in layers:
        for h in _query_layer(coll_name, tier, n):
            if h.entry_id not in seen_ids:
                seen_ids.add(h.entry_id)
                result.append(h)

    log.debug(
        "knowledge retrieve slug=%s question=%.30s entry_types=%s results=%d",
        slug, question, entry_types, len(result),
    )
    return result
