"""Bug condition exploration test — 术语归属错误 (Property 2).

**Validates: Requirements 2.3, 2.4, 2.5**

═══════════════════════════════════════════════════════════════════════════════
  Property 2: Bug Condition — 术语零 repo_id 归属
═══════════════════════════════════════════════════════════════════════════════

  isBugCondition_attribution(KE):
      KE.entry_type == "terminology"  AND  KE.repo_id IS NOT NULL

  期望(修复后)行为:
      经术语抽取写入路径产生的所有 terminology KnowledgeEntry SHALL 满足
      `entry_type="terminology" IMPLIES repo_id IS NULL`, 且对应
      `TerminologyConflict.candidate_repo_id IS NULL`.
      —— 术语只归属 schema/namespace, 不归属单个 repo.

  **CRITICAL**: 本测试在未修复代码上 *预期 FAIL* —— 失败即确认 bug 存在.
  未修复路径 `refresh_terms_for_repo(db, ns_id, repo_id)` →
  `_upsert_terminology_ke(ns_id, repo_id, t)` →
  `upsert_terminology_with_validation(..., source="code_extract", repo_id=repo_id)` 把任取的
  parsed repo id (`repos[0].id`) 一路透传到 `KnowledgeEntry.repo_id` 与
  `TerminologyConflict.candidate_repo_id`, 构造出与实际抽词无关的虚构 repo 关联.

  反例形态: KE.repo_id == <repos[0].id> (而非 NULL);
            TerminologyConflict.candidate_repo_id == <repos[0].id> (而非 NULL).
"""
from __future__ import annotations

import inspect
import json
import uuid

import pytest
import pytest_asyncio
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select

import app.knowledge.terminology_refresher as refresher
from app.db import metadata as _metadata
from app.knowledge.terminology_extractor import ExtractedTerm
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import DataSource, Namespace
from app.models.git_repo import GitRepo
from app.models.terminology_conflict import TerminologyConflict
from app.schemas.knowledge_payload import TerminologyPayload


# db 选择: (database, db_type, collection-前缀) — 覆盖 "多 db_type" 输入域
_DB_CHOICES = [
    ("db_mysql", "mysql"),
    ("db_mongo", "mongodb"),
]


# ════════════════════════════════════════════════════════════════
#  Fixture — ns + mysql/mongodb DataSource + ≥1 parsed GitRepo
#  (有 repo 场景: 未修复代码会把 repos[0].id 透传到术语 KE)
# ════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def ns_with_repo(async_session) -> tuple[int, int, object]:
    """ns + 2 DataSource(mysql db_mysql / mongodb db_mongo) + 1 parsed GitRepo.

    返回 (ns_id, repo_id, async_session_factory).
    """
    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(
            name=f"attr_bug_{uid}", slug=f"attr_bug_{uid}",
            description="术语归属错误复现",
        )
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        db.add(DataSource(
            namespace_id=ns.id, db_type="mysql", database="db_mysql",
            host="localhost", port=3306, username="", password="",
        ))
        db.add(DataSource(
            namespace_id=ns.id, db_type="mongodb", database="db_mongo",
            host="localhost", port=27017, username="", password="",
        ))
        await db.commit()

        repo = GitRepo(
            namespace_id=ns.id,
            url=f"https://example.invalid/attr_{uid}.git",
            parse_status="parsed",
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)
        return ns.id, repo.id, async_session


def _resolve_entry_fn():
    """解析术语抽取入口 — 兼容修复前后命名/签名变更.

    未修复: refresh_terms_for_repo(db, ns_id, repo_id)
    修复后: refresh_namespace_terminology(db, ns_id)
    """
    return (
        getattr(refresher, "refresh_namespace_terminology", None)
        or refresher.refresh_terms_for_repo
    )


async def _run_extraction(db, ns_id: int, repo_id: int):
    """调入口函数, 按其签名决定是否传 repo_id (survive 重命名/去形参)."""
    fn = _resolve_entry_fn()
    if "repo_id" in inspect.signature(fn).parameters:
        return await fn(db, ns_id, repo_id)
    return await fn(db, ns_id)


# ════════════════════════════════════════════════════════════════
#  Property 2a — 术语 KE repo_id IS NULL (未修复代码上预期 FAIL)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(db_indices=st.lists(st.sampled_from([0, 1]), min_size=1, max_size=4))
async def test_terminology_ke_has_null_repo_id(
    ns_with_repo: tuple[int, int, object],
    monkeypatch: pytest.MonkeyPatch,
    db_indices: list[int],
):
    """经术语抽取写入的所有 terminology KE 应满足 repo_id IS NULL.

    生成多 canonical / 多 db_type 的抽词输入, 走真实写入闸门
    (refresh → _upsert_terminology_ke → upsert_terminology_with_validation),
    断言写入的术语 KE 不挂任何 repo_id.

    EXPECTED OUTCOME on unfixed code: FAIL —— KE.repo_id == repos[0].id (= 传入的
    repo_id), 而非 NULL.
    """
    ns_id, repo_id, session_factory = ns_with_repo

    # ── _upsert_terminology_ke 内部开 _metadata.async_session(); 指向测试会话 ──
    monkeypatch.setattr(_metadata, "async_session", session_factory)

    # ── stub extract_terms: 返回确定性 ExtractedTerm (跳过 LLM), 多 db_type ──
    gen_terms: list[ExtractedTerm] = []
    for i, di in enumerate(db_indices):
        database, _db_type = _DB_CHOICES[di]
        coll = f"c_attr_{i}"
        gen_terms.append(ExtractedTerm(
            term=f"实体{i}",
            synonyms=[f"syn{i}"],
            source_canonical_ids=[i + 1],
            source_collections=[coll],
            primary_canonical_id=i + 1,
            primary_collection=coll,
            primary_database=database,
            db_type=_db_type,
        ))

    async def _fake_extract(_canonicals):
        return list(gen_terms), []

    # extract_terms 在 refresher 命名空间内引用
    monkeypatch.setattr(refresher, "extract_terms", _fake_extract, raising=False)

    # _load_canonicals 读 SCO; stub 成非空, 使抽词不被 no_canonicals 跳过
    from app.knowledge.terminology_extractor import CanonicalLite

    async def _fake_load(_db, _ns_id):
        return [CanonicalLite(
            canonical_id=1, collection="c_attr_0", database="db_mysql",
            identity_key="db_mysql.c_attr_0", description="d", purpose_detail="p",
        )]

    monkeypatch.setattr(refresher, "_load_canonicals", _fake_load, raising=False)

    async with session_factory() as db:
        await _run_extraction(db, ns_id, repo_id)

        rows = (await db.execute(
            select(KnowledgeEntry.id, KnowledgeEntry.repo_id).where(
                KnowledgeEntry.namespace_id == ns_id,
                KnowledgeEntry.entry_type == "terminology",
            )
        )).all()

        assert rows, "未写入任何 terminology KE — 写入路径未执行, 无法验证归属"

        offenders = [(ke_id, rid) for ke_id, rid in rows if rid is not None]
        assert not offenders, (
            "术语 KE 被挂上虚构 repo_id (应为 NULL — 术语只归属 schema/namespace). "
            f"传入 repo_id={repo_id}; 违例 [(ke_id, repo_id), ...]={offenders}"
        )

        await db.rollback()


# ════════════════════════════════════════════════════════════════
#  Property 2b — TerminologyConflict.candidate_repo_id IS NULL
#  (未修复代码上预期 FAIL)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_terminology_conflict_has_null_candidate_repo_id(
    ns_with_repo: tuple[int, int, object],
    monkeypatch: pytest.MonkeyPatch,
):
    """术语候选与既有实体冲突落表时, candidate_repo_id 应为 NULL.

    预置一条既有活跃术语 KE(同 primary triple, 词形不相交) → 抽词候选命中唯一键但
    词形无交集 → 走 _record_conflict.

    EXPECTED OUTCOME on unfixed code: FAIL —— TerminologyConflict.candidate_repo_id
    == repos[0].id (= 传入 repo_id), 而非 NULL.
    """
    ns_id, repo_id, session_factory = ns_with_repo

    monkeypatch.setattr(_metadata, "async_session", session_factory)

    primary_coll = "c_conflict"
    primary_db = "db_mysql"

    # ── 预置既有活跃术语 KE (词形: {存量实体, legacy_syn}) ──
    existing_payload = TerminologyPayload(
        term="存量实体",
        primary_collection=primary_coll,
        primary_database=primary_db,
        db_type="mysql",
        synonyms=["legacy_syn"],
    ).model_dump_json()

    async with session_factory() as db:
        db.add(KnowledgeEntry(
            namespace_id=ns_id,
            entry_type="terminology",
            status="proposed",
            tier="normal",
            content="存量实体",
            payload=existing_payload,
            source="code_extract",
            is_superseded=False,
        ))
        await db.commit()

    # ── 抽词候选 (词形: {新实体, new_syn} — 与既有无交集 → 落 conflict) ──
    candidate = ExtractedTerm(
        term="新实体",
        synonyms=["new_syn"],
        source_canonical_ids=[1],
        source_collections=[primary_coll],
        primary_canonical_id=1,
        primary_collection=primary_coll,
        primary_database=primary_db,
        db_type="mysql",
    )

    async def _fake_extract(_canonicals):
        return [candidate], []

    monkeypatch.setattr(refresher, "extract_terms", _fake_extract, raising=False)

    from app.knowledge.terminology_extractor import CanonicalLite

    async def _fake_load(_db, _ns_id):
        return [CanonicalLite(
            canonical_id=1, collection=primary_coll, database=primary_db,
            identity_key=f"{primary_db}.{primary_coll}", description="d",
            purpose_detail="p",
        )]

    monkeypatch.setattr(refresher, "_load_canonicals", _fake_load, raising=False)

    async with session_factory() as db:
        await _run_extraction(db, ns_id, repo_id)

        conflicts = (await db.execute(
            select(
                TerminologyConflict.id, TerminologyConflict.candidate_repo_id
            ).where(TerminologyConflict.namespace_id == ns_id)
        )).all()

        assert conflicts, (
            "未落任何 TerminologyConflict — 冲突路径未触发, 无法验证候选归属"
        )

        offenders = [(cid, rid) for cid, rid in conflicts if rid is not None]
        assert not offenders, (
            "TerminologyConflict.candidate_repo_id 被挂虚构 repo_id (应为 NULL — "
            "术语冲突候选不归属单个 repo). "
            f"传入 repo_id={repo_id}; 违例 [(conflict_id, candidate_repo_id), ...]={offenders}"
        )

        await db.rollback()
