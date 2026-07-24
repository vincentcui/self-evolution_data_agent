"""
test_schema_migrations — 启动期幂等 ALTER TABLE 兜底测试
"""

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.schema_migrations import ensure_knowledge_entry_columns, run_all
from app.models.base import Base

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


@pytest_asyncio.fixture
async def fresh_engine():
    """每个测试独享的 PostgreSQL 引擎，已建全表"""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_columns_adds_missing(fresh_engine):
    """模拟旧版本数据库（缺少 tier 列），补全后列应存在"""
    # 删掉 tier 列来模拟老版本表
    async with fresh_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE knowledge_entries DROP COLUMN tier"))

    await ensure_knowledge_entry_columns(fresh_engine)

    async with fresh_engine.connect() as conn:
        cols = {
            row[0]
            for row in (
                await conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'knowledge_entries'"
                ))
            ).all()
        }

    assert "tier" in cols
    assert "description" in cols
    assert "raw_input" in cols
    assert "refined_at" in cols
    assert "is_superseded" in cols


@pytest.mark.asyncio
async def test_ensure_columns_is_idempotent(fresh_engine):
    """连续调用两次不应抛出任何异常"""
    await ensure_knowledge_entry_columns(fresh_engine)
    await ensure_knowledge_entry_columns(fresh_engine)   # second call must be silent no-op


@pytest_asyncio.fixture
async def legacy_engine():
    """Simulate pre-Task-1.1 knowledge_entries: only old columns exist."""
    e = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with e.begin() as conn:
        # Drop the table if it exists and recreate with only legacy columns
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_entries CASCADE"))
        await conn.execute(text("""
            CREATE TABLE knowledge_entries (
                id SERIAL PRIMARY KEY,
                namespace_id INTEGER,
                entry_type VARCHAR(20) NOT NULL,
                content TEXT NOT NULL,
                source VARCHAR(20) NOT NULL DEFAULT 'manual',
                repo_id INTEGER,
                created_at TIMESTAMP
            )
        """))
    yield e
    # Restore full schema for other tests
    async with e.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_entries CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
    await e.dispose()


@pytest.mark.asyncio
async def test_ensure_columns_adds_all_when_all_missing(legacy_engine):
    """All 5 new columns missing on a legacy table — migration adds them all."""
    await ensure_knowledge_entry_columns(legacy_engine)
    async with legacy_engine.connect() as conn:
        cols = {row[0] for row in (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'knowledge_entries'"
        ))).all()}
    assert {"tier", "raw_input", "description", "is_superseded", "refined_at"} <= cols


@pytest.mark.asyncio
async def test_run_all_adds_conflict_scope_before_creating_scope_index(fresh_engine):
    """旧 conflict 表缺 conflict_scope 时，启动迁移不应在建索引阶段失败。"""
    async with fresh_engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS uq_one_open_conflict_per_field"))
        await conn.execute(text(
            "ALTER TABLE schema_canonical_conflicts DROP COLUMN conflict_scope"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX uq_one_open_conflict_per_field "
            "ON schema_canonical_conflicts"
            "(namespace_id, db_type, database, target, field_path, candidate_kind) "
            "WHERE status = 'open'"
        ))

    await run_all(fresh_engine)

    async with fresh_engine.connect() as conn:
        columns = {row[0] for row in (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = 'schema_canonical_conflicts'"
        ))).all()}
        indexdef = (await conn.execute(text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'uq_one_open_conflict_per_field'"
        ))).scalar_one()

    assert "conflict_scope" in columns
    assert "conflict_scope" in indexdef
