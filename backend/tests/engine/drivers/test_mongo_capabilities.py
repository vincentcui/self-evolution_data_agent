"""Unit tests for mongo_capabilities pure functions."""
from __future__ import annotations

import pytest

from app.engine.drivers.mongo_capabilities import (
    compute_unsupported_ops,
    parse_version,
)


class TestParseVersion:
    def test_three_part(self):
        assert parse_version("4.2.3") == (4, 2, 3)

    def test_two_part(self):
        assert parse_version("4.0") == (4, 0, 0)

    def test_with_patch_suffix(self):
        assert parse_version("4.0.28-rc0") == (4, 0, 28)

    def test_unknown(self):
        assert parse_version("unknown") == (0, 0, 0)

    def test_empty(self):
        assert parse_version("") == (0, 0, 0)


class TestComputeUnsupportedOps:
    def test_v4_0_blocks_round_dateTrunc_function(self):
        out = compute_unsupported_ops("4.0.28")
        assert "$round" in out
        assert "$dateTrunc" in out
        assert "$function" in out

    def test_v4_2_allows_round_blocks_dateTrunc(self):
        out = compute_unsupported_ops("4.2.0")
        assert "$round" not in out
        assert "$dateTrunc" in out
        assert "$function" in out  # 4.4 才有

    def test_v5_0_allows_dateTrunc_blocks_median(self):
        out = compute_unsupported_ops("5.0.0")
        assert "$round" not in out
        assert "$dateTrunc" not in out
        assert "$median" in out  # 7.0 才有
        assert "$percentile" in out

    def test_v7_0_allows_all(self):
        out = compute_unsupported_ops("7.0.0")
        assert "$round" not in out
        assert "$dateTrunc" not in out
        assert "$median" not in out
        assert "$percentile" not in out

    def test_unknown_version_returns_empty(self):
        # 未知 version 不能 false-positive 屏蔽算子
        assert compute_unsupported_ops("unknown") == []

    def test_returned_list_is_sorted(self):
        out = compute_unsupported_ops("4.0.0")
        assert out == sorted(out)


# ──────────────────────────────────────────────────────────
#  MongoDriver.fetch_db_profile → caps (unsupported_ops)
# ──────────────────────────────────────────────────────────

from unittest.mock import AsyncMock, MagicMock, patch

from app.engine.drivers.mongo import MongoDriver
from app.models import DataSource


def make_ds(ds_id: int = 1) -> DataSource:
    ds = MagicMock(spec=DataSource)
    ds.id = ds_id
    ds.host = "h"
    ds.port = 27017
    ds.username = "u"
    ds.password = "p"
    ds.database = "db"
    return ds


class TestMongoFetchDbProfileCaps:
    """Test that MongoDriver.fetch_db_profile produces unsupported_ops in profile."""

    @pytest.mark.asyncio
    async def test_profile_includes_unsupported_ops_for_4_0(self):
        driver = MongoDriver()
        ds = make_ds(1)

        with patch("app.engine.drivers.mongo.AsyncIOMotorClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_db = MagicMock()
            mock_client.__getitem__.return_value = mock_db

            # Ping succeeds
            mock_db.command = AsyncMock(return_value={"ok": 1})
            # buildInfo returns version
            admin_mock = MagicMock()
            admin_mock.command = AsyncMock(return_value={"version": "4.0.28"})
            mock_client.admin = admin_mock
            # list_collection_names
            mock_db.list_collection_names = AsyncMock(return_value=["c1", "c2"])

            profile = await driver.fetch_db_profile(ds)

        assert profile["connected"] is True
        assert profile["version"] == "4.0.28"
        assert "$round" in profile["unsupported_ops"]
        assert "$dateTrunc" in profile["unsupported_ops"]

    @pytest.mark.asyncio
    async def test_buildinfo_failure_omits_caps_keys(self):
        driver = MongoDriver()
        ds = make_ds(1)

        with patch("app.engine.drivers.mongo.AsyncIOMotorClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_db = MagicMock()
            mock_client.__getitem__.return_value = mock_db

            mock_db.command = AsyncMock(return_value={"ok": 1})
            admin_mock = MagicMock()
            admin_mock.command = AsyncMock(side_effect=RuntimeError("boom"))
            mock_client.admin = admin_mock
            mock_db.list_collection_names = AsyncMock(return_value=["c1"])

            profile = await driver.fetch_db_profile(ds)

        assert profile["connected"] is True
        # buildInfo 失败 → profile 无 version/unsupported_ops 键
        assert "version" not in profile
        assert "unsupported_ops" not in profile
