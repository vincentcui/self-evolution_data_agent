"""BulkOperationGuard 单元测试 — 真实 SQLite, 验证宪章 6 条."""

import pytest
from sqlalchemy import select

from app.knowledge.bulk_guard import BulkOperationGuard
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry

# ─────────────────────────────────────────────────────────────────
# 宪章 §1 + §5: dry_run 默认 + 影响数报告 (无副作用)
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_no_side_effects(db_session):
    """dry_run=True 不写库, 仅返回报告."""
    db_session.add_all(
        [
            KnowledgeEntry(
                entry_type="schema_summary", content="x", source="code_extract", status="canonical"
            ),
            KnowledgeEntry(
                entry_type="schema_summary", content="y", source="code_extract", status="canonical"
            ),
        ]
    )
    await db_session.commit()

    guard = BulkOperationGuard(
        op_name="git_reparse_clean",
        scope_filter={"source": ["code_extract"], "entry_type": ["schema_summary"]},
        dry_run=True,
        actor_id=None,
        reason="test",
    )
    report = await guard.execute(db_session, slug="test_ns")

    assert report.affected_count == 2
    assert report.by_source == {"code_extract": 2}
    rows = (await db_session.scalars(select(KnowledgeEntry))).all()
    assert len(rows) == 2  # 没真删


# ─────────────────────────────────────────────────────────────────
# 宪章 §3: 人类编辑兜底
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_protects_human_edited_entries(db_session):
    """audit_log 中 actor_id != NULL 的 entry 视为人类知识, 不可批量删."""
    edited = KnowledgeEntry(
        entry_type="schema_summary", content="edited", source="code_extract", status="canonical"
    )
    plain = KnowledgeEntry(
        entry_type="schema_summary", content="plain", source="code_extract", status="canonical"
    )
    db_session.add_all([edited, plain])
    await db_session.flush()

    db_session.add(
        KnowledgeAuditLog(
            entry_id=edited.id,
            actor_id=42,
            action="edit",
            from_status="canonical",
            to_status="canonical",
        )
    )
    await db_session.commit()

    guard = BulkOperationGuard(
        op_name="git_reparse_clean",
        scope_filter={"source": ["code_extract"]},
        dry_run=False,
        reason="test",
    )
    report = await guard.execute(db_session, slug="test_ns")

    assert report.preserved_audited_count == 1  # edited 被保护
    remaining = (await db_session.scalars(select(KnowledgeEntry))).all()
    assert len(remaining) == 1
    assert remaining[0].id == edited.id


# ─────────────────────────────────────────────────────────────────
# 宪章 §4: 必写 audit_log
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_writes_audit_log_on_execute(db_session):
    db_session.add(
        KnowledgeEntry(
            entry_type="schema_summary", content="x", source="code_extract", status="canonical"
        )
    )
    await db_session.commit()

    guard = BulkOperationGuard(
        op_name="git_reparse_clean",
        scope_filter={"source": ["code_extract"]},
        dry_run=False,
        actor_id=99,
        reason="test",
    )
    await guard.execute(db_session, slug="test_ns")

    logs = (
        await db_session.scalars(
            select(KnowledgeAuditLog).where(KnowledgeAuditLog.action == "bulk_delete")
        )
    ).all()
    assert len(logs) >= 1
    assert logs[0].actor_id == 99


# ─────────────────────────────────────────────────────────────────
# 宪章 §2 + §5: source-aware 复合过滤 + 报告分组
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compound_scope_filter_intersects(db_session):
    """source + entry_type 复合时走 AND 语义, 仅交集命中."""
    db_session.add_all(
        [
            KnowledgeEntry(
                entry_type="schema_summary", content="a", source="code_extract", status="canonical"
            ),
            KnowledgeEntry(
                entry_type="terminology", content="b", source="code_extract", status="canonical"
            ),
            KnowledgeEntry(
                entry_type="schema_summary", content="c", source="manual", status="canonical"
            ),
        ]
    )
    await db_session.commit()

    report = await BulkOperationGuard(
        op_name="t",
        dry_run=True,
        scope_filter={"source": ["code_extract"], "entry_type": ["schema_summary"]},
    ).execute(db_session, slug="ns")
    assert report.affected_count == 1
    assert report.by_source == {"code_extract": 1}
    assert report.by_entry_type == {"schema_summary": 1}


@pytest.mark.asyncio
async def test_report_by_entry_type_grouping(db_session):
    """by_entry_type 字典聚合多类型计数."""
    db_session.add_all(
        [
            KnowledgeEntry(
                entry_type="schema_summary", content="a", source="code_extract", status="canonical"
            ),
            KnowledgeEntry(
                entry_type="schema_summary", content="b", source="code_extract", status="canonical"
            ),
            KnowledgeEntry(
                entry_type="terminology", content="c", source="code_extract", status="canonical"
            ),
        ]
    )
    await db_session.commit()

    report = await BulkOperationGuard(
        op_name="t", dry_run=True, scope_filter={"source": ["code_extract"]},
    ).execute(db_session, slug="ns")
    assert report.by_entry_type == {"schema_summary": 2, "terminology": 1}


# ─────────────────────────────────────────────────────────────────
# 边界: 0 行匹配 / 全部被保护 → 不写 audit
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_match_skips_audit(db_session):
    """0 行匹配 → 不写 audit (空操作无审计意义), 仍返回报告."""
    guard = BulkOperationGuard(
        op_name="t", dry_run=False, scope_filter={"source": ["code_extract"]},
    )
    report = await guard.execute(db_session, slug="ns")
    assert report.affected_count == 0
    assert report.audit_log_id is None
    logs = (await db_session.scalars(select(KnowledgeAuditLog))).all()
    assert len(logs) == 0


@pytest.mark.asyncio
async def test_all_candidates_protected_skips_audit(db_session):
    """全部 candidates 被保护 → 0 删 + 不写 audit + preserved 报告正确."""
    e = KnowledgeEntry(
        entry_type="schema_summary", content="x", source="code_extract", status="canonical"
    )
    db_session.add(e)
    await db_session.flush()
    db_session.add(
        KnowledgeAuditLog(
            entry_id=e.id, actor_id=1, action="edit",
            from_status="canonical", to_status="canonical",
        )
    )
    await db_session.commit()

    report = await BulkOperationGuard(
        op_name="t", dry_run=False, scope_filter={"source": ["code_extract"]},
    ).execute(db_session, slug="ns")
    assert report.affected_count == 0
    assert report.preserved_audited_count == 1
    assert report.audit_log_id is None
    bulk_logs = (
        await db_session.scalars(
            select(KnowledgeAuditLog).where(KnowledgeAuditLog.action == "bulk_delete")
        )
    ).all()
    assert len(bulk_logs) == 0


# ─────────────────────────────────────────────────────────────────
# Stage 2 Task 2: ChromaDB 同步兑现
#   §6 真删后 best-effort 删 ChromaDB; 失败收集到 chromadb_failed_ids 不阻业务.
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chromadb_synced_after_sqlite_delete(db_session, chroma_isolated):
    """真实 ChromaDB 写入 → bulk delete → ChromaDB count=0 + chromadb_deleted_count 准确.

    e2e 验证: SQLite 与 ChromaDB 双向消失, 报告字段如实记账.
    """
    from app.models.namespace import Namespace
    from app.engine.registry import get_knowledge_collection
    from app.knowledge.knowledge_retriever import upsert_knowledge_entry

    # 0. 创建 namespace (FK 约束)
    ns = Namespace(name="bulk_chroma_ns", slug="bulk_chroma_ns", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    # 1. 准备: SQLite 写 2 条 + ChromaDB 同步写
    e1 = KnowledgeEntry(
        entry_type="schema_summary", content="ke_1", source="code_extract",
        status="canonical", namespace_id=ns.id, tier="normal",
    )
    e2 = KnowledgeEntry(
        entry_type="schema_summary", content="ke_2", source="code_extract",
        status="canonical", namespace_id=ns.id, tier="normal",
    )
    db_session.add_all([e1, e2])
    await db_session.commit()
    upsert_knowledge_entry(
        slug="bulk_chroma_ns", entry_id=e1.id, content="ke_1", tier="normal",
        namespace_id=ns.id, entry_type="schema_summary", status="canonical",
    )
    upsert_knowledge_entry(
        slug="bulk_chroma_ns", entry_id=e2.id, content="ke_2", tier="normal",
        namespace_id=ns.id, entry_type="schema_summary", status="canonical",
    )
    coll = get_knowledge_collection("bulk_chroma_ns")
    assert coll.count() == 2  # 前置确认 ChromaDB 有 2 条

    # 2. 执行 bulk delete
    guard = BulkOperationGuard(
        op_name="test_chromadb_sync",
        scope_filter={"source": ["code_extract"]},
        dry_run=False,
        reason="test",
    )
    report = await guard.execute(db_session, slug="bulk_chroma_ns")

    # 3. 验证: SQLite + ChromaDB 双消失 + 报告字段
    assert report.affected_count == 2
    assert report.chromadb_deleted_count == 2
    assert report.chromadb_failed_ids == []
    assert report.audit_log_id is not None  # §4 必写 audit, ChromaDB 同步不应破坏
    rows = (await db_session.scalars(
        select(KnowledgeEntry).where(KnowledgeEntry.namespace_id == ns.id)
    )).all()
    assert len(rows) == 0
    assert coll.count() == 0


@pytest.mark.asyncio
async def test_chromadb_failure_does_not_rollback_sqlite(db_session, monkeypatch):
    """ChromaDB delete 抛异常 → SQLite 仍删 + chromadb_failed_ids 收集.

    设计原则: ChromaDB 是 derived data, 失败不应反向回滚权威源 (SQLite).
    后续 Stage 4 重灌脚本扫一致性补救.
    """
    from app.models.namespace import Namespace

    ns = Namespace(name="bulk_chroma_fail_ns", slug="bulk_chroma_fail_ns", description="")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    e1 = KnowledgeEntry(
        entry_type="schema_summary", content="x", source="code_extract",
        status="canonical", namespace_id=ns.id, tier="normal",
    )
    db_session.add(e1)
    await db_session.commit()
    e1_id = e1.id

    # patch 真实导入路径下的 delete_knowledge_entry, 模拟 ChromaDB 故障
    def boom(**kwargs):
        raise RuntimeError("simulated chromadb crash")
    monkeypatch.setattr(
        "app.knowledge.knowledge_retriever.delete_knowledge_entry", boom
    )

    guard = BulkOperationGuard(
        op_name="test_chromadb_failure",
        scope_filter={"source": ["code_extract"]},
        dry_run=False,
        reason="test",
    )
    report = await guard.execute(db_session, slug="bulk_chroma_fail_ns")

    # SQLite 已删 (ChromaDB 失败不能反向回滚)
    rows = (await db_session.scalars(
        select(KnowledgeEntry).where(KnowledgeEntry.namespace_id == ns.id)
    )).all()
    assert len(rows) == 0
    # ChromaDB 失败被记录
    assert report.affected_count == 1
    assert report.chromadb_deleted_count == 0
    assert e1_id in report.chromadb_failed_ids
