"""knowledge 测试 fixtures — SAVEPOINT rollback 隔离.

提供两类共享 fixture:
- db_session / async_session: SAVEPOINT rollback 隔离 (测试结束数据全部回滚)
- chroma_isolated: 临时 ChromaDB 持久化目录 + registry 单例重置
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

from app.models.base import Base
from app.models.namespace import DataSource, Namespace
from app.models.git_repo import GitRepo
from app.models.knowledge_entry import KnowledgeEntry
from app.models.terminology_conflict import TerminologyConflict
from tests._db_schema_sync import prepare_test_schema

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


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
    """Per-test session with SAVEPOINT rollback — 测试结束后数据全部回滚.

    使用 SQLAlchemy 2.0 join_transaction_mode="create_savepoint": session 内的
    commit()/begin_nested() 都映射为 SAVEPOINT, 外层 trans.rollback() 整体回滚。
    取代旧的手写 after_transaction_end 监听器 (与 begin_nested 嵌套时在新版
    SQLAlchemy 下抛 "Can't operate on closed transaction")。
    """
    async with _engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        yield session
        await session.close()
        await trans.rollback()


@pytest.fixture
def async_session(db_session):
    """sessionmaker 形态别名: 返回一个 callable async context manager, 内部复用 db_session.

    让 `async with async_session() as session: ...` 语法继续工作,
    但底层走 SAVEPOINT rollback 隔离.
    """
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


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


@pytest.fixture
def real_chromadb(chroma_isolated):
    """`chroma_isolated` 别名 — 跨测试套件命名一致."""
    yield


# ════════════════════════════════════════════
#  Phase 1b+ — seed fixtures
# ════════════════════════════════════════════


@pytest_asyncio.fixture
async def seeded_ns_with_mongo_ds(db_session) -> tuple[int, int]:
    """1 Namespace(slug='test_ns') + 1 mongodb DataSource + 1 GitRepo.
    返回 (ns_id, repo_id).
    """
    ns = Namespace(name="test_ns", slug="test_ns", description="phase1b")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    ds = DataSource(
        namespace_id=ns.id,
        db_type="mongodb",
        database="db_q",
        host="localhost",
        port=27017,
        username="",
        password="",
    )
    db_session.add(ds)
    await db_session.commit()
    repo = GitRepo(namespace_id=ns.id, url="https://example.invalid/repo.git")
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return ns.id, repo.id


# ════════════════════════════════════════════
#  Phase 2 共享 seed fixtures
# ════════════════════════════════════════════


@pytest_asyncio.fixture
async def seeded_repo_with_mixed_kes(db_session) -> tuple[int, int]:
    """ns + repo + 8 KE: 2 canonical / 2 proposed / 2 superseded / 2 rejected."""
    ns = Namespace(name="ns_purge", slug="ns_purge", description="phase2-purge")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    repo = GitRepo(namespace_id=ns.id, url="https://example.invalid/purge.git")
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)

    for status in ("canonical", "canonical", "proposed", "proposed",
                   "superseded", "superseded", "rejected", "rejected"):
        db_session.add(KnowledgeEntry(
            namespace_id=ns.id,
            entry_type="schema_summary",
            status=status,
            tier="normal",
            content=f"ke-{status}",
            source="code_extract",
            repo_id=repo.id,
        ))
    await db_session.commit()
    return ns.id, repo.id


@pytest_asyncio.fixture
async def seeded_repo_with_open_conflicts(db_session) -> tuple[int, int]:
    """ns + repo + 1 KE + open TerminologyConflict + 1 resolved."""
    ns = Namespace(name="ns_conf", slug="ns_conf", description="phase2-conf")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    repo = GitRepo(namespace_id=ns.id, url="https://example.invalid/conf.git")
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)

    anchor = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        status="canonical",
        tier="normal",
        content="anchor",
        source="manual",
    )
    db_session.add(anchor)
    await db_session.commit()
    await db_session.refresh(anchor)

    db_session.add(TerminologyConflict(
        namespace_id=ns.id,
        existing_entry_id=anchor.id,
        candidate_payload="{}",
        candidate_source="code_extract",
        status="open",
    ))
    db_session.add(TerminologyConflict(
        namespace_id=ns.id,
        existing_entry_id=anchor.id,
        candidate_payload="{}",
        candidate_source="code_extract",
        status="resolved",
    ))
    await db_session.commit()
    return ns.id, repo.id


# ════════════════════════════════════════════
#  Phase 2 — fake_llm fixture (mock chat_completion)
# ════════════════════════════════════════════


class FakeLLM:
    """Queue-based LLM mock: queue_response → next chat_completion returns JSON."""

    def __init__(self):
        self._responses: list[str] = []

    def queue_response(self, data: dict | str) -> None:
        import json as _json
        if isinstance(data, dict):
            self._responses.append(_json.dumps(data, ensure_ascii=False))
        else:
            self._responses.append(data)

    def __call__(self, *args, **kwargs) -> str:
        if not self._responses:
            raise RuntimeError("FakeLLM: no queued responses")
        return self._responses.pop(0)


@pytest.fixture
def fake_llm():
    """Patch chat_completion globally with a queue-based fake."""
    fake = FakeLLM()
    with patch("app.engine.llm.chat_completion", side_effect=fake):
        yield fake
