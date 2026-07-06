"""Stage 2 Task 7a — embedding 模型切换后 ChromaDB 原地重灌.

E 场景 (docs/.../04-safety-and-bulk-ops.md §3.1): 切 embedding 模型后,
ChromaDB 旧向量与新模型不一致, 检索失准. 此脚本仅 ChromaDB delete + upsert,
不动 SQLite (KE 是真相源).

USAGE:
    cd backend
    python -m scripts.reembed_after_model_change --dry-run             # 仅统计 (默认)
    python -m scripts.reembed_after_model_change --dry-run=false       # 真重灌
    python -m scripts.reembed_after_model_change --dry-run=false --from-id 1000   # 续跑

环境变量:
    IS_MIGRATION_DRY_RUN — --dry-run 默认值
    IS_METADATA_DB_URL   — 数据库连接

Exit code: 0 全成功 / 1 任一 KE upsert 失败.

部署: 仅在 IS_EMBEDDING_MODEL 切换后人工跑一次. 跑前必须 backup_before_migration.sh.
"""

import argparse
import asyncio
import dataclasses
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.knowledge.knowledge_retriever import (
    delete_knowledge_entry,
    upsert_knowledge_entry,
)
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  报告
# ════════════════════════════════════════════

@dataclass
class ReembedReport:
    candidates: int = 0
    reembedded: int = 0
    skipped: int = 0       # tier=critical 或 status≠canonical (Stage 2 防御)
    failed_ids: list[int] = field(default_factory=list)
    from_id: int | None = None


# ════════════════════════════════════════════
#  主体逻辑
# ════════════════════════════════════════════

async def reembed(
    db: AsyncSession,
    *,
    dry_run: bool = True,
    from_id: int | None = None,
) -> ReembedReport:
    """全量遍历 status=canonical && tier!=critical KE, 每条 ChromaDB delete + upsert.

    failed_ids 收集失败条目 (delete 失败也计 failed, 因 stale embedding 不删=污染),
    供二次跑 --from-id N 续跑.
    """
    stmt = select(KnowledgeEntry).where(
        KnowledgeEntry.status == "canonical",
        KnowledgeEntry.tier != "critical",
    )
    if from_id is not None:
        stmt = stmt.where(KnowledgeEntry.id >= from_id)
    stmt = stmt.order_by(KnowledgeEntry.id)

    candidates = list((await db.scalars(stmt)).all())
    ns_by_id = {ns.id: ns for ns in (await db.scalars(select(Namespace))).all()}

    report = ReembedReport(candidates=len(candidates), from_id=from_id)
    if dry_run:
        log.info(
            "[reembed] dry-run candidates=%d from_id=%s",
            report.candidates, from_id,
        )
        return report

    for ke in candidates:
        slug = (
            ns_by_id[ke.namespace_id].slug
            if ke.namespace_id in ns_by_id
            else "__global__"
        )
        try:
            delete_knowledge_entry(
                slug=slug, entry_id=ke.id, namespace_id=ke.namespace_id,
            )
            upsert_knowledge_entry(
                slug=slug, entry_id=ke.id, content=ke.content,
                tier=ke.tier, namespace_id=ke.namespace_id,
                entry_type=ke.entry_type, status=ke.status,
            )
            report.reembedded += 1
            if report.reembedded % 100 == 0:
                log.info(
                    "[reembed] progress %d/%d",
                    report.reembedded, report.candidates,
                )
        except Exception as exc:  # noqa: BLE001 — 隔离失败, 不阻整体
            report.failed_ids.append(ke.id)
            log.warning("[reembed] failed entry_id=%d: %s", ke.id, exc)

    log.info(
        "[reembed] done reembedded=%d failed=%d (sample=%s)",
        report.reembedded, len(report.failed_ids), report.failed_ids[:20],
    )
    return report


# ════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════

async def _amain() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 2 ChromaDB 原地重灌 (embedding 切换后)",
    )
    parser.add_argument(
        "--dry-run",
        nargs="?",
        const=True,
        type=lambda x: str(x).lower() not in ("false", "0", "no"),
        default=True,
        help="dry-run 默认开, 传 --dry-run=false 真执行",
    )
    parser.add_argument(
        "--from-id", type=int, default=None,
        help="从指定 KE.id 续跑 (用于失败后恢复)",
    )
    args = parser.parse_args()

    log.info(
        "[reembed] starting (dry_run=%s, from_id=%s)",
        args.dry_run, args.from_id,
    )

    engine = create_async_engine(settings.metadata_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            report = await reembed(db, dry_run=args.dry_run, from_id=args.from_id)
    finally:
        await engine.dispose()

    print(json.dumps(
        {"dry_run": args.dry_run, **dataclasses.asdict(report)},
        ensure_ascii=False, indent=2,
    ))
    return 1 if report.failed_ids else 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(asyncio.run(_amain()))
