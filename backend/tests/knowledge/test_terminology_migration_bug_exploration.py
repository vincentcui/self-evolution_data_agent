"""Bug condition exploration test — 存量迁移幂等回填 (Property 5).

**Validates: Requirements 2.8**

═══════════════════════════════════════════════════════════════════════════════
  Property 5: Bug Condition — 存量迁移幂等回填
═══════════════════════════════════════════════════════════════════════════════

  对任意历史术语 KE (`entry_type="terminology" AND source="code_extract"` 带 repo_id 值),
  迁移后 SHALL 满足:
      source == "schema"  AND  repo_id IS NULL
  对应术语冲突 (`candidate_source="code_extract"` 的 TerminologyConflict):
      candidate_source == "schema"  AND  candidate_repo_id IS NULL
  迁移重复运行 SHALL 不改变已迁移条目 (幂等, 二次命中 0 行);
  非 git / 非术语条目 SHALL NOT 受影响.

  **CRITICAL**: 本测试在未修复代码上 *预期 FAIL* —— 失败即确认 bug 存在.
  存量 `source="code_extract"` 的术语 KE 与 `candidate_source="code_extract"` 的冲突当前 **无任何
  迁移路径回填**: `app.db.schema_migrations` 中不存在
  `_migrate_terminology_source_to_schema` 函数 (待 task 9.8 新增 migration_019).

  反例形态: AttributeError —— `schema_migrations._migrate_terminology_source_to_schema`
            不存在, 存量 git 术语条目永远清不掉 / 回填不了.

  方法论说明:
    - migration 用 `async with engine.begin()` 自管事务并 COMMIT, 无法靠 conftest
      的 SAVEPOINT rollback 隔离. 故本测试自建 engine + 唯一 namespace 种子数据,
      迁移后断言, 最后显式 DELETE namespace (CASCADE 清 KE + 冲突) 兜底清理.
    - 入口按候选名集合解析 (survive task 9.8 落地前后):
      解析失败即视为 "迁移路径缺失" 反例.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.db.schema_migrations as schema_migrations
from app.models.git_repo import GitRepo
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace
from app.models.terminology_conflict import TerminologyConflict

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


# ════════════════════════════════════════════════════════════════
#  迁移入口解析 — 兼容 task 9.8 落地前后
# ════════════════════════════════════════════════════════════════

_MIGRATION_FN_NAME = "_migrate_terminology_source_to_schema"


def _resolve_migration_fn():
    """解析存量术语回填迁移函数 — 不存在即视为迁移路径缺失反例.

    未修复 (当前): schema_migrations 中无此函数 → AttributeError.
    修复后 (task 9.8): _migrate_terminology_source_to_schema(engine) 存在.
    """
    fn = getattr(schema_migrations, _MIGRATION_FN_NAME, None)
    assert fn is not None, (
        f"迁移函数 schema_migrations.{_MIGRATION_FN_NAME} 不存在 —— 存量 "
        "source='code_extract' 的术语 KE / candidate_source='code_extract' 的冲突无迁移路径回填, "
        "新清场逻辑 (按 source='schema') 永远清不掉这些存量孤儿条目. "
        "(待 task 9.8 新增 migration_019)"
    )
    return fn


# ════════════════════════════════════════════════════════════════
#  自管 engine — migration 自带 COMMIT, 不能靠 SAVEPOINT 回滚
# ════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def migration_engine(_engine):
    """复用 conftest._engine 已 prepare 好 schema 的库, 但用独立 engine 跑迁移.

    依赖 `_engine` 仅为确保 prepare_test_schema 已建表/对齐列; 实际迁移与种子数据
    走本 engine 自管事务 (会真实 COMMIT), 由测试显式清理种子 namespace.
    """
    eng = create_async_engine(TEST_DATABASE_URL)
    yield eng
    await eng.dispose()


# ── 生成的存量条目种类 ──────────────────────────────────────────
#   git_term      : terminology + source=git + repo_id 值  → 应回填
#   schema_term   : terminology + source=schema + repo_id=NULL → 应不动 (幂等目标态)
#   manual_term   : terminology + source=manual (非 git 术语) → 应不动
#   git_nonterm   : example + source=git + repo_id 值 (非术语) → 应不动
_ENTRY_KINDS = ["git_term", "schema_term", "manual_term", "git_nonterm"]


async def _seed_namespace(db: AsyncSession) -> tuple[int, int]:
    """建唯一 ns + 1 parsed GitRepo. 返回 (ns_id, repo_id)."""
    uid = uuid.uuid4().hex[:8]
    ns = Namespace(
        name=f"mig_bug_{uid}", slug=f"mig_bug_{uid}",
        description="存量迁移幂等回填复现",
    )
    db.add(ns)
    await db.flush()
    repo = GitRepo(
        namespace_id=ns.id,
        url=f"https://example.invalid/mig_{uid}.git",
        parse_status="parsed",
    )
    db.add(repo)
    await db.flush()
    return ns.id, repo.id


async def _seed_entries(
    db: AsyncSession, ns_id: int, repo_id: int, kinds: list[str],
) -> tuple[list[int], list[int], int, list[int], list[int]]:
    """按 kinds 写入 KE + 冲突. 返回:

    (git_term_ke_ids, untouched_ke_snapshot_ids, anchor_id,
     git_conflict_ids, nongit_conflict_ids)
    其中 untouched_ke 指应保持不变的 KE id 集合.
    """
    git_term_ids: list[int] = []
    untouched_ids: list[int] = []
    for i, kind in enumerate(kinds):
        if kind == "git_term":
            ke = KnowledgeEntry(
                namespace_id=ns_id, entry_type="terminology", status="proposed",
                tier="normal", content=f"git术语{i}", payload="{}",
                source="code_extract", repo_id=repo_id,
            )
        elif kind == "schema_term":
            ke = KnowledgeEntry(
                namespace_id=ns_id, entry_type="terminology", status="proposed",
                tier="normal", content=f"schema术语{i}", payload="{}",
                source="schema", repo_id=None,
            )
        elif kind == "manual_term":
            ke = KnowledgeEntry(
                namespace_id=ns_id, entry_type="terminology", status="proposed",
                tier="normal", content=f"manual术语{i}", payload="{}",
                source="manual", repo_id=None,
            )
        else:  # git_nonterm
            ke = KnowledgeEntry(
                namespace_id=ns_id, entry_type="example", status="proposed",
                tier="normal", content=f"git示例{i}", payload="{}",
                source="code_extract", repo_id=repo_id,
            )
        db.add(ke)
        await db.flush()
        if kind == "git_term":
            git_term_ids.append(ke.id)
        else:
            untouched_ids.append(ke.id)

    # ── 冲突需要一个 anchor KE (existing_entry_id NOT NULL) ──
    anchor = KnowledgeEntry(
        namespace_id=ns_id, entry_type="terminology", status="canonical",
        tier="normal", content="anchor", payload="{}", source="manual",
    )
    db.add(anchor)
    await db.flush()

    git_conflict_ids: list[int] = []
    nongit_conflict_ids: list[int] = []
    # git 冲突 (应回填) — 与 git_term 数量挂钩, 至少 1 条
    n_git_conf = max(1, sum(1 for k in kinds if k == "git_term"))
    for _ in range(n_git_conf):
        c = TerminologyConflict(
            namespace_id=ns_id, existing_entry_id=anchor.id,
            candidate_payload="{}", candidate_source="code_extract",
            candidate_repo_id=repo_id, status="open",
        )
        db.add(c)
        await db.flush()
        git_conflict_ids.append(c.id)
    # 非 git 冲突 (应不动)
    c2 = TerminologyConflict(
        namespace_id=ns_id, existing_entry_id=anchor.id,
        candidate_payload="{}", candidate_source="manual",
        candidate_repo_id=None, status="open",
    )
    db.add(c2)
    await db.flush()
    nongit_conflict_ids.append(c2.id)

    return git_term_ids, untouched_ids, anchor.id, git_conflict_ids, nongit_conflict_ids


async def _cleanup_namespace(engine, ns_id: int) -> None:
    """显式删 ns (CASCADE 清 KE + 冲突 + repo) — 兜底, migration 已 COMMIT 种子数据."""
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM namespaces WHERE id = :nid"), {"nid": ns_id}
        )


# ════════════════════════════════════════════════════════════════
#  Property 5a — 存量 git 术语回填 schema + repo_id 置空
#  (未修复代码上预期 FAIL: 迁移函数不存在)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(kinds=st.lists(st.sampled_from(_ENTRY_KINDS), min_size=1, max_size=6))
async def test_migration_backfills_git_terminology_to_schema(
    migration_engine,
    kinds: list[str],
):
    """存量 git 术语 KE / 冲突回填 schema; 非 git / 非术语不受影响.

    EXPECTED OUTCOME on unfixed code: FAIL —— schema_migrations.
    _migrate_terminology_source_to_schema 不存在 (AttributeError/解析断言失败).
    """
    # 至少保证有一条 git_term, 使断言有意义
    if "git_term" not in kinds:
        kinds = [*kinds, "git_term"]

    migrate_fn = _resolve_migration_fn()

    async with AsyncSession(migration_engine, expire_on_commit=False) as db:
        ns_id, repo_id = await _seed_namespace(db)
        (git_term_ids, untouched_ids, _anchor_id,
         git_conf_ids, nongit_conf_ids) = await _seed_entries(
            db, ns_id, repo_id, kinds,
        )
        # 快照"应不动"条目的 (source, repo_id)
        untouched_before = {}
        for kid in untouched_ids:
            row = (await db.execute(
                select(KnowledgeEntry.source, KnowledgeEntry.repo_id).where(
                    KnowledgeEntry.id == kid
                )
            )).first()
            untouched_before[kid] = (row[0], row[1])
        await db.commit()

    try:
        # ── 跑迁移 ──
        await migrate_fn(migration_engine)

        # ── 断言 ──
        async with AsyncSession(migration_engine, expire_on_commit=False) as db:
            # (1) git 术语 KE → source=schema AND repo_id IS NULL
            for kid in git_term_ids:
                src, rid = (await db.execute(
                    select(KnowledgeEntry.source, KnowledgeEntry.repo_id).where(
                        KnowledgeEntry.id == kid
                    )
                )).first()
                assert src == "schema" and rid is None, (
                    f"存量 git 术语 KE(id={kid}) 未回填: "
                    f"source={src!r} repo_id={rid!r} (期望 source='schema', repo_id=NULL)"
                )

            # (2) git 冲突 → candidate_source=schema AND candidate_repo_id IS NULL
            for cid in git_conf_ids:
                csrc, crid = (await db.execute(
                    select(
                        TerminologyConflict.candidate_source,
                        TerminologyConflict.candidate_repo_id,
                    ).where(TerminologyConflict.id == cid)
                )).first()
                assert csrc == "schema" and crid is None, (
                    f"存量 git 术语冲突(id={cid}) 未回填: "
                    f"candidate_source={csrc!r} candidate_repo_id={crid!r} "
                    "(期望 candidate_source='schema', candidate_repo_id=NULL)"
                )

            # (3) 非 git / 非术语 KE 不受影响
            for kid, (src0, rid0) in untouched_before.items():
                src1, rid1 = (await db.execute(
                    select(KnowledgeEntry.source, KnowledgeEntry.repo_id).where(
                        KnowledgeEntry.id == kid
                    )
                )).first()
                assert (src1, rid1) == (src0, rid0), (
                    f"非 git/非术语 KE(id={kid}) 被误改: "
                    f"({src0!r},{rid0!r}) → ({src1!r},{rid1!r})"
                )

            # (4) 非 git 冲突不受影响
            for cid in nongit_conf_ids:
                csrc, crid = (await db.execute(
                    select(
                        TerminologyConflict.candidate_source,
                        TerminologyConflict.candidate_repo_id,
                    ).where(TerminologyConflict.id == cid)
                )).first()
                assert csrc == "manual", (
                    f"非 git 冲突(id={cid}) candidate_source 被误改为 {csrc!r}"
                )
    finally:
        await _cleanup_namespace(migration_engine, ns_id)


# ════════════════════════════════════════════════════════════════
#  Property 5b — 迁移幂等 (二次运行不改变已迁移条目)
#  (未修复代码上预期 FAIL: 迁移函数不存在)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_migration_is_idempotent(migration_engine):
    """迁移重复运行不改变已迁移条目 (纯 UPDATE...WHERE, 二次命中 0 行).

    EXPECTED OUTCOME on unfixed code: FAIL —— 迁移函数不存在.
    """
    migrate_fn = _resolve_migration_fn()

    async with AsyncSession(migration_engine, expire_on_commit=False) as db:
        ns_id, repo_id = await _seed_namespace(db)
        git_term_ids, _untouched, _anchor, git_conf_ids, _nongit = await _seed_entries(
            db, ns_id, repo_id, ["git_term", "git_term"],
        )
        await db.commit()

    try:
        await migrate_fn(migration_engine)

        # 第一次迁移后快照
        async with AsyncSession(migration_engine, expire_on_commit=False) as db:
            after_first = {}
            for kid in git_term_ids:
                after_first[kid] = (await db.execute(
                    select(KnowledgeEntry.source, KnowledgeEntry.repo_id).where(
                        KnowledgeEntry.id == kid
                    )
                )).first()

        # 二次运行
        await migrate_fn(migration_engine)

        async with AsyncSession(migration_engine, expire_on_commit=False) as db:
            for kid in git_term_ids:
                after_second = (await db.execute(
                    select(KnowledgeEntry.source, KnowledgeEntry.repo_id).where(
                        KnowledgeEntry.id == kid
                    )
                )).first()
                assert after_second == after_first[kid], (
                    f"二次迁移改变了已迁移条目(id={kid}): "
                    f"{after_first[kid]} → {after_second} (迁移应幂等)"
                )
                assert after_second[0] == "schema" and after_second[1] is None, (
                    f"迁移后条目(id={kid}) 状态非预期: {after_second}"
                )
    finally:
        await _cleanup_namespace(migration_engine, ns_id)
