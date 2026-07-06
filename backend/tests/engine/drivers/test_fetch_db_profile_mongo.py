"""L1 live test — MongoDriver.fetch_db_profile. 读 .env.test 的 E2E_MONGO_* 连真实库.

无 E2E_MONGO_HOST 时 skip."""
from __future__ import annotations

import os

import pytest

from app.engine.drivers.mongo import MongoDriver
from app.models import DataSource

# 注: 不挂模块级 pytestmark=live — 否则 test_get_client_rejects_unsaved_ds (CI-safe 守卫,
# 不连真库) 会被误标 live, 将来 CI 改用 -m "not live" 时被误删. 仅给真连库的用例逐个标 live.


def _mongo_ds_from_env() -> DataSource | None:
    host = os.environ.get("E2E_MONGO_HOST")
    if not host:
        return None
    return DataSource(
        id=None, namespace_id=0, db_type="mongodb",
        host=host,
        port=int(os.environ.get("E2E_MONGO_PORT", "27017")),
        database=os.environ.get("E2E_MONGO_DB", ""),
        username=os.environ.get("E2E_MONGO_USER", ""),
        password=os.environ.get("E2E_MONGO_PASS", ""),
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_mongo_fetch_db_profile_real():
    ds = _mongo_ds_from_env()
    if ds is None:
        pytest.skip("E2E_MONGO_HOST not set")
    driver = MongoDriver()
    profile = await driver.fetch_db_profile(ds)
    assert profile["connected"] is True  # 连通判据 (ping, 与 buildInfo 解耦)
    assert "version" in profile and profile["version"]
    assert "object_count" in profile and isinstance(profile["object_count"], int)
    assert "flavor" in profile
    assert "profiled_at" in profile


@pytest.mark.live
@pytest.mark.asyncio
async def test_mongo_fetch_db_profile_no_cache_pollution():
    ds = _mongo_ds_from_env()
    if ds is None:
        pytest.skip("E2E_MONGO_HOST not set")
    driver = MongoDriver()
    await driver.fetch_db_profile(ds)
    assert None not in driver._clients


@pytest.mark.live
@pytest.mark.asyncio
async def test_mongo_fetch_db_profile_nonexistent_database():
    """连通但 ds.database 不存在 — buildInfo 仍成功 (server 级), 但 object_count=0.

    锚定 driver 间语义差异: MySQL connect(db=X) 库不存在直接拒绝 (无 version);
    Mongo 连 server 成功后 list_collection_names 在不存在的库上返回空列表 (不报错).
    因此 Mongo 侧 "连通" 的语义是 server 可达 + auth 通过, 不保证 database 存在.
    本测试确认该行为: version 有 (server 级), object_count=0 (空库或不存在均如此).
    """
    ds = _mongo_ds_from_env()
    if ds is None:
        pytest.skip("E2E_MONGO_HOST not set")
    # 篡改 database 为一个肯定不存在的库名
    ds.database = "nonexistent_db_e2e_probe_" + str(int(__import__("time").time()))
    driver = MongoDriver()
    profile = await driver.fetch_db_profile(ds)
    # server 可达 → ping 成功 → connected True (即便 database 不存在)
    assert profile["connected"] is True
    # server 级信息仍可获取
    assert "version" in profile and profile["version"]
    assert "profiled_at" in profile
    # database 不存在但 Mongo 不报错, list_collection_names 返空 → object_count=0
    assert profile.get("object_count") == 0


@pytest.mark.asyncio
async def test_get_client_rejects_unsaved_ds():
    """ds.id is None 进 _get_client 立即 ValueError (防缓存污染地雷)."""
    ds = DataSource(
        id=None, namespace_id=0, db_type="mongodb",
        host="h", port=27017, database="d", username="u", password="p",
    )
    driver = MongoDriver()
    with pytest.raises(ValueError, match="未落库"):
        driver._get_client(ds)  # 同步方法, 不 await


@pytest.mark.asyncio
async def test_mongo_fetch_db_profile_includes_caps(monkeypatch):
    """Mock Motor 确认 fetch_db_profile 落库四能力限制字段."""

    class _FakeAdmin:
        async def command(self, cmd: str) -> dict:
            if cmd == "buildInfo":
                return {"version": "5.0", "gitVersion": "abc123", "modules": []}
            raise ValueError(f"Unexpected admin command: {cmd}")

    class _FakeDatabase:
        async def command(self, cmd: str) -> dict:
            if cmd == "ping":
                return {"ok": 1}
            raise ValueError(f"Unexpected db command: {cmd}")

        async def list_collection_names(self) -> list:
            return []

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            self.admin = _FakeAdmin()
            self._db = _FakeDatabase()

        def __getitem__(self, _key: str) -> _FakeDatabase:
            return self._db

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.engine.drivers.mongo.AsyncIOMotorClient", _FakeClient)

    ds = DataSource(
        id=999, namespace_id=0, db_type="mongodb",
        host="localhost", port=27017, database="test_db",
        username="u", password="p",
    )
    driver = MongoDriver()
    profile = await driver.fetch_db_profile(ds)

    assert profile["connected"] is True
    assert profile["flavor"] == "mongodb"  # 5.0 native
    for key in ("unsupported_ops", "unsupported_stage_variants",
                 "syntax_constraints", "equivalent_hints"):
        assert key in profile, f"profile 缺少 {key}"
        assert isinstance(profile[key], list), f"{key} 应为 list"
