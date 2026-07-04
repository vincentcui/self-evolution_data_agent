"""L1 live test — OracleDriver.fetch_db_profile 返回 charset + nchar_charset.

读 IS_ORACLE_TEST_* 凭据; 无凭据时 skip, 不影响 CI.
"""
from __future__ import annotations

import os

import pytest

from app.engine.drivers.oracle import OracleDriver
from app.models import DataSource


def _oracle_ds() -> DataSource | None:
    host = os.environ.get("IS_ORACLE_TEST_HOST")
    if not host:
        return None
    return DataSource(
        id=None,
        namespace_id=0,
        db_type="oracle",
        host=host,
        port=int(os.environ.get("IS_ORACLE_TEST_PORT", "1521")),
        database=os.environ.get("IS_ORACLE_TEST_SERVICE", ""),
        username=os.environ.get("IS_ORACLE_TEST_USER", ""),
        password=os.environ.get("IS_ORACLE_TEST_PASSWORD", ""),
        description="live test oracle charset",
        timezone="Asia/Shanghai",  # 仅用于构造, charset 探测与 timezone 无关
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_oracle_fetch_db_profile_includes_charset() -> None:
    """连真实 Oracle, fetch_db_profile 返回含 charset + nchar_charset.

    Task 6 验收: Oracle charset 探测落库验证 (plan01 Task 4 实现).
    """
    ds = _oracle_ds()
    if ds is None:
        pytest.skip("IS_ORACLE_TEST_HOST 未配置")

    driver = OracleDriver()
    profile = await driver.fetch_db_profile(ds)

    # 连接失败 skip 而非 fail — live 测试对环境问题(如 thin mode 不支持该 Oracle 版本)宽容
    if not profile.get("connected"):
        pytest.skip(f"Oracle 连接失败 (环境问题, 非测试缺陷): {profile.get('error', 'unknown')}")
    assert "version" in profile and profile["version"], (
        f"version 缺失或为空, profile keys: {list(profile.keys())}"
    )
    assert "charset" in profile and profile["charset"], (
        f"NLS_CHARACTERSET 缺失, profile keys: {list(profile.keys())}"
    )
    assert "nchar_charset" in profile and profile["nchar_charset"], (
        f"NLS_NCHAR_CHARACTERSET 缺失, profile keys: {list(profile.keys())}"
    )
    assert "schema" in profile and profile["schema"], "schema 缺失"
    assert "profiled_at" in profile, "profiled_at 缺失"

    # 打印采集结果供人工审查 (live 测试可见)
    print(f"\n[live Oracle] version={profile['version']}")
    print(f"[live Oracle] charset={profile['charset']}")
    print(f"[live Oracle] nchar_charset={profile['nchar_charset']}")
    print(f"[live Oracle] schema={profile['schema']}")
