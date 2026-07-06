"""MySQLDriver.fetch_foreign_keys — L0 pure mapping + L1 live test."""
from __future__ import annotations

import os

import pytest

from app.engine.drivers.mysql import MySQLDriver, _fk_rows_to_relationships
from app.models import DataSource


# ═══════════ L0: pure row mapping ═══════════

def test_fk_rows_to_empty():
    assert _fk_rows_to_relationships([], "mysql") == []


def test_fk_rows_single_row():
    rows = [{
        "TABLE_NAME": "t_order",
        "COLUMN_NAME": "user_id",
        "REFERENCED_TABLE_SCHEMA": "app_db",
        "REFERENCED_TABLE_NAME": "t_user",
        "REFERENCED_COLUMN_NAME": "id",
    }]
    result = _fk_rows_to_relationships(rows, "mysql")
    assert len(result) == 1
    r = result[0]
    assert r["from_target"] == "t_order"
    assert r["from_field"] == "user_id"
    assert r["to_db_type"] == "mysql"
    assert r["to_database"] == "app_db"
    assert r["to_target"] == "t_user"
    assert r["to_field"] == "id"
    assert r["relation_type"] == "many_to_one"
    assert len(r) == 7  # 7 键 exact


def test_fk_rows_multiple():
    rows = [
        {"TABLE_NAME": "t_order", "COLUMN_NAME": "user_id",
         "REFERENCED_TABLE_SCHEMA": "db1", "REFERENCED_TABLE_NAME": "t_user",
         "REFERENCED_COLUMN_NAME": "id"},
        {"TABLE_NAME": "t_order", "COLUMN_NAME": "product_id",
         "REFERENCED_TABLE_SCHEMA": "db1", "REFERENCED_TABLE_NAME": "t_product",
         "REFERENCED_COLUMN_NAME": "id"},
    ]
    result = _fk_rows_to_relationships(rows, "mysql")
    assert len(result) == 2
    assert result[0]["from_field"] == "user_id"
    assert result[1]["from_field"] == "product_id"


# ═══════════ degradation test ═══════════

@pytest.mark.asyncio
async def test_mysql_fetch_foreign_keys_connection_refused():
    """连接失败 → 降级返 [](与 Oracle 对称)."""
    ds = DataSource(
        id=999, namespace_id=0, db_type="mysql",
        host="127.0.0.1", port=19999, database="no_such_db",
        username="nobody", password="x",
    )
    driver = MySQLDriver()
    result = await driver.fetch_foreign_keys(ds)
    assert result == []  # 降级,不抛异常


# ═══════════ L1: live test ═══════════

def _mysql_ds_from_env() -> DataSource | None:
    host = os.environ.get("E2E_MYSQL_HOST")
    if not host:
        return None
    return DataSource(
        id=1, namespace_id=0, db_type="mysql",
        host=host,
        port=int(os.environ.get("E2E_MYSQL_PORT", "3306")),
        database=os.environ.get("E2E_MYSQL_DB", ""),
        username=os.environ.get("E2E_MYSQL_USER", ""),
        password=os.environ.get("E2E_MYSQL_PASS", ""),
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_mysql_fetch_foreign_keys_live():
    ds = _mysql_ds_from_env()
    if ds is None:
        pytest.skip("E2E_MYSQL_HOST not set")
    driver = MySQLDriver()
    result = await driver.fetch_foreign_keys(ds)
    assert isinstance(result, list)
    for r in result:
        assert set(r.keys()) == {
            "from_target", "from_field",
            "to_db_type", "to_database", "to_target", "to_field",
            "relation_type",
        }
        assert r["relation_type"] == "many_to_one"
        assert r["to_db_type"] == "mysql"


@pytest.mark.live
@pytest.mark.asyncio
async def test_mysql_fetch_foreign_keys_single_table():
    """target 参数收窄为单表."""
    ds = _mysql_ds_from_env()
    if ds is None:
        pytest.skip("E2E_MYSQL_HOST not set")
    driver = MySQLDriver()
    result = await driver.fetch_foreign_keys(ds, target="t_order")
    assert isinstance(result, list)
    for r in result:
        assert r["from_target"] == "t_order"
