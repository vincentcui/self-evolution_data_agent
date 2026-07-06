"""_build_coll_to_db: 增强返 (db_type, database) tuple."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import DataSource


@pytest.mark.asyncio
async def test_coll_to_db_returns_db_type_database_tuple():
    from app.knowledge.trainer import _build_coll_to_db

    ds_mysql = DataSource(id=1,namespace_id=100,db_type="mysql",host="h",port=3306,database="db_a",username="u",password="p")
    ds_mongo = DataSource(id=2,namespace_id=100,db_type="mongodb",host="h",port=27017,database="db_b",username="u",password="p")

    def _fake_get_driver(_dt):
        drv = AsyncMock()
        drv.list_object_names = AsyncMock(return_value=["t1","t2"])
        return drv

    # get_driver 是函数内 import, 在 app.engine.drivers 模块层面 patch
    with (
        patch("app.knowledge.trainer.async_session") as s,
        patch("app.engine.drivers.get_driver", _fake_get_driver),
        patch("app.knowledge.trainer.select"),
    ):
        ses = AsyncMock()
        # execute(…) 返 AsyncMock, await 返同一对象 (self-loop)
        rv = AsyncMock()
        rv.return_value = rv
        # scalars 必须用 MagicMock (非 AsyncMock), 否则调用返 coroutine
        rv.scalars = MagicMock()
        rv.scalars.return_value.all.return_value = [ds_mysql, ds_mongo]
        ses.execute.return_value = rv
        ses.commit = AsyncMock()
        s.return_value.__aenter__.return_value = ses

        from app.engine.drivers import DRIVERS
        DRIVERS["mysql"] = True
        DRIVERS["mongodb"] = True

        result = await _build_coll_to_db(100,"test")
        assert result["t1"] == ("mysql","db_a")
        assert result["t2"] == ("mysql","db_a")


@pytest.mark.asyncio
async def test_empty_ns_returns_empty_dict():
    from app.knowledge.trainer import _build_coll_to_db

    with (
        patch("app.knowledge.trainer.async_session") as s,
        patch("app.knowledge.trainer.select"),
    ):
        ses = AsyncMock()
        rv = AsyncMock()
        rv.return_value = rv
        rv.scalars = MagicMock()
        rv.scalars.return_value.all.return_value = []
        ses.execute.return_value = rv
        ses.commit = AsyncMock()
        s.return_value.__aenter__.return_value = ses

        assert await _build_coll_to_db(100,"empty") == {}
