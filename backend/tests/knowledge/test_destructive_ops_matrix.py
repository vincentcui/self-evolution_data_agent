"""Stage 2 Task 9 — 破坏性操作 11 用例测试矩阵索引.

依据 docs/todos/knowledge-unification-and-agent-loop/04-safety-and-bulk-ops.md §5.
6/11 用例在 Stage 2 task 3-7 实现, 5/11 Stage 3 deferred.
本文件做集成验证 — 不重复实现, 仅 index + 自检 + deferred placeholder.

Phase 8 T25: case #5 (reembed) 随 E 场景退役删除; case #10 改指 verify 脚本。

Stage 2 范围 (6 用例 — 跨文件实现, 跑全测试集自然 cover):
    1. test_git_reparse_preserves_human_edits
       → tests/test_chroma_lifecycle.py::test_clean_ke_via_bulk_guard_preserves_manual
    2. test_namespace_delete_with_human_knowledge_warning
       → tests/knowledge/test_namespace_delete.py::test_dry_run_returns_protected_count
    3. test_repo_delete_orphan_cleanup_safe
       → tests/knowledge/test_cleanup_stale_schema_summary.py::test_human_edited_orphan_preserved
    9. test_tier_change_chromadb_sync
       → tests/knowledge/test_patch_chromadb_sync.py (4 cases)
    10. test_sqlite_chromadb_consistency_check
        → scripts/verify_db_chromadb_consistency.py (离线诊断脚本)
    12. test_bulk_op_guard_blocks_above_threshold
        → tests/knowledge/test_namespace_delete.py::test_above_threshold_requires_confirm_token

Stage 3 deferred (5 用例 — 需要前置依赖):
    4. test_datasource_delete_marks_schema_stale       → 需 KE.status=stale 状态扩展
    6. test_business_terms_refresh_preserves_edited    → Stage 1 BT ORM 已退役
    7. test_self_answer_dedup_supersedes_old           → 需 unclear_item hash 去重机制
    8. test_parser_version_bump_lists_not_deletes      → 需 payload.parser_version CLI
    11. test_bulk_reject_audit_with_restore            → 需 Stage 3 审核 UI + restore API
"""

import pytest


# ════════════════════════════════════════════════════════════════════
# 矩阵索引 (本 task 不重复实现, 跨文件 PASS 即此 matrix 通过)
# ════════════════════════════════════════════════════════════════════

STAGE2_COVERED_CASES = {
    1: ("test_git_reparse_preserves_human_edits",
        "tests/test_chroma_lifecycle.py::test_clean_ke_via_bulk_guard_preserves_manual"),
    2: ("test_namespace_delete_with_human_knowledge_warning",
        "tests/knowledge/test_namespace_delete.py::test_dry_run_returns_protected_count"),
    3: ("test_repo_delete_orphan_cleanup_safe",
        "tests/knowledge/test_cleanup_stale_schema_summary.py::test_human_edited_orphan_preserved"),
    9: ("test_tier_change_chromadb_sync",
        "tests/knowledge/test_patch_chromadb_sync.py (4 cases)"),
    10: ("test_sqlite_chromadb_consistency_check",
         "scripts/verify_db_chromadb_consistency.py (离线诊断脚本)"),
    12: ("test_bulk_op_guard_blocks_above_threshold",
         "tests/knowledge/test_namespace_delete.py::test_above_threshold_requires_confirm_token"),
}

STAGE3_DEFERRED_CASES = {
    4: ("test_datasource_delete_marks_schema_stale",
        "需 KE.status=stale 状态扩展, Stage 3 接审核 UI 时一并加"),
    6: ("test_business_terms_refresh_preserves_edited",
        "Stage 1 BusinessTerm ORM 已退役 (rule 自然过时), 历史 use case 不再适用"),
    7: ("test_self_answer_dedup_supersedes_old",
        "需 unclear_item hash 去重机制 (Stage 3 self_answer 改造)"),
    8: ("test_parser_version_bump_lists_not_deletes",
        "需 payload.parser_version CLI 工具 (Stage 3 schema 演进治理)"),
    11: ("test_bulk_reject_audit_with_restore",
         "需 Stage 3 审核 UI + soft delete restore API"),
}


# ════════════════════════════════════════════════════════════════════
# Stage 2 7/12 矩阵覆盖完整性自检
# ════════════════════════════════════════════════════════════════════

def test_stage2_destructive_matrix_coverage():
    """Stage 2 必须覆盖 6 个 destructive ops 用例 (其余 5 个 Stage 3 deferred)."""
    assert len(STAGE2_COVERED_CASES) == 6, (
        f"Stage 2 应覆盖 6 用例, 实际 {len(STAGE2_COVERED_CASES)}"
    )
    assert len(STAGE3_DEFERRED_CASES) == 5, (
        f"Stage 3 deferred 应 5 用例, 实际 {len(STAGE3_DEFERRED_CASES)}"
    )
    # 11 用例编号完整 (原 #5 reembed 随 E 场景退役删除), 无重叠
    all_ids = set(STAGE2_COVERED_CASES.keys()) | set(STAGE3_DEFERRED_CASES.keys())
    expected = set(range(1, 13)) - {5}  # #5 retired (Phase 8 T25)
    assert all_ids == expected, (
        f"11 用例编号应完整 (排除退役 #5), 实际 {sorted(all_ids)}"
    )


# ════════════════════════════════════════════════════════════════════
# Stage 3 deferred 用例 placeholder (跑测试时 SKIP, 提醒未来实现)
# ════════════════════════════════════════════════════════════════════

@pytest.mark.skip(reason="Stage 3: 需 KE.status=stale 状态扩展 (审核 UI 接入时一并加)")
def test_datasource_delete_marks_schema_stale():
    """场景 4: datasource 删除时 schema_summary 应自动标 stale, 不删不召回."""
    pass


@pytest.mark.skip(reason="Stage 1 BusinessTerm ORM 已退役, 历史 use case 不再适用")
def test_business_terms_refresh_preserves_edited():
    """场景 6: business_terms refresher 自动刷新保护已编辑 proposed (历史)."""
    pass


@pytest.mark.skip(reason="Stage 3: 需 unclear_item hash 去重机制 (self_answer 改造)")
def test_self_answer_dedup_supersedes_old():
    """场景 7: self-answer 同 unclear_item 重跑 dedup 标 superseded 旧条目."""
    pass


@pytest.mark.skip(reason="Stage 3: 需 payload.parser_version CLI 工具")
def test_parser_version_bump_lists_not_deletes():
    """场景 8: parser 升级列出陈旧 schema_summary 但不自动清."""
    pass


@pytest.mark.skip(reason="Stage 3: 需审核 UI + soft delete restore API")
def test_bulk_reject_audit_with_restore():
    """场景 11: 审核员 bulk reject 100 条, 24h 内 restore 全成功."""
    pass
