"""Stage 2 Task 7b — DB ↔ ChromaDB 双向一致性扫描.

K 场景 (docs/.../04-safety-and-bulk-ops.md §3.1): 离线场景 DB 与 ChromaDB
状态错配 (partial restore / failed migration / manual 删表). 本脚本只 read-only
扫描, 输出修复指令 (上层决定是 reembed 还是 backfill).

USAGE:
    cd backend
    python -m scripts.verify_db_chromadb_consistency

输出:
    JSON 报告 含
        db_only:       KE 在 DB 但 ChromaDB 没向量 (status=canonical & tier!=critical)
        chromadb_only: ChromaDB 有 doc 但 DB 没对应 KE (孤儿向量)
    + 修复指令 (打印到 stderr, 方便 grep)

Exit code: 0 一致 / 1 任一不一致.
"""

import asyncio
import dataclasses
import json
import logging
import sys
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.knowledge.knowledge_retriever import GLOBAL_NS_SLUG, make_doc_id
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  报告
# ════════════════════════════════════════════

@dataclass
class ConsistencyReport:
    db_only: list[int] = field(default_factory=list)
    chromadb_only: list[str] = field(default_factory=list)
    type_mismatch: list[tuple[int, str | None, str]] = field(default_factory=list)
    checked_canonical_ke: int = 0
    checked_chromadb_docs: int = 0


# ════════════════════════════════════════════
#  双向扫描
# ════════════════════════════════════════════

async def verify(db: AsyncSession) -> ConsistencyReport:
    """双向扫描. 仅检 status=canonical && tier!=critical (其他不应入 ChromaDB)."""
    from app.engine.registry import get_chroma_client

    client = get_chroma_client()
    ns_by_id = {ns.id: ns for ns in (await db.scalars(select(Namespace))).all()}

    canonical_kes = list((await db.scalars(
        select(KnowledgeEntry).where(
            KnowledgeEntry.status == "canonical",
            KnowledgeEntry.tier != "critical",
        )
    )).all())

    report = ConsistencyReport(checked_canonical_ke=len(canonical_kes))

    # ── A 向: DB KE → ChromaDB 应有 doc ──────────────────────
    ke_by_doc_id: dict[str, KnowledgeEntry] = {}
    for ke in canonical_kes:
        slug = (
            ns_by_id[ke.namespace_id].slug
            if ke.namespace_id in ns_by_id
            else GLOBAL_NS_SLUG
        )
        coll_name = f"ns_{slug}_knowledge"
        doc_id = make_doc_id(ke.id)
        ke_by_doc_id[doc_id] = ke
        try:
            coll = client.get_collection(coll_name)
            res = coll.get(ids=[doc_id])
            if not res.get("ids"):
                report.db_only.append(ke.id)
            else:
                # type_mismatch: DB KE 与 ChromaDB metadata.entry_type 不一致
                metas = res.get("metadatas") or []
                # ChromaDB metadata 值是宽 union (str|int|float|bool|SparseVector|
                # MetadataListValue|None), 我们只关心 str|None, 其他类型按 None 视作错配.
                chroma_type_raw = metas[0].get("entry_type") if metas else None
                chroma_type = chroma_type_raw if isinstance(chroma_type_raw, str) else None
                if chroma_type != ke.entry_type:
                    report.type_mismatch.append((ke.id, chroma_type, ke.entry_type))
        except Exception:  # noqa: BLE001 — 集合不存在 = doc 必缺, 计入 db_only
            report.db_only.append(ke.id)

    # ── B 向: ChromaDB doc → DB 应有 KE ──────────────────────
    for slug in {ns.slug for ns in ns_by_id.values()} | {GLOBAL_NS_SLUG}:
        coll_name = f"ns_{slug}_knowledge"
        try:
            coll = client.get_collection(coll_name)
            all_docs = coll.get()
            ids = all_docs.get("ids") or []
            report.checked_chromadb_docs += len(ids)
            for doc_id in ids:
                if doc_id not in ke_by_doc_id:
                    report.chromadb_only.append(doc_id)
        except Exception:  # noqa: BLE001 — 集合不存在 = 该 ns 没向量, 跳过即可
            continue

    return report


# ════════════════════════════════════════════
#  公开 API — dict 形态便于单测/脚本复用
# ════════════════════════════════════════════

async def diff_db_vs_chromadb(db: AsyncSession) -> dict:
    """对比 DB canonical KE 与 ChromaDB 向量, 返标准化 dict.

    Returns:
        {
            "db_only": [entry_id...],              # KE canonical 但 ChromaDB 缺向量
            "chromadb_only": [chroma_doc_id...],   # ChromaDB 有 doc 但 DB 没对应 canonical KE
            "type_mismatch": [(ke_id, chroma_type, db_type)...],  # entry_type 错配
        }

    与 ``verify(db) -> ConsistencyReport`` 共享内部扫描逻辑, 仅裁剪出三个差异字段
    (丢弃 checked_* 计数). 供单测 / 外部脚本以稳定契约消费.
    """
    report = await verify(db)
    return {
        "db_only": report.db_only,
        "chromadb_only": report.chromadb_only,
        "type_mismatch": report.type_mismatch,
    }


# Backward-compatible alias for callers still using the old name
diff_sqlite_vs_chromadb = diff_db_vs_chromadb


# ════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════

async def _amain() -> int:
    engine = create_async_engine(settings.metadata_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            report = await verify(db)
    finally:
        await engine.dispose()

    print(json.dumps(dataclasses.asdict(report), ensure_ascii=False, indent=2))

    if report.db_only:
        sys.stderr.write(
            f"\n[FIX] DB 有但 ChromaDB 缺 ({len(report.db_only)} 条):\n"
            f"      python -m scripts.backfill_knowledge_vectors\n"
        )
    if report.chromadb_only:
        sys.stderr.write(
            f"\n[FIX] ChromaDB 孤儿向量 ({len(report.chromadb_only)} 条):\n"
            f"      doc_ids={report.chromadb_only[:20]}\n"
            f"      手工 delete 或 DB 回填 KE.\n"
        )
    if report.type_mismatch:
        sys.stderr.write(
            f"\n[FIX] entry_type 错配 ({len(report.type_mismatch)} 条):\n"
            f"      samples={report.type_mismatch[:20]}\n"
            f"      跑 backfill_knowledge_vectors 重灌, 或 relabel 后再查.\n"
        )

    return 1 if (report.db_only or report.chromadb_only or report.type_mismatch) else 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(asyncio.run(_amain()))
