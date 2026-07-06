"""Bug condition exploration test — trainer 全量重建清术语 (Property 7).

**Validates: Requirements 2.7, 3.7**

═══════════════════════════════════════════════════════════════════════════════
  Property 7: Bug Condition — trainer 全量重建对术语先清后建
═══════════════════════════════════════════════════════════════════════════════

  对任意 trainer 全量重建 (`purge_legacy_for_full_rebuild`) 在某 namespace 上执行:
      SHALL 在清场阶段删除该 ns 下所有
      `entry_type="terminology" AND source="schema"` 的术语 KE (含 ChromaDB 向量)
      + open 术语冲突; 随后 trainer stage 全量重抽术语.
      即术语在全量重建下执行 ns 级「先清后建」, 与手动刷新路径语义一致.

  **CRITICAL**: 本测试在未修复代码上 *预期 FAIL* —— 失败即确认 bug 存在.

  **NOTE**: 这是按用户「清场需清术语」反馈修订后 *新增* 的修复要求; 未修复代码
  无 ns 级术语清场.

  未修复代码: `purge_legacy_for_full_rebuild` 只调 per-repo 的 `_delete_repo_extracted_kes`
  (`WHERE repo_id=:R AND source IN("code_extract")`). 修复后术语 KE 是
  ns 级 (`repo_id=NULL`, `source="schema"`), per-repo 清场 *天然不命中*, 术语残留.

  反例形态: terminology KE (source="schema", repo_id=NULL) 在 purge 后仍存在
            (未被删除), 且其 ChromaDB 向量未被清理.

  场景构造 (同一 ns):
    - example     (source="code_extract",     repo_id=R)  ← per-repo 清场命中
    - route_hint  (source="code_extract",     repo_id=R)  ← per-repo 清场命中
    - terminology (source="schema",          repo_id=NULL) ← ns 级清场目标 (修复后)
    - open TerminologyConflict (candidate_source="schema") ← open 冲突清场目标

  注: terminology 用 source="schema" 直接经 ORM 写入构造 (未修复代码的 Source
  Literal 校验只在闸门写入路径生效; ORM 直写无校验, 可设 source="schema").
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace
from app.models.git_repo import GitRepo
from app.models.terminology_conflict import TerminologyConflict

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


# ════════════════════════════════════════════════════════════════
#  本地 async_session — 真实引擎 + sessionmaker.
#  不能复用 knowledge/conftest 的 SAVEPOINT-rollback async_session:
#  purge_legacy_for_full_rebuild 内部调 db.begin_nested(), 与 SAVEPOINT
#  rollback 隔离的 _restart_savepoint 事件冲突 (InvalidRequestError).
#  本 fixture 走真实事务 + 末尾按 ns_id 显式清理 (CASCADE 删 KE/冲突).
# ════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def async_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    created_ns_ids: list[int] = []
    factory._created_ns_ids = created_ns_ids  # type: ignore[attr-defined]
    yield factory

    # ── teardown: 删测试创建的 ns (CASCADE 清 KE / 冲突) ──
    if created_ns_ids:
        async with factory() as db:
            await db.execute(
                delete(Namespace).where(Namespace.id.in_(created_ns_ids))
            )
            await db.commit()
    await engine.dispose()


def _mk_ke(ns_id, *, entry_type, source, repo_id, content):
    return KnowledgeEntry(
        namespace_id=ns_id,
        entry_type=entry_type,
        status="proposed",
        tier="normal",
        content=content,
        payload="{}",
        source=source,
        repo_id=repo_id,
        is_superseded=False,
        raw_input="",
        evidence_json="{}",
    )


# ════════════════════════════════════════════════════════════════
#  Property 7 — 全量重建删 ns 级 schema 术语 KE (含向量)
#  (未修复代码上预期 FAIL)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_full_rebuild_purges_schema_terminology(
    async_session, chroma_isolated, monkeypatch,
):
    """全量重建 SHALL ns 级删 source="schema" 术语 KE + 向量, 并清 open 冲突.

    同时保留 (per-repo 行为不变): example/route_hint(source∈code_extract,
    repo_id=R) 被 per-repo 清场删除.

    EXPECTED OUTCOME on unfixed code: FAIL —— per-repo 的 `_delete_repo_extracted_kes`
    (`WHERE repo_id=:R AND source IN("code_extract")`) 天然不命中
    ns 级术语 (repo_id=NULL, source="schema"), 术语 KE 与其向量残留.
    """
    from app.knowledge import trainer_purge
    from app.knowledge.trainer_purge import purge_legacy_for_full_rebuild

    # ── 捕获 ChromaDB 向量清理调用 (验证术语向量被纳入清理) ──
    deleted_vector_ids: list[int] = []

    def _spy_delete(**kwargs):
        eid = kwargs.get("entry_id")
        if eid is not None:
            deleted_vector_ids.append(eid)

    monkeypatch.setattr(trainer_purge, "delete_knowledge_entry", _spy_delete)

    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(
            name=f"frpurge_{uid}", slug=f"frpurge_{uid}",
            description="全量重建清术语复现",
        )
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        async_session._created_ns_ids.append(ns.id)  # teardown 清理

        repo = GitRepo(
            namespace_id=ns.id,
            url=f"https://example.invalid/frpurge_{uid}.git",
            parse_status="parsed",
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

        # ── per-repo 清场目标 (非术语, source∈code_extract, repo_id=R) ──
        example_ke = _mk_ke(
            ns.id, entry_type="example", source="code_extract",
            repo_id=repo.id, content="code_extract example",
        )
        route_hint_ke = _mk_ke(
            ns.id, entry_type="route_hint", source="code_extract",
            repo_id=repo.id, content="code_extract route_hint",
        )
        # ── ns 级清场目标 (术语, source="schema", repo_id=NULL) ──
        term_ke = _mk_ke(
            ns.id, entry_type="terminology", source="schema",
            repo_id=None, content="schema terminology",
        )
        db.add_all([example_ke, route_hint_ke, term_ke])
        await db.commit()
        await db.refresh(example_ke)
        await db.refresh(route_hint_ke)
        await db.refresh(term_ke)

        example_id = example_ke.id
        route_hint_id = route_hint_ke.id
        term_id = term_ke.id

        # ── open 术语冲突 (candidate_source="schema") ──
        conflict = TerminologyConflict(
            namespace_id=ns.id,
            existing_entry_id=term_ke.id,
            candidate_payload="{}",
            candidate_source="schema",
            status="open",
        )
        db.add(conflict)
        await db.commit()

        await purge_legacy_for_full_rebuild(db, repo.id, ns.id)
        await db.commit()

    # ── 验证 ──
    async with async_session() as db:
        # per-repo 非术语条目被删 (基线, 不变)
        assert await db.get(KnowledgeEntry, example_id) is None, (
            "per-repo example(source=code_extract) 应被全量重建清场删除"
        )
        assert await db.get(KnowledgeEntry, route_hint_id) is None, (
            "per-repo route_hint(source=code_extract) 应被全量重建清场删除"
        )

        # ns 级 schema 术语被删 (修复目标 —— 未修复代码上此断言 FAIL)
        surviving_term = await db.get(KnowledgeEntry, term_id)
        assert surviving_term is None, (
            "ns 级 schema 术语 KE 未被全量重建清场删除 (应执行先清后建). "
            "per-repo 的 _delete_repo_extracted_kes(WHERE repo_id=R AND source IN("
            "code_extract)) 天然不命中 repo_id=NULL/source=schema 的术语. "
            f"残留 KE id={term_id}, source={surviving_term.source!r}, "
            f"repo_id={surviving_term.repo_id!r}"
        )

        # open 术语冲突被清
        open_conflicts = (await db.execute(
            select(TerminologyConflict).where(
                TerminologyConflict.namespace_id == ns.id,
                TerminologyConflict.status == "open",
            )
        )).scalars().all()
        assert not open_conflicts, "open 术语冲突应被全量重建清场删除"

    # ── 术语向量被纳入 ChromaDB 清理 (修复目标) ──
    assert term_id in deleted_vector_ids, (
        "ns 级 schema 术语 KE 的 ChromaDB 向量未被纳入清理. "
        "术语 KE 应合并进 deleted_ke_ids 使步骤 4 向量清理覆盖. "
        f"实际清理的 entry_id={deleted_vector_ids}, 缺术语 id={term_id}"
    )
