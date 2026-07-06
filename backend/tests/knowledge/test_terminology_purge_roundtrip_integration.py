"""集成测试 — 清场往返 (task 10).

聚焦探索性测试未直接覆盖的清场 *往返* 行为:

  - `_purge_schema_terminology` (api/terminology_refresh): 写入 source=schema 术语
    + 向量 + open 冲突 → 清场 → KE + 向量 + open 冲突全清, 无孤儿
    (Property 4 探索只做 *静态* 标签一致性断言, 未跑真实清场往返)
  - `_delete_schema_terminology` (trainer_purge): ns 级删术语并返回行供向量清理
    (full-rebuild 探索 Property 7 走整条 purge_legacy_for_full_rebuild, 本用例
    单测 helper 自身的 DELETE + 返回值契约)

清场函数 (`_purge_schema_terminology`) 内部自管 `async with async_session()` 并
COMMIT, 不能复用 conftest 的 SAVEPOINT-rollback async_session (会与 _restart_savepoint
冲突). 故用本地真实引擎 + ns 级 teardown (见 task 6/7/8 探索测试同款 pattern).
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
from app.models.terminology_conflict import TerminologyConflict

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


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

    if created_ns_ids:
        async with factory() as db:
            await db.execute(delete(Namespace).where(Namespace.id.in_(created_ns_ids)))
            await db.commit()
    await engine.dispose()


def _mk_term(ns_id, *, source, content, repo_id=None):
    return KnowledgeEntry(
        namespace_id=ns_id,
        entry_type="terminology",
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
#  _purge_schema_terminology — 写入 → 清场 → KE+向量+open 冲突全清
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_purge_schema_terminology_roundtrip(
    async_session, chroma_isolated, monkeypatch,
):
    """清场往返: source=schema 术语 KE + 向量 + open 冲突全清, source=manual 保留.

    钉死写入/清场标签自洽 (write_tag=purge_tag=schema) 的 *运行时* 行为, 且清场
    不误删非 schema 术语 (manual)、不漏删向量.
    """
    from app.api import terminology_refresh as refresh_module

    monkeypatch.setattr(refresh_module, "async_session", async_session)

    # 捕获向量清理调用 (验证每条 schema 术语向量被纳入)
    purged_vector_ids: list[int] = []

    def _spy_delete_vectors(**kwargs):
        eid = kwargs.get("entry_id")
        if eid is not None:
            purged_vector_ids.append(eid)

    monkeypatch.setattr(
        refresh_module, "delete_terminology_vectors", _spy_delete_vectors,
    )

    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(
            name=f"purge_rt_{uid}", slug=f"purge_rt_{uid}",
            description="清场往返",
        )
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        async_session._created_ns_ids.append(ns.id)

        schema_a = _mk_term(ns.id, source="schema", content="schema术语A")
        schema_b = _mk_term(ns.id, source="schema", content="schema术语B")
        manual_keep = _mk_term(ns.id, source="manual", content="manual术语保留")
        db.add_all([schema_a, schema_b, manual_keep])
        await db.commit()
        await db.refresh(schema_a)
        await db.refresh(schema_b)
        await db.refresh(manual_keep)
        schema_ids = {schema_a.id, schema_b.id}
        manual_id = manual_keep.id

        # open 冲突: candidate_source=schema (应清) + candidate_source=manual (应留)
        db.add(TerminologyConflict(
            namespace_id=ns.id, existing_entry_id=schema_a.id,
            candidate_payload="{}", candidate_source="schema", status="open",
        ))
        db.add(TerminologyConflict(
            namespace_id=ns.id, existing_entry_id=manual_keep.id,
            candidate_payload="{}", candidate_source="manual", status="open",
        ))
        await db.commit()
        ns_id, ns_slug = ns.id, ns.slug

    deleted = await refresh_module._purge_schema_terminology(ns_id, ns_slug)
    assert deleted == 2, f"应清 2 条 schema 术语, 实测 {deleted}"

    # ── 验证 ──
    async with async_session() as db:
        surviving = (await db.execute(
            select(KnowledgeEntry.id, KnowledgeEntry.source).where(
                KnowledgeEntry.namespace_id == ns_id,
                KnowledgeEntry.entry_type == "terminology",
            )
        )).all()
        surviving_ids = {kid for kid, _ in surviving}
        # schema 术语全清, manual 保留 (无孤儿)
        assert surviving_ids == {manual_id}, (
            f"清场后应仅余 manual 术语 id={manual_id}, 实测 {surviving}"
        )

        # open 冲突: schema 清, manual 留
        conflicts = (await db.execute(
            select(TerminologyConflict.candidate_source).where(
                TerminologyConflict.namespace_id == ns_id,
                TerminologyConflict.status == "open",
            )
        )).scalars().all()
        assert sorted(conflicts) == ["manual"], (
            f"open 冲突应仅余 candidate_source=manual, 实测 {conflicts}"
        )

    # 每条 schema 术语向量被纳入清理
    assert set(purged_vector_ids) == schema_ids, (
        f"schema 术语向量未全部纳入清理. 期望 {schema_ids}, 实测 {purged_vector_ids}"
    )


# ════════════════════════════════════════════════════════════════
#  _delete_schema_terminology — ns 级删术语并返回行供向量清理
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delete_schema_terminology_returns_rows_for_vector_cleanup(
    async_session,
):
    """_delete_schema_terminology: 删 ns 下 source=schema 术语 KE,
    返回 [(entry_id, namespace_id, entry_type), ...] 供 ChromaDB 清理.

    不命中: source=git 术语 (per-repo 清场负责) / source=schema 非术语.
    """
    from app.knowledge.trainer_purge import _delete_schema_terminology

    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(
            name=f"del_term_{uid}", slug=f"del_term_{uid}",
            description="ns 级删术语",
        )
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        async_session._created_ns_ids.append(ns.id)

        schema_term = _mk_term(ns.id, source="schema", content="schema术语")
        # 不应命中: git 术语 (per-repo 清场负责)
        git_term = _mk_term(ns.id, source="code_extract", content="git术语", repo_id=None)
        # 不应命中: schema 非术语
        schema_nonterm = KnowledgeEntry(
            namespace_id=ns.id, entry_type="example", status="proposed",
            tier="normal", content="schema example", payload="{}",
            source="schema", is_superseded=False, raw_input="", evidence_json="{}",
        )
        db.add_all([schema_term, git_term, schema_nonterm])
        await db.commit()
        await db.refresh(schema_term)
        await db.refresh(git_term)
        await db.refresh(schema_nonterm)
        schema_term_id = schema_term.id
        git_term_id = git_term.id
        nonterm_id = schema_nonterm.id

        rows = await _delete_schema_terminology(db, ns.id)
        await db.commit()

        # 返回契约: 仅 schema 术语行, (entry_id, namespace_id, entry_type)
        assert rows == [(schema_term_id, ns.id, "terminology")], (
            f"返回行应仅含 schema 术语 (entry_id, ns_id, entry_type), 实测 {rows}"
        )

        remaining = (await db.execute(
            select(KnowledgeEntry.id).where(KnowledgeEntry.namespace_id == ns.id)
        )).scalars().all()
        assert schema_term_id not in remaining, "schema 术语应被删"
        assert git_term_id in remaining, "git 术语不应被 ns 级 schema 清场命中"
        assert nonterm_id in remaining, "schema 非术语不应被命中"


@pytest.mark.asyncio
async def test_delete_schema_terminology_empty_returns_empty_list(async_session):
    """无 schema 术语时返回空 list (纯 DELETE 幂等, 不报错)."""
    from app.knowledge.trainer_purge import _delete_schema_terminology

    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(
            name=f"del_empty_{uid}", slug=f"del_empty_{uid}",
            description="空清场",
        )
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        async_session._created_ns_ids.append(ns.id)

        rows = await _delete_schema_terminology(db, ns.id)
        await db.commit()
        assert rows == []
