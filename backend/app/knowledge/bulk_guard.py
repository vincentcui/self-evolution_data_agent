"""破坏性批量操作统一入口 — 实现"分类保护宪章" 6 条.

参考 `docs/todos/knowledge-unification-and-agent-loop/04-safety-and-bulk-ops.md`.

宪章六条:
    §1 默认 dry_run=True            — preview 永远无副作用
    §2 source-aware 范围            — filter by source / entry_type / repo_id / namespace_id
    §3 人类编辑兜底                  — audit_log 中 actor_id != NULL 的 entry 永不批删
    §4 必写 audit_log                — 真删后落 bulk_delete 主记录
    §5 影响数报告                    — by_source / by_entry_type / sample_ids
    §6 ChromaDB 同步                 — execute() 真删后 best-effort 同步删 ChromaDB,
                                       失败入 chromadb_failed_ids 不阻业务 (Stage 2 兑现)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TypedDict

from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.audit import write_audit
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry

log = logging.getLogger(__name__)


_SAMPLE_LIMIT = 5
"""sample_ids 报告截断阈值 (UI 友好显示, 非业务行为阈值, 故无需 IS_ env var)."""


# ─────────────────────────────────────────────────────────────────
# 范围过滤 typing 契约
# ─────────────────────────────────────────────────────────────────


class ScopeFilter(TypedDict, total=False):
    """BulkOperationGuard.scope_filter 的 typed 契约 (宪章 §2 source-aware).

    所有 key 可选, AND 语义合并. 调用方传 ``{"sources": [...]}`` 拼写错时 IDE 直接飘红.

    Stage 2 Task 6: ``repo_id`` 与 ``repo_id_is_null`` 互斥, 同时给 ``repo_id`` 优先;
    后者仅在 True 时生效, 等价于 ``KE.repo_id IS NULL`` (用于孤儿条目清理).
    """

    source: list[str]
    entry_type: list[str]
    repo_id: int
    repo_id_is_null: bool
    namespace_id: int


# ─────────────────────────────────────────────────────────────────
# 报告对象 — execute / preview 共用
# ─────────────────────────────────────────────────────────────────


@dataclass
class BulkOpReport:
    """批量操作影响数报告 (宪章 §5 + §6)."""

    op_name: str
    scope_filter: ScopeFilter
    affected_count: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    by_entry_type: dict[str, int] = field(default_factory=dict)
    preserved_audited_count: int = 0
    sample_ids: list[int] = field(default_factory=list)
    audit_log_id: int | None = None

    # ── §6 ChromaDB 同步记账 (Stage 2 兑现) ─────────────────
    chromadb_deleted_count: int = 0
    """ChromaDB delete 实际成功条数 (best-effort, 等于 affected_count - len(failed_ids))."""
    chromadb_failed_ids: list[int] = field(default_factory=list)
    """ChromaDB delete 失败的 entry_id 列表; SQLite 已删, 等待 Stage 4 一致性脚本补救."""


# ─────────────────────────────────────────────────────────────────
# Guard 主体
# ─────────────────────────────────────────────────────────────────


class BulkOperationGuard:
    """所有破坏性批量操作的统一入口.

    用法:
        guard = BulkOperationGuard(
            op_name="git_reparse_clean",
            scope_filter={"source": ["code_extract"], "repo_id": 7},
            dry_run=False, actor_id=user.id, reason="re-parse repo #7",
        )
        report = await guard.execute(db, slug="orders")
    """

    def __init__(
        self,
        op_name: str,
        scope_filter: ScopeFilter,
        dry_run: bool = True,
        actor_id: int | None = None,
        reason: str = "",
        full_purge: bool = False,
    ) -> None:
        self.op_name = op_name
        self.scope_filter = scope_filter
        self.dry_run = dry_run
        self.actor_id = actor_id
        self.reason = reason
        self.full_purge = full_purge
        """整域销毁模式 (如 namespace 删除): 跳过人类编辑保护 (反正后续 CASCADE 全删),
        affected 统计反映真实删除规模; audit 锚点 entry_id=NULL 避免被 CASCADE 自毁."""

    # ── 内部: 范围与保护集 ─────────────────────────────────────

    def _build_clauses(self) -> list[ColumnElement[bool]]:
        """构造 SQLAlchemy where clauses (宪章 §2 source-aware)."""
        clauses: list[ColumnElement[bool]] = []
        sf = self.scope_filter
        if "source" in sf:
            clauses.append(KnowledgeEntry.source.in_(sf["source"]))
        if "entry_type" in sf:
            clauses.append(KnowledgeEntry.entry_type.in_(sf["entry_type"]))
        # Stage 2 Task 6: repo_id 字段三态过滤 — 整数值 / IS NULL / 不过滤
        if "repo_id" in sf:
            clauses.append(KnowledgeEntry.repo_id == sf["repo_id"])
        elif sf.get("repo_id_is_null"):
            clauses.append(KnowledgeEntry.repo_id.is_(None))
        if "namespace_id" in sf:
            clauses.append(KnowledgeEntry.namespace_id == sf["namespace_id"])
        return clauses

    async def _human_edited_ids(self, db: AsyncSession) -> set[int]:
        """audit_log 中 actor_id != NULL 的 edit/approve 表示人类编辑过 (宪章 §3)."""
        rows = await db.scalars(
            select(KnowledgeAuditLog.entry_id)
            .where(
                KnowledgeAuditLog.actor_id.isnot(None),
                KnowledgeAuditLog.action.in_(("approve", "edit")),
            )
            .distinct()
        )
        return {eid for eid in rows.all() if eid is not None}

    # ── 报告计算 ───────────────────────────────────────────────

    async def _candidates_and_protected(
        self, db: AsyncSession
    ) -> tuple[list[KnowledgeEntry], set[int]]:
        clauses = self._build_clauses()
        candidates = list(
            (await db.scalars(select(KnowledgeEntry).where(and_(*clauses)))).all()
        )
        protected = await self._human_edited_ids(db)
        return candidates, protected

    def _build_report(
        self, candidates: list[KnowledgeEntry], protected: set[int]
    ) -> tuple[BulkOpReport, list[KnowledgeEntry]]:
        # full_purge: 整域销毁, 不排除 protected (后续 CASCADE 全删, 保护逻辑无意义)
        if self.full_purge:
            affected = candidates
        else:
            affected = [e for e in candidates if e.id not in protected]
        report = BulkOpReport(
            op_name=self.op_name,
            scope_filter=self.scope_filter,
            affected_count=len(affected),
            preserved_audited_count=len(candidates) - len(affected),
            sample_ids=[e.id for e in affected[:_SAMPLE_LIMIT]],
        )
        for e in affected:
            report.by_source[e.source] = report.by_source.get(e.source, 0) + 1
            report.by_entry_type[e.entry_type] = report.by_entry_type.get(e.entry_type, 0) + 1
        return report, affected

    # ── 公开 API ───────────────────────────────────────────────

    async def preview(self, db: AsyncSession) -> BulkOpReport:
        """无副作用预览 (宪章 §1)."""
        candidates, protected = await self._candidates_and_protected(db)
        report, _ = self._build_report(candidates, protected)
        return report

    @staticmethod
    def _pick_anchor_id(
        protected: set[int],
        candidates: list[KnowledgeEntry],
        deleted_ids: list[int],
    ) -> int:
        """选择 audit_log.entry_id 锚点 — 规避 FK ON DELETE CASCADE.

        优先级:
            1. protected (人类编辑过的行, 本次不删, FK 安全)
            2. candidates 中未被删的行 (即 candidates - deleted)
            3. 首个被删 id (兜底; 生产 fk=ON 时 audit 会被级联清, 已知缺口)
        """
        if protected:
            return next(iter(protected))
        deleted_set = set(deleted_ids)
        for e in candidates:
            if e.id not in deleted_set:
                return e.id
        return deleted_ids[0]

    async def execute(self, db: AsyncSession, slug: str) -> BulkOpReport:
        """真执行: 删除 SQLite 行 + 写 audit_log + 同步删 ChromaDB (best-effort).

        Important:
            - dry_run=True 时退化为 preview (无任何 db 变更, 不 commit).
            - dry_run=False 时本函数自身负责 ``db.commit()``,
              同事务内一并落入业务删除与 bulk_delete audit (宪章 §4).
            - ChromaDB 同步 (宪章 §6, Stage 2 兑现): SQLite commit 之后
              best-effort 删 ChromaDB. 失败仅记账到 ``chromadb_failed_ids``,
              不反向回滚 SQLite — ChromaDB 是 derived data, 真相源在 SQLite,
              一致性偏差由 Stage 4 重灌脚本扫描补救.
            - ``slug`` 参数在 ChromaDB 同步阶段消费, 决定 ns_{slug}_knowledge
              集合定位; 全局知识 (namespace_id IS NULL) 落在 __global__ 集合.
            - 真删 0 行时不写 audit_log (空操作无审计意义); 仍返回报告.
        """
        candidates, protected = await self._candidates_and_protected(db)
        report, affected = self._build_report(candidates, protected)

        if self.dry_run:
            log.info(
                "[bulk_guard] dry-run %s slug=%s affected=%d preserved=%d",
                self.op_name, slug, report.affected_count, report.preserved_audited_count,
            )
            return report

        if not affected:
            log.info(
                "[bulk_guard] executed %s slug=%s deleted=0 preserved=%d (no audit, no chromadb)",
                self.op_name, slug, report.preserved_audited_count,
            )
            return report

        # ── 关键: SQLite delete 前缓存 (id, namespace_id, entry_type) 元组.
        # ORM 实例 delete 后再访问字段在某些 session 配置下会触发 refresh
        # (即便 expire_on_commit=False), 元组化彻底规避.
        to_delete: list[tuple[int, int | None, str]] = [
            (e.id, e.namespace_id, e.entry_type) for e in affected
        ]
        deleted_ids = await self._delete_sqlite_rows(db, affected)
        audit = await self._write_bulk_audit(db, candidates, protected, deleted_ids, report)
        await db.commit()
        report.audit_log_id = audit.id
        self._sync_chromadb(slug, to_delete, report)  # best-effort, 不阻业务

        log.info(
            "[bulk_guard] executed %s slug=%s sqlite_deleted=%d chromadb_deleted=%d "
            "chromadb_failed=%d preserved=%d audit_id=%s",
            self.op_name, slug, len(deleted_ids), report.chromadb_deleted_count,
            len(report.chromadb_failed_ids), report.preserved_audited_count, audit.id,
        )
        return report

    # ── 内部: execute 三段式拆解 (主体 ≤20 行铁律) ─────────────

    async def _delete_sqlite_rows(
        self, db: AsyncSession, affected: list[KnowledgeEntry]
    ) -> list[int]:
        """SQLite 行删除. 返回已删 entry_id 列表 (顺序与 affected 一致)."""
        deleted_ids: list[int] = []
        for e in affected:
            await db.delete(e)
            deleted_ids.append(e.id)
        return deleted_ids

    async def _write_bulk_audit(
        self,
        db: AsyncSession,
        candidates: list[KnowledgeEntry],
        protected: set[int],
        deleted_ids: list[int],
        report: BulkOpReport,
    ) -> KnowledgeAuditLog:
        """写 bulk_delete 主记录 (宪章 §4). 锚点选取见 _pick_anchor_id.

        full_purge (整域销毁) 时锚点强制 entry_id=NULL: 本次删除的所有 KE 都将被
        后续 namespace CASCADE 物理删除, 任何指向真实 entry 的锚点都会被级联清掉
        导致审计自毁; entry_id=NULL 符合"跨 entry 批操作无单一锚点"语义且不受
        knowledge_entries FK CASCADE 影响, 删除痕迹得以留存.
        """
        anchor_id = None if self.full_purge else self._pick_anchor_id(
            protected, candidates, deleted_ids
        )
        return await write_audit(
            db,
            entry_id=anchor_id,
            action="bulk_delete",
            from_status="any",
            to_status="deleted",
            actor_id=self.actor_id,
            reason=self.reason,
            diff={
                "op_name": self.op_name,
                "scope_filter": self.scope_filter,
                "deleted_ids": deleted_ids,
                "preserved_audited_count": report.preserved_audited_count,
            },
        )

    def _sync_chromadb(
        self,
        slug: str,
        to_delete: list[tuple[int, int | None, str]],
        report: BulkOpReport,
    ) -> None:
        """ChromaDB best-effort 同步 (宪章 §6).

        真故障入 chromadb_failed_ids 不阻业务 — derived data 失败永远不应反向
        侵蚀真相源 (SQLite). 局部 import 防循环: knowledge_retriever 间接导入
        engine.registry, 后者依赖 app.config 在 module load 期就绪.
        模块属性调用 (而非 ``from ... import name``) 以兼容 monkeypatch.
        """
        from app.knowledge import knowledge_retriever
        for entry_id, namespace_id, entry_type in to_delete:
            try:
                knowledge_retriever.delete_knowledge_entry(
                    slug=slug, entry_id=entry_id, namespace_id=namespace_id,
                    entry_type=entry_type,
                )
                report.chromadb_deleted_count += 1
            except Exception as exc:
                report.chromadb_failed_ids.append(entry_id)
                log.warning(
                    "[bulk_guard] chromadb delete failed entry_id=%d slug=%s: %s",
                    entry_id, slug, exc,
                )
