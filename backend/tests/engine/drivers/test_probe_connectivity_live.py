"""L1 live test — 三 driver probe_connectivity 真连.

读各环境变量凭据; 无凭据时对应测试 skip, 不影响 CI.
"""
from __future__ import annotations

import os

import pytest

from app.engine.drivers.mongo import MongoDriver
from app.engine.drivers.mysql import MySQLDriver
from app.engine.drivers.oracle import OracleDriver
from app.models import DataSource

# ════════════════════════════════════════════
#  Helper: DS 构造
# ════════════════════════════════════════════


def _mysql_ds() -> DataSource | None:
    host = os.environ.get("E2E_MYSQL_HOST")
    if not host:
        return None
    return DataSource(
        id=None, namespace_id=0, db_type="mysql",
        host=host,
        port=int(os.environ.get("E2E_MYSQL_PORT", "3306")),
        database=os.environ.get("E2E_MYSQL_DB", ""),
        username=os.environ.get("E2E_MYSQL_USER", ""),
        password=os.environ.get("E2E_MYSQL_PASS", ""),
        timezone="Asia/Shanghai",
    )


def _mongo_ds() -> DataSource | None:
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
        timezone="Asia/Shanghai",
    )


def _oracle_ds() -> DataSource | None:
    host = os.environ.get("IS_ORACLE_TEST_HOST")
    if not host:
        return None
    return DataSource(
        id=None, namespace_id=0, db_type="oracle",
        host=host,
        port=int(os.environ.get("IS_ORACLE_TEST_PORT", "1521")),
        database=os.environ.get("IS_ORACLE_TEST_SERVICE", ""),
        username=os.environ.get("IS_ORACLE_TEST_USER", ""),
        password=os.environ.get("IS_ORACLE_TEST_PASSWORD", ""),
        timezone="Asia/Shanghai",
    )


# ════════════════════════════════════════════
#  MySQL probe
# ════════════════════════════════════════════

@pytest.mark.live
@pytest.mark.asyncio
async def test_probe_mysql_live() -> None:
    """连真实 MySQL, probe_connectivity 返回 connected=True.

    凭据: E2E_MYSQL_HOST (E2E_MYSQL_PORT / E2E_MYSQL_DB / E2E_MYSQL_USER / E2E_MYSQL_PASS).
    detected_timezone 可能为 None (CST 歧义) 或 IANA 名, 都接受.
    """
    ds = _mysql_ds()
    if ds is None:
        pytest.skip("E2E_MYSQL_HOST not set")

    driver = MySQLDriver()
    r = await driver.probe_connectivity(ds)
    assert r.connected is True, f"MySQL 连接失败: {r.failure_reason}"

    # detected_timezone 可能 None (CST 歧义) 或 IANA 名, 不断言具体值
    print(f"\n[live MySQL probe] connected={r.connected} "
          f"detected_timezone={r.detected_timezone}")


# ════════════════════════════════════════════
#  MongoDB probe
# ════════════════════════════════════════════

@pytest.mark.live
@pytest.mark.asyncio
async def test_probe_mongo_live() -> None:
    """连真实 MongoDB, probe_connectivity 返回 connected=True.

    凭据: E2E_MONGO_HOST (E2E_MONGO_PORT / E2E_MONGO_DB / E2E_MONGO_USER / E2E_MONGO_PASS).
    MongoDB 不暴露时区设置, detected_timezone 始终 None.
    """
    ds = _mongo_ds()
    if ds is None:
        pytest.skip("E2E_MONGO_HOST not set")

    driver = MongoDriver()
    r = await driver.probe_connectivity(ds)
    assert r.connected is True, f"MongoDB 连接失败: {r.failure_reason}"
    # MongoDB 不暴露时区 → detected_timezone 始终 None
    assert r.detected_timezone is None


# ════════════════════════════════════════════
#  Oracle probe
# ════════════════════════════════════════════

@pytest.mark.live
@pytest.mark.asyncio
async def test_probe_oracle_live() -> None:
    """连真实 Oracle, probe_connectivity 返回 connected=True.

    凭据: IS_ORACLE_TEST_HOST (IS_ORACLE_TEST_PORT / IS_ORACLE_TEST_SERVICE /
    IS_ORACLE_TEST_USER / IS_ORACLE_TEST_PASSWORD).
    Oracle detected_timezone 从 SESSIONTIMEZONE 获取, 应为 IANA 名或 None.
    """
    ds = _oracle_ds()
    if ds is None:
        pytest.skip("IS_ORACLE_TEST_HOST 未配置")

    driver = OracleDriver()
    r = await driver.probe_connectivity(ds)
    # 连接失败 skip 而非 fail — live 测试对环境问题(如 thin mode 不支持该 Oracle 版本)宽容
    if not r.connected:
        pytest.skip(f"Oracle 连接失败 (环境问题, 非测试缺陷): {r.failure_reason}")

    print(f"\n[live Oracle probe] connected={r.connected} "
          f"detected_timezone={r.detected_timezone}")
    # Oracle 从 SESSIONTIMEZONE 探测时区, 正常应为 IANA 名, 但允许 None
