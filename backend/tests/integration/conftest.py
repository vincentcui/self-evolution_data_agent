"""integration 测试 fixtures — PostgreSQL + 真实 ChromaDB.

提供 sessionmaker 形态的 ``async_session`` (与 tests/scripts/conftest.py 同形态),
配合 ``real_chromadb`` 隔离持久化目录, 以及 ``seeded_legacy_terminology_kes``
同时种 PostgreSQL KE 行 + ChromaDB 向量, 模拟历史错配场景, 用于跨脚本一致性回归.
"""

import asyncio
import json
import os

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.models import DataSource, GitRepo, Namespace
from app.models.base import Base
from app.models.knowledge_entry import KnowledgeEntry
from tests._db_schema_sync import prepare_test_schema

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


# ════════════════════════════════════════════
#  pytest-asyncio 全局 event loop
# ════════════════════════════════════════════
@pytest.fixture(scope="session")
def event_loop():  # noqa: D401
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ════════════════════════════════════════════
#  async_session — sessionmaker 形态, 共享 PostgreSQL
# ════════════════════════════════════════════
@pytest_asyncio.fixture
async def async_session(monkeypatch):
    """PostgreSQL sessionmaker + ``app.db.metadata.async_session`` 替身.

    脚本侧 ``verify_db_chromadb_consistency`` 通过 ``app.db.metadata.async_session``
    打开会话; 这里把 module-level sessionmaker monkeypatch 到测试用 engine, 让脚本
    与测试看同一份数据库.
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    await prepare_test_schema(engine)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # ── 让生产 async_session sessionmaker 指向测试 engine ──────
    monkeypatch.setattr("app.db.metadata.async_session", factory)
    yield factory
    await engine.dispose()


# ════════════════════════════════════════════
#  real_chromadb — 真 PersistentClient, 隔离目录, 重置单例
# ════════════════════════════════════════════
@pytest.fixture
def real_chromadb(tmp_path, monkeypatch):
    """每测试独立 chroma_persist_dir, 强制重置 registry._chroma_client.

    ChromaDB PersistentClient 是进程级单例, 跨测试共享会污染集合状态.
    yield 前后双重置, 防 cache 漏入下个 case.
    """
    monkeypatch.setattr(
        "app.config.settings.chroma_persist_dir", str(tmp_path / "chroma")
    )
    import app.engine.registry as reg

    reg._chroma_client = None
    yield
    reg._chroma_client = None


# ════════════════════════════════════════════
#  seeded_legacy_terminology_kes — SQLite + ChromaDB 双种入口
# ════════════════════════════════════════════
@pytest_asyncio.fixture
async def seeded_legacy_terminology_kes(async_session, real_chromadb):
    """种 3 条历史错配 ``terminology`` KE + 对应 ChromaDB 向量.

    设计要点:
    - status='proposed' 入 SQLite, 保证后续 verify 不把 KE 当 canonical 漏算 sqlite_only
      (verify 仅扫描 status=canonical), 让我们专注验证 chromadb_only 是否被清掉.
    - ChromaDB 向量必须直接调 raw 客户端 upsert, 因为 ``upsert_knowledge_entry`` 会跳
      过非 canonical, 而我们恰恰要模拟 ``relabel`` 之前数据库里就有"错配旧向量"的状态.
    - 3 条 content 走 monkeypatch ``_llm_classify`` 的 "枚举/关联/合法术语" 关键词路由
      (复用 ``tests/scripts/test_relabel_legacy_terminology.py`` 的 stub 模式), 保证
      relabel 真改 entry_type → 真触发 ChromaDB delete.
    """
    from app.engine.registry import get_knowledge_collection

    async with async_session() as db:
        ns = Namespace(name="legacy_test", slug="legacy_test", description="phase0 task0.3")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        kes = [
            KnowledgeEntry(
                namespace_id=ns.id, entry_type="terminology", source="code_extract",
                status="proposed", is_superseded=False,
                payload=json.dumps({"term": "订单状态枚举: 0=draft, 1=published"}),
                content="订单状态枚举",
            ),
            KnowledgeEntry(
                namespace_id=ns.id, entry_type="terminology", source="code_extract",
                status="proposed", is_superseded=False,
                payload=json.dumps({"term": "c_product.categoryId 关联 c_category._id"}),
                content="c_product 关联 c_category",
            ),
            KnowledgeEntry(
                namespace_id=ns.id, entry_type="terminology", source="code_extract",
                status="proposed", is_superseded=False,
                payload=json.dumps({"term": "订单状态枚举别名"}),
                content="另一条枚举描述",
            ),
        ]
        for k in kes:
            db.add(k)
        await db.commit()
        for k in kes:
            await db.refresh(k)
        entry_ids = [k.id for k in kes]
        ns_slug = ns.slug

    # ── ChromaDB 直接 raw upsert, 绕过 status 守门员 ─────────────
    coll = get_knowledge_collection(ns_slug)
    coll.upsert(
        ids=[f"ke_{eid}" for eid in entry_ids],
        documents=[f"legacy doc {eid}" for eid in entry_ids],
        metadatas=[
            {
                "tier": "normal",
                "entry_type": "terminology",
                "status": "canonical",
                "entry_id": eid,
                "namespace_id": -1,
            }
            for eid in entry_ids
        ],
    )

    return entry_ids


# ════════════════════════════════════════════
#  schema-style fixtures (for multi-repo race tests)
# ════════════════════════════════════════════


@pytest_asyncio.fixture
async def test_engine() -> AsyncEngine:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    await prepare_test_schema(engine)
    yield engine  # type: ignore[misc]
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine: AsyncEngine) -> AsyncSession:
    """事务回滚隔离 — 测试内 commit() / begin_nested() 仅落到 SAVEPOINT, 结束 rollback, 零残留.

    用 SQLAlchemy 2.0 的 join_transaction_mode="create_savepoint": session 绑定到
    已开启外层事务的连接, 其后所有 commit()/begin_nested() 自动在 SAVEPOINT 层面操作,
    与业务代码自用的 begin_nested() 兼容。测试结束统一 rollback, 不向远程库提交数据。
    """
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        yield session  # type: ignore[misc]
        await session.close()
        await trans.rollback()


@pytest_asyncio.fixture
async def namespace_factory(test_session: AsyncSession):
    counter = {"n": 0}

    async def _make(slug: str | None = None) -> Namespace:
        counter["n"] += 1
        ns = Namespace(slug=slug or f"test_ns_{counter['n']}", name=f"Test NS {counter['n']}")
        test_session.add(ns)
        await test_session.flush()
        return ns

    return _make


@pytest_asyncio.fixture
async def repo_factory(test_session: AsyncSession):
    counter = {"n": 0}

    async def _make(*, ns_id: int, url: str | None = None) -> GitRepo:
        counter["n"] += 1
        repo = GitRepo(
            namespace_id=ns_id,
            url=url or f"https://example.com/repo-{counter['n']}",
            branch="main",
            parse_status="parsed",
        )
        test_session.add(repo)
        await test_session.flush()
        return repo

    return _make


@pytest_asyncio.fixture
async def datasource_factory(test_session: AsyncSession):
    counter = {"n": 0}

    async def _make(*, ns_id: int) -> DataSource:
        counter["n"] += 1
        ds = DataSource(
            namespace_id=ns_id,
            db_type="mysql",
            host="localhost",
            port=3306,
            database=f"db_{counter['n']}",
            username="x",
            password="y",
        )
        test_session.add(ds)
        await test_session.flush()
        return ds

    return _make
