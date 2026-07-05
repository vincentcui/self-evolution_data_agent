"""Verify fetch_schema / estimate_cost output includes db_profile (schema+caps merge).

L0 集成测试: mock driver + 验证 fetch_schema/estimate_cost 输出 db_profile 含
caps (unsupported_ops 等), 不再返 server_capabilities 字段 (caps 融入 db_profile).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engine.tools.data_access_tools import estimate_cost, fetch_schema


@pytest.fixture
def mock_ds():
    ds = MagicMock()
    ds.id = 99
    ds.timezone = "Asia/Shanghai"
    ds.db_profile_json = json.dumps({
        "version": "4.0.28", "flavor": "mongodb", "charset": None, "object_count": 3,
        "unsupported_ops": ["$round", "$dateTrunc", "$function"],
        "unsupported_stage_variants": [], "syntax_constraints": [], "equivalent_hints": [],
    })
    return ds


@pytest.fixture
def mock_driver_with_caps():
    driver = MagicMock()
    driver.fetch_schema = AsyncMock(return_value={
        "db_type": "mongodb",
        "database": "db_x",
        "target": "coll_x",
        "description": "",
        "fields": [],
        "indexes": [],
        "sample_count": 0,
    })
    driver.estimate_cost = AsyncMock(return_value={
        "estimated_rows": 100,
        "warning_level": "ok",
        "raw_explain": {},
    })
    return driver


@pytest.mark.asyncio
async def test_fetch_schema_canonical_branch_includes_db_profile_caps(
    mock_ds, mock_driver_with_caps,
):
    """canonical 命中分支: db_profile 含 caps (unsupported_ops), 无 server_capabilities."""
    canonical = MagicMock()
    canonical.fields_json = "[]"
    canonical.indexes_json = "[]"
    canonical.relationships_json = "[]"
    canonical.description = "canon-desc"
    canonical.sample_count = 0
    with patch(
        "app.engine.tools.data_access_tools.resolve_ds",
        AsyncMock(return_value=mock_ds),
    ), patch(
        "app.engine.tools.data_access_tools.get_driver",
        return_value=mock_driver_with_caps,
    ), patch(
        "app.knowledge.schema_canonical.get_schema_canonical",
        AsyncMock(return_value=canonical),
    ):
        result = await fetch_schema(
            db=MagicMock(),
            namespace_id=1,
            db_type="mongodb",
            database="db_x",
            target="coll_x",
        )
    assert result["source"] == "canonical"
    assert "server_capabilities" not in result
    assert "db_profile" in result
    assert "$round" in result["db_profile"]["unsupported_ops"]


@pytest.mark.asyncio
async def test_fetch_schema_includes_db_profile_caps(
    mock_ds, mock_driver_with_caps,
):
    """canonical = None → introspect 路径: db_profile 含 caps, 无 server_capabilities."""
    with patch(
        "app.engine.tools.data_access_tools.resolve_ds",
        AsyncMock(return_value=mock_ds),
    ), patch(
        "app.engine.tools.data_access_tools.get_driver",
        return_value=mock_driver_with_caps,
    ), patch(
        "app.knowledge.schema_canonical.get_schema_canonical",
        AsyncMock(return_value=None),
    ):
        result = await fetch_schema(
            db=MagicMock(),
            namespace_id=1,
            db_type="mongodb",
            database="db_x",
            target="coll_x",
        )
    assert "server_capabilities" not in result
    assert "db_profile" in result
    assert "$round" in result["db_profile"]["unsupported_ops"]


@pytest.mark.asyncio
async def test_estimate_cost_includes_db_profile_caps(
    mock_ds, mock_driver_with_caps,
):
    """estimate_cost: timezone + db_profile(schema+caps merge), 无 server_capabilities."""
    with patch(
        "app.engine.tools.data_access_tools.resolve_ds",
        AsyncMock(return_value=mock_ds),
    ), patch(
        "app.engine.tools.data_access_tools.get_driver",
        return_value=mock_driver_with_caps,
    ):
        result = await estimate_cost(
            db=MagicMock(),
            namespace_id=1,
            db_type="mongodb",
            database="db_x",
            target="coll_x",
            query={"filter": {}},
        )
    assert "server_capabilities" not in result
    assert result["timezone"] == "Asia/Shanghai"
    db_profile = result["db_profile"]
    assert "version" in db_profile  # from schema projection
    assert "unsupported_ops" in db_profile  # from caps projection
    assert "$round" in db_profile["unsupported_ops"]
