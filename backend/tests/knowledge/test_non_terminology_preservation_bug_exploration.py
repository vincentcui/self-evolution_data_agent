"""Preservation property test — 非术语知识清场与归属不变 (Property 6).

**Validates: Requirements 3.1, 3.2, 3.4, 3.5, 3.6, 3.8, 3.9**

═══════════════════════════════════════════════════════════════════════════════
  Property 6: Preservation — 非术语知识清场与归属不变
═══════════════════════════════════════════════════════════════════════════════

  对任意 *不满足* bug 条件的输入 (非术语条目、非术语抽取路径), 修复后的代码
  SHALL 产生与修复前完全相同的结果. 本测试遵循 observation-first 方法论:
  先在 *未修复代码* 上观察基线行为, 再编码该行为为属性测试.

  本任务只钉死 *非术语* scope 的不变部分; git_reparse 对 *术语* 的清场变更
  由 task 7 (Property 8) 覆盖, 不在此断言.

  **IMPORTANT**: 本测试在 *未修复代码* 上 *预期 PASS* —— PASS 即确认待保留基线行为.

  ────────────────────────────────────────────────────────────────────────────
  在未修复代码上观察到的基线行为 (probe 记录, 本测试据此编码):
  ────────────────────────────────────────────────────────────────────────────
  [1] purge_legacy_for_full_rebuild(repo_id=A):
        删除 non-terminology 且 (repo_id==A AND source∈{code_extract});
        保留 其他 repo 的条目 (repo_id==B) 与 非清场 source (manual/...).
  [2] git banner (source=="code_extract"): total=count(source==git),
        canonical=count(source==git AND status==canonical).
  [3a] gate 唯一键去重 + synonyms 合并: 同 (collection,database,db_type) 三元组
        且词形相交 → 命中同一 active 行并合并 synonyms.
  [3b] gate canonical 保护: 既有 canonical + 候选词形相交 → 落 open conflict,
        canonical synonyms 不变 (冲突工单机制保留, 仅 candidate_repo_id 维度改动).
  [4] 删 GitRepo: 非术语 KE 存活, repo_id → NULL (ondelete=SET NULL).
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
import pytest_asyncio
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.git_repo import GitRepo
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import DataSource, Namespace
from app.models.terminology_conflict import TerminologyConflict

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)

# 非术语 entry_type (排除 terminology) —— 覆盖 NOT isBugCondition(X) 输入域
NON_TERM_ENTRY_TYPES = ["example", "route_hint", "rule", "schema_summary", "instance_alias"]
# trainer purge per-repo 命中 source
PURGE_SOURCES = ["code_extract"]
# 非清场 / 非 scope source (保留)
KEPT_SOURCES = ["manual", "schema", "agent_learn"]


# ════════════════════════════════════════════════════════════════
#  本地 async_session — 真实引擎 + sessionmaker.
#  不能复用 knowledge/conftest 的 SAVEPOINT-rollback async_session:
#  purge_legacy_for_full_rebuild 内部 db.begin_nested(); git_reparse 经
#  BulkOperationGuard.execute 内部自行 db.commit() —— 均与 SAVEPOINT rollback
#  隔离的 _restart_savepoint 事件冲突. 本 fixture 走真实事务 + 末尾按 ns_id
#  显式清理 (CASCADE 删 KE/冲突). (与 task 6/7 一致.)
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

    if created_ns_ids:
        async with factory() as db:
            await db.execute(delete(Namespace).where(Namespace.id.in_(created_ns_ids)))
            await db.commit()
    await engine.dispose()


def _mk_ke(ns_id, *, entry_type, source, repo_id, content, status="proposed", payload="{}"):
    return KnowledgeEntry(
        namespace_id=ns_id, entry_type=entry_type, status=status, tier="normal",
        content=content, payload=payload, source=source, repo_id=repo_id,
        is_superseded=False, raw_input="", evidence_json="{}",
    )


# 生成单条非术语条目规格: (entry_type, source, which_repo∈{"A","B"})
_purge_spec_st = st.tuples(
    st.sampled_from(NON_TERM_ENTRY_TYPES),
    st.sampled_from(PURGE_SOURCES + KEPT_SOURCES),
    st.sampled_from(["A", "B"]),
)


# ════════════════════════════════════════════════════════════════
#  Property 6a — trainer 全量重建对非术语条目按 per-repo 规则清/留
#  (Req 3.1, 3.2)  ——  未修复代码上预期 PASS
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(specs=st.lists(_purge_spec_st, min_size=1, max_size=6))
async def test_purge_non_terminology_per_repo_baseline(
    async_session, chroma_isolated, specs: list[tuple[str, str, str]],
):
    """purge_legacy_for_full_rebuild(repo_id=A) 对非术语条目按 per-repo 规则:
    删除 (repo_id==A AND source∈{code_extract}); 保留其余.
    保留条目同时维持其原 source / repo_id 归属 (Req 3.2).

    EXPECTED OUTCOME on unfixed code: PASS (基线保留行为).
    """
    from app.knowledge.trainer_purge import purge_legacy_for_full_rebuild

    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(name=f"pp_{uid}", slug=f"pp_{uid}", description="purge preservation")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        async_session._created_ns_ids.append(ns.id)

        repoA = GitRepo(namespace_id=ns.id, url=f"https://x.invalid/a_{uid}.git", parse_status="parsed")
        repoB = GitRepo(namespace_id=ns.id, url=f"https://x.invalid/b_{uid}.git", parse_status="parsed")
        db.add_all([repoA, repoB])
        await db.commit()
        await db.refresh(repoA)
        await db.refresh(repoB)
        repo_id_map = {"A": repoA.id, "B": repoB.id}

        # 物化条目 + 计算期望
        recs: list[tuple[int, str, str | None, bool]] = []  # (ke_id, source, repo_id, expect_kept)
        for i, (etype, source, which) in enumerate(specs):
            rid = repo_id_map[which]
            ke = _mk_ke(ns.id, entry_type=etype, source=source, repo_id=rid, content=f"c{i}")
            db.add(ke)
            await db.flush()
            expect_deleted = (which == "A") and (source in PURGE_SOURCES)
            recs.append((ke.id, source, rid, not expect_deleted))
        await db.commit()

        await purge_legacy_for_full_rebuild(db, repoA.id, ns.id)
        await db.commit()

    async with async_session() as db:
        for ke_id, source, rid, expect_kept in recs:
            ke = await db.get(KnowledgeEntry, ke_id)
            if expect_kept:
                assert ke is not None, (
                    f"非术语条目应被保留 (per-repo 清场不命中): "
                    f"ke_id={ke_id} source={source!r} repo_id={rid}"
                )
                # Req 3.2: 保留条目 source / repo_id 归属不变
                assert ke.source == source, (
                    f"保留条目 source 被改动: ke_id={ke_id} 期望 {source!r} 实际 {ke.source!r}"
                )
                assert ke.repo_id == rid, (
                    f"保留条目 repo_id 被改动: ke_id={ke_id} 期望 {rid} 实际 {ke.repo_id}"
                )
            else:
                assert ke is None, (
                    f"per-repo 清场目标应被删除: ke_id={ke_id} source={source!r} repo_id={rid}"
                )


# ════════════════════════════════════════════════════════════════
#  Property 6c — git banner 按 source=="code_extract" 统计非术语条目 (Req 3.4)
#  未修复代码上预期 PASS
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    specs=st.lists(
        st.tuples(
            st.sampled_from(NON_TERM_ENTRY_TYPES),
            st.sampled_from(["code_extract", "manual", "schema", "agent_learn"]),
            st.sampled_from(["proposed", "canonical", "superseded", "rejected"]),
        ),
        min_size=1, max_size=8,
    ),
)
async def test_git_banner_stats_baseline(
    async_session, specs: list[tuple[str, str, str]],
):
    """git banner 统计: total=count(source==git), canonical=count(source==git AND
    status==canonical). 非 git source 不计入.

    EXPECTED OUTCOME on unfixed code: PASS (基线统计行为).
    """
    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(name=f"gb_{uid}", slug=f"gb_{uid}", description="git banner preservation")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        async_session._created_ns_ids.append(ns.id)

        expected_total = 0
        expected_canonical = 0
        for i, (etype, source, status) in enumerate(specs):
            db.add(_mk_ke(ns.id, entry_type=etype, source=source, repo_id=None,
                          content=f"c{i}", status=status))
            if source == "code_extract":
                expected_total += 1
                if status == "canonical":
                    expected_canonical += 1
        await db.commit()

        # 复刻 get_git_ke_summary 的两条统计 query (api/knowledge.py)
        total = (await db.execute(
            select(func.count(KnowledgeEntry.id)).where(
                KnowledgeEntry.namespace_id == ns.id,
                KnowledgeEntry.source == "code_extract",
            )
        )).scalar_one()
        canonical = (await db.execute(
            select(func.count(KnowledgeEntry.id)).where(
                KnowledgeEntry.namespace_id == ns.id,
                KnowledgeEntry.source == "code_extract",
                KnowledgeEntry.status == "canonical",
            )
        )).scalar_one()

    assert total == expected_total, (
        f"git banner total 不符: 期望 {expected_total} 实际 {total}"
    )
    assert canonical == expected_canonical, (
        f"git banner canonical 不符: 期望 {expected_canonical} 实际 {canonical}"
    )


# ════════════════════════════════════════════════════════════════
#  Property 6d — 闸门唯一键去重 + synonyms 合并语义不变 (Req 3.6, 3.9)
#  未修复代码上预期 PASS
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gate_unique_key_dedup_and_synonyms_merge_baseline(
    async_session,
):
    """同 (primary_collection, primary_database, db_type) 三元组 + 词形相交:
    命中同一 active 行 (唯一键去重, Req 3.9) 并合并 synonyms (Req 3.6).

    EXPECTED OUTCOME on unfixed code: PASS (基线闸门语义).
    """
    from app.knowledge.terminology_intake import upsert_terminology_with_validation

    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(name=f"gx_{uid}", slug=f"gx_{uid}", description="gate dedup")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        async_session._created_ns_ids.append(ns.id)
        db.add(DataSource(
            namespace_id=ns.id, db_type="mongodb", database="db_q",
            host="localhost", port=27017, username="", password="",
        ))
        await db.commit()

        base = {
            "term": "商品", "primary_collection": "c_cat", "primary_database": "db_q",
            "db_type": "mongodb", "synonyms": ["货品"], "source_collections": ["c_cat"],
        }
        ke1 = await upsert_terminology_with_validation(
            db, ns_id=ns.id, payload_dict=base, source="manual",
        )
        await db.commit()
        assert ke1 is not None

        # 同三元组、词形相交 (货品) → 命中同一 active 行 + 合并 synonyms
        cand = {**base, "term": "货品", "synonyms": ["货物"]}
        ke2 = await upsert_terminology_with_validation(
            db, ns_id=ns.id, payload_dict=cand, source="manual",
        )
        await db.commit()

        assert ke2 is not None, "同三元组词形相交应命中合并, 返回 active 行 (非 None)"
        assert ke2.id == ke1.id, (
            f"唯一键去重应命中同一 active 行: ke1={ke1.id} ke2={ke2.id}"
        )
        merged = json.loads(ke2.payload)["synonyms"]
        assert "货物" in merged, f"候选 synonyms 应被合并: {merged}"
        # 仅一条 active terminology 行 (无重复)
        active_count = (await db.execute(
            select(func.count(KnowledgeEntry.id)).where(
                KnowledgeEntry.namespace_id == ns.id,
                KnowledgeEntry.entry_type == "terminology",
                KnowledgeEntry.is_superseded.is_(False),
            )
        )).scalar_one()
        assert active_count == 1, f"唯一键去重应仅留一条 active 行, 实际 {active_count}"


# ════════════════════════════════════════════════════════════════
#  Property 6e — 闸门 canonical 保护 + 冲突工单机制不变 (Req 3.5, 3.6)
#  未修复代码上预期 PASS
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gate_canonical_protection_and_conflict_baseline(
    async_session,
):
    """既有 canonical + 候选词形相交 → 落 open TerminologyConflict (冲突工单机制
    保留, Req 3.5), canonical synonyms 不被改动 (canonical 保护, Req 3.6).

    EXPECTED OUTCOME on unfixed code: PASS (基线闸门语义).
    """
    from app.knowledge.terminology_intake import upsert_terminology_with_validation

    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(name=f"gc_{uid}", slug=f"gc_{uid}", description="gate canonical")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        async_session._created_ns_ids.append(ns.id)
        db.add(DataSource(
            namespace_id=ns.id, db_type="mongodb", database="db_q",
            host="localhost", port=27017, username="", password="",
        ))
        await db.commit()

        base = {
            "term": "商品", "primary_collection": "c_cat", "primary_database": "db_q",
            "db_type": "mongodb", "synonyms": ["货品"], "source_collections": ["c_cat"],
        }
        canon = KnowledgeEntry(
            namespace_id=ns.id, entry_type="terminology", source="manual",
            status="canonical", is_superseded=False,
            payload=json.dumps(base, ensure_ascii=False), content="商品",
            raw_input="", evidence_json="{}",
        )
        db.add(canon)
        await db.commit()
        await db.refresh(canon)

        res = await upsert_terminology_with_validation(
            db, ns_id=ns.id,
            payload_dict={**base, "term": "货品", "synonyms": ["货物"]},
            source="code_extract", repo_id=None,
        )
        await db.commit()

        assert res is None, "canonical 不应被合并, 应返 None 落 conflict"
        conflict = (await db.execute(
            select(TerminologyConflict).where(
                TerminologyConflict.namespace_id == ns.id,
                TerminologyConflict.status == "open",
            )
        )).scalar_one_or_none()
        assert conflict is not None, "应落 open TerminologyConflict (冲突工单机制保留)"

        await db.refresh(canon)
        assert json.loads(canon.payload)["synonyms"] == ["货品"], (
            "canonical synonyms 被改动 (canonical 保护被破坏)"
        )


# ════════════════════════════════════════════════════════════════
#  Property 6f — 删 GitRepo 时非术语 KE repo_id SET NULL (Req 3.8)
#  未修复代码上预期 PASS
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    entry_type=st.sampled_from(NON_TERM_ENTRY_TYPES),
    source=st.sampled_from(["code_extract", "manual", "schema", "agent_learn"]),
)
async def test_git_repo_delete_sets_non_terminology_repo_id_null_baseline(
    async_session, entry_type: str, source: str,
):
    """删 GitRepo → 指向它的非术语 KE 存活, repo_id → NULL (ondelete=SET NULL).

    EXPECTED OUTCOME on unfixed code: PASS (基线 FK 行为).
    """
    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(name=f"fk_{uid}", slug=f"fk_{uid}", description="fk set null")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        async_session._created_ns_ids.append(ns.id)

        repo = GitRepo(namespace_id=ns.id, url=f"https://x.invalid/d_{uid}.git", parse_status="parsed")
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

        ke = _mk_ke(ns.id, entry_type=entry_type, source=source, repo_id=repo.id, content="fk")
        db.add(ke)
        await db.commit()
        await db.refresh(ke)
        ke_id = ke.id

        await db.execute(delete(GitRepo).where(GitRepo.id == repo.id))
        await db.commit()

    async with async_session() as db:
        ke = await db.get(KnowledgeEntry, ke_id)
        assert ke is not None, (
            f"删 GitRepo 后非术语 KE 应存活 (SET NULL, 非 CASCADE): ke_id={ke_id}"
        )
        assert ke.repo_id is None, (
            f"删 GitRepo 后非术语 KE repo_id 应置 NULL, 实际 {ke.repo_id}"
        )
