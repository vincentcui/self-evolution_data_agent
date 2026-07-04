"""probe_connectivity 单元测试 — MySQL / MongoDB / Oracle 三 driver.

走 AsyncMock/patch mock 掉 DB 连接层; 不依赖真实数据库.
验证 ProbeResult 的 connected / detected_timezone / failure_reason 字段语义.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engine.drivers.base import ProbeResult

# ── 工具: 构造最简 DataSource mock ───────────────────────────────────────────


def _make_ds(db_type: str = "mysql") -> MagicMock:
    ds = MagicMock()
    ds.id = None
    ds.host = "db-host"
    ds.port = 3306 if db_type == "mysql" else (27017 if db_type == "mongo" else 1521)
    ds.database = "shop_db"
    ds.username = "appuser"
    ds.password = "pw"  # dummy (≤5 字符避 publish-github 脱敏闸门 password= 正则误命中)
    return ds


# ══════════════════════════════════════════════════════════════════════════════
#  MySQL probe_connectivity
# ══════════════════════════════════════════════════════════════════════════════


def _make_mysql_cursor(sess: str, sys: str) -> MagicMock:
    """构造支持 async with 的 aiomysql DictCursor mock."""
    cur = MagicMock()
    cur.__aenter__ = AsyncMock(return_value=cur)
    cur.__aexit__ = AsyncMock(return_value=False)
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(return_value={"sess": sess, "sys": sys})
    return cur


def _make_mysql_conn(cur: MagicMock) -> MagicMock:
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)
    conn.close = MagicMock()
    return conn


@pytest.mark.asyncio
async def test_mysql_probe_connects_and_detects_offset():
    """aiomysql.connect 成功 + @@session.time_zone='+08:00' → 'Asia/Shanghai'."""
    cur = _make_mysql_cursor(sess="+08:00", sys="CST")
    conn = _make_mysql_conn(cur)

    with patch("aiomysql.connect", AsyncMock(return_value=conn)):
        from app.engine.drivers.mysql import MySQLDriver
        driver = MySQLDriver()
        result = await driver.probe_connectivity(_make_ds("mysql"))

    assert isinstance(result, ProbeResult)
    assert result.connected is True
    assert result.detected_timezone == "Asia/Shanghai"
    assert result.failure_reason is None


@pytest.mark.asyncio
async def test_mysql_probe_cst_returns_none():
    """@@session.time_zone='SYSTEM' + @@system_time_zone='CST' → detected_timezone=None (歧义)."""
    cur = _make_mysql_cursor(sess="SYSTEM", sys="CST")
    conn = _make_mysql_conn(cur)

    with patch("aiomysql.connect", AsyncMock(return_value=conn)):
        from app.engine.drivers.mysql import MySQLDriver
        driver = MySQLDriver()
        result = await driver.probe_connectivity(_make_ds("mysql"))

    assert result.connected is True
    assert result.detected_timezone is None


@pytest.mark.asyncio
async def test_mysql_probe_connect_fail():
    """aiomysql.connect 抛异常 → ProbeResult(connected=False, failure_reason 含错误信息)."""
    with patch("aiomysql.connect", AsyncMock(side_effect=OSError("connection refused"))):
        from app.engine.drivers.mysql import MySQLDriver
        driver = MySQLDriver()
        result = await driver.probe_connectivity(_make_ds("mysql"))

    assert result.connected is False
    assert result.failure_reason is not None
    assert "connection refused" in result.failure_reason


# ══════════════════════════════════════════════════════════════════════════════
#  MongoDB probe_connectivity
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mongo_probe_always_none_timezone():
    """AsyncIOMotorClient ping 成功 → connected=True, detected_timezone=None (Mongo 不可探)."""
    fake_db = MagicMock()
    fake_db.command = AsyncMock(return_value={"ok": 1})

    fake_client = MagicMock()
    fake_client.__getitem__ = MagicMock(return_value=fake_db)
    fake_client.close = MagicMock()

    with patch("app.engine.drivers.mongo.AsyncIOMotorClient", return_value=fake_client):
        from app.engine.drivers.mongo import MongoDriver
        driver = MongoDriver()
        result = await driver.probe_connectivity(_make_ds("mongo"))

    assert isinstance(result, ProbeResult)
    assert result.connected is True
    assert result.detected_timezone is None
    assert result.failure_reason is None


@pytest.mark.asyncio
async def test_mongo_probe_connect_fail():
    """ping 抛异常 → ProbeResult(connected=False, failure_reason 含错误信息)."""
    fake_db = MagicMock()
    fake_db.command = AsyncMock(side_effect=Exception("ServerSelectionTimeout"))

    fake_client = MagicMock()
    fake_client.__getitem__ = MagicMock(return_value=fake_db)
    fake_client.close = MagicMock()

    with patch("app.engine.drivers.mongo.AsyncIOMotorClient", return_value=fake_client):
        from app.engine.drivers.mongo import MongoDriver
        driver = MongoDriver()
        result = await driver.probe_connectivity(_make_ds("mongo"))

    assert result.connected is False
    assert result.failure_reason is not None
    assert "ServerSelectionTimeout" in result.failure_reason


# ══════════════════════════════════════════════════════════════════════════════
#  Oracle probe_connectivity
# ══════════════════════════════════════════════════════════════════════════════


async def _direct_executor(self_inner, func, *args):
    """_run_in_executor 替身: 直接同步调用, 绕开线程池 (单元测试专用)."""
    return func(*args)


def _make_oracle_conn(sessiontz_raw: str) -> MagicMock:
    """构造带 SESSIONTIMEZONE 结果的 Oracle 同步连接 mock."""
    cur = MagicMock()
    cur.execute = MagicMock()
    cur.fetchone = MagicMock(return_value=(sessiontz_raw,))

    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)
    conn.close = MagicMock()
    return conn


def _make_oracle_ds() -> MagicMock:
    return _make_ds("oracle")


@pytest.mark.asyncio
async def test_oracle_probe_sessiontimezone():
    """oracledb.connect 成功 + SESSIONTIMEZONE='+08:00' → detected_timezone='Asia/Shanghai'."""
    from app.engine.drivers.oracle import OracleDriver

    conn = _make_oracle_conn("+08:00")

    with patch("app.engine.drivers.oracle.oracledb.connect", return_value=conn), \
         patch.object(OracleDriver, "_run_in_executor", _direct_executor):
        driver = OracleDriver()
        result = await driver.probe_connectivity(_make_oracle_ds())

    assert isinstance(result, ProbeResult)
    assert result.connected is True
    assert result.detected_timezone == "Asia/Shanghai"
    assert result.failure_reason is None


@pytest.mark.asyncio
async def test_oracle_probe_connect_fail():
    """oracledb.connect 抛异常 → ProbeResult(connected=False)."""
    from app.engine.drivers.oracle import OracleDriver

    with patch("app.engine.drivers.oracle.oracledb.connect",
               side_effect=Exception("ORA-12541: no listener")), \
         patch.object(OracleDriver, "_run_in_executor", _direct_executor):
        driver = OracleDriver()
        result = await driver.probe_connectivity(_make_oracle_ds())

    assert result.connected is False
    assert result.failure_reason is not None
    assert "ORA-12541" in result.failure_reason
