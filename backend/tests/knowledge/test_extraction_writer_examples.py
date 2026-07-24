"""Tests for _write_business_examples — Task 3: content=question + 单步 final_query_plan."""
import json
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import event

from app.models.base import Base
from app.models.git_repo import GitRepo
from app.models.namespace import DataSource, Namespace
from app.models.knowledge_entry import KnowledgeEntry

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession) -> tuple[int, int]:
    """Create namespace + datasource + repo, return (ns_id, repo_id)."""
    slug = f"test_ex_{uuid.uuid4().hex[:8]}"
    ns = Namespace(name=slug, slug=slug, description="example writer test")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    ds = DataSource(
        namespace_id=ns.id, db_type="mongodb", database="shop",
        host="localhost", port=27017, username="", password="",
    )
    db_session.add(ds)
    await db_session.commit()

    repo = GitRepo(namespace_id=ns.id, url="https://example.invalid/ex.git")
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return ns.id, repo.id


@pytest.mark.asyncio
async def test_write_business_examples_wraps_single_step_plan(db_session, seeded):
    """code_extract example: content=question, payload.final_query_plan 单步含 native query."""
    from app.knowledge.extraction_writer import _write_business_examples

    ns_id, repo_id = seeded
    ex = {
        "question": "查所有在售商品",
        "query": {"filter": {"active": True}},
        "operation": "filter",
        "tables": ["products"],
    }
    coll_to_db = {"products": ("mongodb", "shop")}
    n = await _write_business_examples(db_session, ns_id, repo_id, [ex], coll_to_db)
    assert n == 1

    rows = (await db_session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "example",
        )
    )).scalars().all()
    assert len(rows) == 1

    ke = rows[0]
    assert ke.content == "查所有在售商品"
    payload = json.loads(ke.payload)
    assert payload["question_pattern"] == "查所有在售商品"
    plan = payload["final_query_plan"]
    assert plan["steps"][0]["db_type"] == "mongodb"
    assert plan["steps"][0]["database"] == "shop"
    assert plan["steps"][0]["collection"] == "products"
    assert plan["steps"][0]["operation"] == "filter"
    assert plan["steps"][0]["query"] == {"filter": {"active": True}}


@pytest.mark.asyncio
async def test_write_business_examples_skips_missing_question_or_query(db_session, seeded):
    """缺 question 或 query 或 tables 的 ex 跳过, 不写 KE."""
    from app.knowledge.extraction_writer import _write_business_examples

    ns_id, repo_id = seeded
    bad = [
        {"query": {"sql": "SELECT 1"}, "tables": ["orders"]},  # 缺 question
        {"question": "q", "tables": ["orders"]},               # 缺 query
        {"question": "q", "query": {"sql": "SELECT 1"}},       # 缺 tables
    ]
    n = await _write_business_examples(db_session, ns_id, repo_id, bad, {"orders": ("mysql", "shop")})
    assert n == 0
