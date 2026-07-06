"""L1 live test — connects to ds=3 (the same MongoDB that exposed
trace 173dff87's $round failure). Skipped without IS_METADATA_DB_URL."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.engine.drivers.mongo import MongoDriver
from app.models import DataSource

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_real_mongo_fetch_db_profile():
    url = os.environ.get("IS_METADATA_DB_URL")
    if not url:
        pytest.skip("IS_METADATA_DB_URL not set")
    engine = create_async_engine(url)
    SM = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with SM() as s:
            ds = (await s.execute(
                select(DataSource).where(DataSource.id == 3)
            )).scalar_one_or_none()
        if ds is None:
            pytest.skip("ds=3 not found in metadata db")

        driver = MongoDriver()
        profile = await driver.fetch_db_profile(ds)
        assert profile["connected"] is True, "ds=3 must be reachable"
        assert profile.get("version"), "version must be non-empty on real ds=3"
        print(f"\n[live] ds=3 version={profile['version']}")
        print(f"[live] unsupported_ops={profile.get('unsupported_ops', [])}")
    finally:
        await engine.dispose()
