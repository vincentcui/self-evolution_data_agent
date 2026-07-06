"""MongoDriver.fetch_foreign_keys — 返 [], 零连库."""
from __future__ import annotations

import pytest
from app.engine.drivers.mongo import MongoDriver
from app.models import DataSource


@pytest.mark.asyncio
async def test_mongo_fetch_foreign_keys_returns_empty():
    driver = MongoDriver()
    ds = DataSource(
        id=1, namespace_id=0, db_type="mongodb",
        host="h", port=27017, database="d",
        username="u", password="p",
    )
    result = await driver.fetch_foreign_keys(ds)
    assert result == []
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_mongo_fetch_foreign_keys_with_target():
    """target 参数不影响结果."""
    driver = MongoDriver()
    ds = DataSource(
        id=2, namespace_id=0, db_type="mongodb",
        host="h", port=27017, database="d",
        username="u", password="p",
    )
    result = await driver.fetch_foreign_keys(ds, target="some_collection")
    assert result == []
