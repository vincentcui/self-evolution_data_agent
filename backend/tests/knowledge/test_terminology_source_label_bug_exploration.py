"""Bug condition exploration test — 术语 source 标签错配 (Property 3).

**Validates: Requirements 2.6, 3.7**

═══════════════════════════════════════════════════════════════════════════════
  Property 3: Bug Condition — source 反映 schema 自省机制
═══════════════════════════════════════════════════════════════════════════════

  isBugCondition_source(KE):
      KE.entry_type == "terminology"  AND  KE.source == "code_extract"

  期望(修复后)行为:
      经术语抽取写入路径产生的所有 terminology KnowledgeEntry SHALL 满足
      `source == "schema"` —— 无论手动刷新还是 trainer stage 触发, 无论 namespace
      有无 git repo. 术语 source 反映"抽取机制 (schema 自省)", 判别依据是抽取机制
      而非 namespace 偶然有无 repo.

  **CRITICAL**: 本测试在未修复代码上 *预期 FAIL* —— 失败即确认 bug 存在.
  未修复代码有两条术语写入路径, 均硬编码 source="code_extract":
    1. 手动刷新 / trainer stage 共用: refresh_terms_for_repo →
       _upsert_terminology_ke → upsert_terminology_with_validation(..., source="code_extract")
    2. 潜伏路径 (extraction_writer): _write_terminology_ke →
       upsert_terminology_with_validation(..., source="code_extract")
  且 `Source` Literal 无 "schema" 成员.

  反例形态: KE.source == "code_extract" (而非 "schema").

  覆盖:
    - 触发路径①(手动刷新 / trainer stage 共用写入内核): test_terminology_ke_source_is_schema_*
    - 触发路径②(extraction_writer 潜伏路径): test_extraction_writer_terminology_source_is_schema
    - 有 repo 的 ns / 无 repo 的 ns: 两个 fixture 分别覆盖
"""
from __future__ import annotations

import inspect
import uuid

import pytest
import pytest_asyncio
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select

import app.knowledge.terminology_refresher as refresher
from app.db import metadata as _metadata
from app.knowledge.terminology_extractor import CanonicalLite, ExtractedTerm
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import DataSource, Namespace
from app.models.git_repo import GitRepo


# db 选择: (database, db_type) — 覆盖 "多 db_type" 输入域
_DB_CHOICES = [
    ("db_mysql", "mysql"),
    ("db_mongo", "mongodb"),
]


# ════════════════════════════════════════════════════════════════
#  入口解析 — 兼容修复前后命名/签名变更
# ════════════════════════════════════════════════════════════════


def _resolve_entry_fn():
    """解析术语抽取入口 — 兼容修复前后命名/签名变更.

    未修复: refresh_terms_for_repo(db, ns_id, repo_id)
    修复后: refresh_namespace_terminology(db, ns_id)
    """
    return (
        getattr(refresher, "refresh_namespace_terminology", None)
        or refresher.refresh_terms_for_repo
    )


async def _run_extraction(db, ns_id: int, repo_id: int | None):
    """调入口函数, 按其签名决定是否传 repo_id (survive 重命名/去形参)."""
    fn = _resolve_entry_fn()
    if "repo_id" in inspect.signature(fn).parameters:
        return await fn(db, ns_id, repo_id)
    return await fn(db, ns_id)


def _stub_extraction_kernel(
    monkeypatch: pytest.MonkeyPatch, db_indices: list[int]
) -> None:
    """stub extract_terms + _load_canonicals — 跳过 LLM, 走真实写入闸门."""
    gen_terms: list[ExtractedTerm] = []
    for i, di in enumerate(db_indices):
        database, _db_type = _DB_CHOICES[di]
        coll = f"c_src_{i}"
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

    async def _fake_load(_db, _ns_id):
        return [CanonicalLite(
            canonical_id=1, collection="c_src_0", database="db_mysql",
            identity_key="db_mysql.c_src_0", description="d", purpose_detail="p",
        )]

    monkeypatch.setattr(refresher, "extract_terms", _fake_extract, raising=False)
    monkeypatch.setattr(refresher, "_load_canonicals", _fake_load, raising=False)


# ════════════════════════════════════════════════════════════════
#  Fixtures — ns + 两个 DataSource(mysql/mongodb), 有/无 parsed GitRepo
# ════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def ns_with_repo(async_session) -> tuple[int, int, object]:
    """ns + 2 DataSource(db_mysql/db_mongo) + 1 parsed GitRepo.

    返回 (ns_id, repo_id, async_session_factory).
    """
    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(
            name=f"src_repo_{uid}", slug=f"src_repo_{uid}",
            description="source 标签错配复现 — 有 repo",
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
            url=f"https://example.invalid/src_{uid}.git",
            parse_status="parsed",
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)
        return ns.id, repo.id, async_session


@pytest_asyncio.fixture
async def ns_without_repo(async_session) -> tuple[int, None, object]:
    """ns + 2 DataSource(db_mysql/db_mongo), 无任何 GitRepo (直连自省建库).

    返回 (ns_id, None, async_session_factory).
    """
    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(
            name=f"src_norepo_{uid}", slug=f"src_norepo_{uid}",
            description="source 标签错配复现 — 无 repo 直连自省建库",
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
        return ns.id, None, async_session


# ════════════════════════════════════════════════════════════════
#  Property 3a — 有 repo 的 ns: 术语 KE source == "schema"
#  (手动刷新 / trainer stage 共用写入内核; 未修复代码上预期 FAIL)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(db_indices=st.lists(st.sampled_from([0, 1]), min_size=1, max_size=4))
async def test_terminology_ke_source_is_schema_with_repo(
    ns_with_repo: tuple[int, int, object],
    monkeypatch: pytest.MonkeyPatch,
    db_indices: list[int],
):
    """有 parsed repo 的 ns 抽词写入的所有术语 KE 应满足 source == "schema".

    refresh_terms_for_repo / refresh_namespace_terminology 是手动刷新与 trainer
    stage 共用的术语写入内核, 故该断言同时覆盖两条触发路径.

    EXPECTED OUTCOME on unfixed code: FAIL —— _upsert_terminology_ke 硬编码
    source="code_extract", 写入的术语 KE source == "code_extract" 而非 "schema".
    """
    ns_id, repo_id, session_factory = ns_with_repo

    monkeypatch.setattr(_metadata, "async_session", session_factory)
    _stub_extraction_kernel(monkeypatch, db_indices)

    async with session_factory() as db:
        await _run_extraction(db, ns_id, repo_id)

        rows = (await db.execute(
            select(KnowledgeEntry.id, KnowledgeEntry.source).where(
                KnowledgeEntry.namespace_id == ns_id,
                KnowledgeEntry.entry_type == "terminology",
            )
        )).all()

        assert rows, "未写入任何 terminology KE — 写入路径未执行, 无法验证 source 标签"

        offenders = [(ke_id, src) for ke_id, src in rows if src != "schema"]
        assert not offenders, (
            "术语 KE source 未反映 schema 自省机制 (应为 'schema'). "
            "术语 source 应反映抽取机制而非 namespace 偶然有无 repo. "
            f"违例 [(ke_id, source), ...]={offenders}"
        )

        await db.rollback()


# ════════════════════════════════════════════════════════════════
#  Property 3b — 无 repo 的 ns: 术语 KE source == "schema"
#  (直连自省建库; 未修复代码上预期 FAIL)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(db_indices=st.lists(st.sampled_from([0, 1]), min_size=1, max_size=4))
async def test_terminology_ke_source_is_schema_without_repo(
    ns_without_repo: tuple[int, None, object],
    monkeypatch: pytest.MonkeyPatch,
    db_indices: list[int],
):
    """无 git repo 的 ns 抽词写入的所有术语 KE 仍应满足 source == "schema".

    source 标签反映抽取机制 (schema 自省), 与 namespace 有无 repo 无关 ——
    未修复路径传 repo_id=None 时 source 仍硬编码 "code_extract", 印证标签与真实抽取机制脱钩.

    EXPECTED OUTCOME on unfixed code: FAIL —— 术语 KE source == "code_extract" 而非 "schema".
    """
    ns_id, _none, session_factory = ns_without_repo

    monkeypatch.setattr(_metadata, "async_session", session_factory)
    _stub_extraction_kernel(monkeypatch, db_indices)

    async with session_factory() as db:
        # 无 repo: 未修复签名要求 repo_id 时传 None (FK 允许 NULL), 修复后签名无此形参
        await _run_extraction(db, ns_id, None)

        rows = (await db.execute(
            select(KnowledgeEntry.id, KnowledgeEntry.source).where(
                KnowledgeEntry.namespace_id == ns_id,
                KnowledgeEntry.entry_type == "terminology",
            )
        )).all()

        assert rows, "未写入任何 terminology KE — 写入路径未执行, 无法验证 source 标签"

        offenders = [(ke_id, src) for ke_id, src in rows if src != "schema"]
        assert not offenders, (
            "无 repo 的 ns 写入的术语 KE source 未反映 schema 自省机制 (应为 'schema'). "
            f"违例 [(ke_id, source), ...]={offenders}"
        )

        await db.rollback()


# ════════════════════════════════════════════════════════════════
#  Property 3c — 潜伏路径 extraction_writer: 术语 KE source == "schema"
#  (第二条术语写入路径; 未修复代码上预期 FAIL)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_extraction_writer_terminology_source_is_schema(
    ns_with_repo: tuple[int, int, object],
    monkeypatch: pytest.MonkeyPatch,
):
    """extraction_writer._write_terminology_ke 写入的术语 KE 应满足 source == "schema".

    这是第二条 (当前休眠但潜伏) 术语写入路径, 同样硬编码 source="code_extract".
    为"彻底不留隐患", 修复需同步纠正该路径标签.

    EXPECTED OUTCOME on unfixed code: FAIL —— source == "code_extract" 而非 "schema".
    """
    ns_id, repo_id, session_factory = ns_with_repo

    from app.knowledge import extraction_writer

    term = {
        "term": "潜伏实体",
        "primary_collection": "c_src_writer",
        "primary_database": "db_mysql",
        "db_type": "mysql",
        "synonyms": ["writer_syn"],
        "source_collections": ["c_src_writer"],
    }

    async with session_factory() as db:
        created = await extraction_writer._write_terminology_ke(
            db, ns_id, repo_id, term, repo_name="src_writer",
        )
        await db.flush()

        assert created, "extraction_writer 未写入术语 KE — 无法验证 source 标签"

        rows = (await db.execute(
            select(KnowledgeEntry.id, KnowledgeEntry.source).where(
                KnowledgeEntry.namespace_id == ns_id,
                KnowledgeEntry.entry_type == "terminology",
            )
        )).all()

        offenders = [(ke_id, src) for ke_id, src in rows if src != "schema"]
        assert not offenders, (
            "extraction_writer 写入的术语 KE source 未反映 schema 自省机制 (应为 'schema'). "
            f"违例 [(ke_id, source), ...]={offenders}"
        )

        await db.rollback()
