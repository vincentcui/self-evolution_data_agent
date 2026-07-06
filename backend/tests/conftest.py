"""
conftest — 共享 fixture：PostgreSQL 测试数据库、模型表
"""

import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests._db_schema_sync import prepare_test_schema

# 从 backend/.env.test 加载测试配置 (含 IS_TEST_DATABASE_URL)。
# 独立于应用 .env: pydantic Settings 只读 .env, 不读 .env.test, 避免 IS_ 前缀冲突。
# override=False: CI 经 workflow env 注入的值优先, 不被 .env.test 覆盖。
load_dotenv(Path(__file__).resolve().parents[1] / ".env.test")

# 测试数据库 URL — 从环境变量读取，默认使用远程测试 RDS
TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)
os.environ.setdefault("IS_METADATA_DB_URL", TEST_DATABASE_URL)


async def _ensure_postgres_database_exists(database_url: str) -> None:
    """Create the local PostgreSQL test database when only the server exists."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql" or not url.database:
        return

    admin_url = url.set(database=os.environ.get("IS_TEST_ADMIN_DATABASE", "postgres"))
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            )
            if exists:
                return
            escaped_database = url.database.replace('"', '""')
            await conn.execute(text(f'CREATE DATABASE "{escaped_database}"'))
    finally:
        await admin_engine.dispose()


# ── pytest-asyncio 全局配置 ──
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def _engine():
    """Session-scoped async engine for PostgreSQL test database."""
    await _ensure_postgres_database_exists(TEST_DATABASE_URL)
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    # 连接级时区设置 — 与生产一致
    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    # 建表 + 列对齐 + 生产 migrations (单点收口, 见 _db_schema_sync.prepare_test_schema)
    await prepare_test_schema(engine)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(_engine):
    """Per-test PostgreSQL session with transaction rollback isolation.

    每个测试在一个事务内运行，测试结束后回滚，保证测试间隔离且速度快。
    使用 SAVEPOINT 嵌套事务，使得测试内的 commit() 调用不会真正提交。
    """
    async with _engine.connect() as conn:
        trans = await conn.begin()
        # 创建嵌套事务（SAVEPOINT），使得 session.commit() 只提交到 savepoint
        await conn.begin_nested()

        session = AsyncSession(bind=conn, expire_on_commit=False)

        # 当 session.commit() 被调用时，重新开始一个新的 SAVEPOINT
        @event.listens_for(session.sync_session, "after_transaction_end")
        def _restart_savepoint(sync_session, transaction):
            if transaction.nested and not transaction._parent.nested:
                sync_session.begin_nested()

        yield session

        await session.close()
        await trans.rollback()


@pytest_asyncio.fixture
async def make_client(db):
    """按角色构造 ASGI client (override get_current_user + get_db)。
    所有上层依赖 (require_admin_or_above / require_ns_manage / require_ns_access)
    基于注入的真实 User 对象走真实判定, 不被 override 短路。
    用法: client = await make_client(role="admin", user_id=7)
    """
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.auth import get_current_user
    from app.db.metadata import get_db
    from app.models.user import User

    created = []

    async def _factory(role: str = "super_admin", user_id: int = 1, username: str = "admin"):
        if await db.get(User, user_id) is None:
            db.add(User(id=user_id, username=username, role=role, password_hash="x"))
            await db.flush()

        async def _fake_user():
            return User(id=user_id, username=username, role=role, password_hash="x")

        async def _fake_db():
            yield db

        app.dependency_overrides[get_current_user] = _fake_user
        app.dependency_overrides[get_db] = _fake_db
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        created.append(client)
        return client

    yield _factory
    for c in created:
        await c.aclose()
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_client(make_client):
    """向后兼容: 默认 super_admin 身份 client (旧测试沿用)。"""
    return await make_client(role="super_admin", user_id=1, username="admin")

