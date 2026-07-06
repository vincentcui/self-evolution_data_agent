"""OracleDriver.fetch_foreign_keys — degradation + live test."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.engine.drivers.oracle import OracleDriver, _oracle_fk_rows_to_relationships
from app.models import DataSource


# ═══════════ L0: degradation ═══════════

@pytest.mark.asyncio
async def test_oracle_fetch_foreign_keys_degradation():
    """USER_CONSTRAINTS 查询失败 → 降级返 []."""
    driver = OracleDriver()
    ds = DataSource(
        id=1, namespace_id=0, db_type="oracle",
        host="h", port=1521, database="XEPDB1",
        username="u", password="p",
    )
    # 只 mock 关键点: _get_sync_pool 抛异常
    with patch.object(driver, "_get_sync_pool", side_effect=Exception("perm denied")):
        result = await driver.fetch_foreign_keys(ds)
        assert result == []


@pytest.mark.asyncio
async def test_oracle_fetch_foreign_keys_connect_failure():
    """连接失败也降级."""
    driver = OracleDriver()
    ds = DataSource(
        id=1, namespace_id=0, db_type="oracle",
        host="h", port=1521, database="XEPDB1",
        username="u", password="p",
    )
    # ds.id != None → _get_sync_pool 路径. 降级覆盖 _get_sync_pool 失败.
    with patch.object(driver, "_get_sync_pool", side_effect=OSError("connect refused")):
        result = await driver.fetch_foreign_keys(ds)
        assert result == []


# ═══════════ L0: pure row mapping ═══════════

def test_oracle_fk_rows_empty():
    assert _oracle_fk_rows_to_relationships([], "oracle") == []


def test_oracle_fk_rows_single():
    rows = [{
        "table_name": "T_ORDER",
        "column_name": "USER_ID",
        "referenced_owner": "APP_SCHEMA",
        "referenced_table_name": "T_USER",
        "referenced_column_name": "ID",
    }]
    result = _oracle_fk_rows_to_relationships(rows, "oracle")
    assert len(result) == 1
    r = result[0]
    assert r["to_db_type"] == "oracle"
    assert r["to_database"] == "APP_SCHEMA"
    assert r["relation_type"] == "many_to_one"


# ═══════════ L1: live ═══════════

def _oracle_ds_from_env() -> DataSource | None:
    host = os.environ.get("E2E_ORACLE_HOST")
    if not host:
        return None
    return DataSource(
        id=1, namespace_id=0, db_type="oracle",
        host=host,
        port=int(os.environ.get("E2E_ORACLE_PORT", "1521")),
        database=os.environ.get("E2E_ORACLE_DB", ""),
        username=os.environ.get("E2E_ORACLE_USER", ""),
        password=os.environ.get("E2E_ORACLE_PASS", ""),
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_oracle_fetch_foreign_keys_live():
    ds = _oracle_ds_from_env()
    if ds is None:
        pytest.skip("E2E_ORACLE_HOST not set")
    driver = OracleDriver()
    result = await driver.fetch_foreign_keys(ds)
    assert isinstance(result, list)
    for r in result:
        assert set(r.keys()) == {
            "from_target", "from_field",
            "to_db_type", "to_database", "to_target", "to_field",
            "relation_type",
        }
