"""agent_loop 测试 fixtures — SAVEPOINT rollback 隔离.

Stage 4 Task 3: lookup_knowledge / save_knowledge tool 需要真正的 DB + 向量集合,
不用 mock 掩盖集成问题 (符合 knowledge 层测试铁律).

Stage 4 Task 5 follow-up (M3): 抽 Mongo Motor 测试夹具 (FakeMongoCursor /
make_fake_mongo_db) 到这里, 避免 probe/prequery 双份维护.
"""

import os
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

from app.engine import agent_loop as al
from tests._db_schema_sync import prepare_test_schema

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


# ════════════════════════════════════════════
#  Motor mock 夹具 — Stage 4 Task 4+ 共用
# ════════════════════════════════════════════

class FakeMongoCursor:
    """Async iterable cursor for Motor mocks (test helper).

    Stage 4 Tasks 4+ Mongo tools use AsyncMock for Motor (real Motor too costly in CI).
    """

    def __init__(self, docs):
        self.docs = docs
        self._limit = None

    def limit(self, n):
        self._limit = n
        return self

    def __aiter__(self):
        async def _gen():
            for d in self.docs[: self._limit or len(self.docs)]:
                yield d
        return _gen()


def make_fake_mongo_db(coll_to_docs: dict[str, list[dict]]):
    """Construct fake AsyncIOMotorDatabase keyed by collection name."""
    fake_db = MagicMock()

    def _get_coll(name):
        coll = MagicMock()
        coll.find = MagicMock(return_value=FakeMongoCursor(coll_to_docs.get(name, [])))
        return coll

    fake_db.__getitem__.side_effect = _get_coll
    return fake_db


# ════════════════════════════════════════════
#  PostgreSQL SAVEPOINT rollback 隔离
# ════════════════════════════════════════════


@pytest_asyncio.fixture
async def _engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    await prepare_test_schema(engine)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session with SAVEPOINT rollback — 测试结束后数据全部回滚."""
    async with _engine.connect() as conn:
        trans = await conn.begin()
        await conn.begin_nested()

        session = AsyncSession(bind=conn, expire_on_commit=False)

        @event.listens_for(session.sync_session, "after_transaction_end")
        def _restart_savepoint(sync_session, transaction):
            if transaction.nested and not transaction._parent.nested:
                sync_session.begin_nested()

        yield session

        await session.close()
        await trans.rollback()


# ════════════════════════════════════════════
#  ChromaDB 隔离夹具
# ════════════════════════════════════════════


@pytest.fixture
def chroma_isolated(tmp_path, monkeypatch):
    """每个测试独立 ChromaDB 持久化目录, 重置 registry 单例."""
    monkeypatch.setattr(
        "app.config.settings.chroma_persist_dir", str(tmp_path / "chroma")
    )
    import app.engine.registry as reg

    reg._chroma_client = None
    yield
    reg._chroma_client = None


# ════════════════════════════════════════════
#  Stage 5 G5 夹具 — fake_llm_end_turn
# ════════════════════════════════════════════

@pytest.fixture
def fake_llm_end_turn():
    """立即返回 text="hello" end_turn 的最简 LLM 桩, Stage 5 G5 验证用."""
    async def _llm(**kwargs):
        from app.engine.llm import ToolUseResponse
        return ToolUseResponse(text="hello", tool_calls=[], stop_reason="end_turn", usage={})
    return _llm


@pytest.fixture
def relax_quota(monkeypatch):
    """放宽 agent_loop 配额/阈值, 让 Forced_Clarify / dead_loop 等行为可观测.

    集中在 conftest 供 test_forced_clarify*.py 共享 (避免跨测试模块 import fixture).
    """
    monkeypatch.setattr(al.settings, "agent_loop_max_exploratory_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_decisive_calls", 100)
    monkeypatch.setattr(al.settings, "agent_loop_max_total_iterations", 100)
    monkeypatch.setattr(al.settings, "agent_loop_dead_loop_window", 3)
    monkeypatch.setattr(al.settings, "agent_loop_error_class_window_size", 5)
    monkeypatch.setattr(al.settings, "agent_loop_error_class_threshold", 2)
    monkeypatch.setattr(al.settings, "agent_loop_max_forced_clarify_per_class", 1)
