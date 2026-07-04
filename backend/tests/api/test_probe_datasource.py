"""POST /{ns_id}/datasources/probe 端点契约 + add_datasource timezone 必填.

Fixtures: make_client + db (from tests/conftest.py).
No ns_factory — Namespace 直接用 db.add 创建, 与邻近测试保持一致.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.engine.drivers.base import ProbeResult
from app.models import Namespace

# ── 辅助 ──────────────────────────────────────────────────────────────────

def _probe_url(ns_id: int) -> str:
    return f"/api/namespaces/{ns_id}/datasources/probe"


_PROBE_BODY = {
    "db_type": "mysql",
    "host": "db.shop-orders.internal",
    "port": 3306,
    "database": "orders_db",
    "username": "ro_user",
    "password": "s3cr3t",
}


# ── 测试 ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_returns_detected_timezone(make_client, db):
    """连通成功 → 200 + connected=True + detected_timezone 回传."""
    ns = Namespace(name="probe_ok", slug="probe-ok")
    db.add(ns)
    await db.flush()
    client = await make_client(role="super_admin", user_id=1)

    mock_driver = AsyncMock()
    mock_driver.probe_connectivity = AsyncMock(
        return_value=ProbeResult(connected=True, detected_timezone="Asia/Shanghai")
    )

    with patch("app.api.namespace.get_driver", return_value=mock_driver):
        resp = await client.post(_probe_url(ns.id), json=_PROBE_BODY)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "connected": True,
        "detected_timezone": "Asia/Shanghai",
        "failure_reason": None,
    }


@pytest.mark.asyncio
async def test_probe_connect_fail_returns_reason_at_200(make_client, db):
    """连通失败 → HTTP 200 (非 400), connected=False, failure_reason 存在, 让前端展示原因."""
    ns = Namespace(name="probe_fail", slug="probe-fail")
    db.add(ns)
    await db.flush()
    client = await make_client(role="super_admin", user_id=1)

    mock_driver = AsyncMock()
    mock_driver.probe_connectivity = AsyncMock(
        return_value=ProbeResult(
            connected=False,
            detected_timezone=None,
            failure_reason="Connection refused: port 3306",
        )
    )

    with patch("app.api.namespace.get_driver", return_value=mock_driver):
        resp = await client.post(_probe_url(ns.id), json=_PROBE_BODY)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connected"] is False
    assert body["failure_reason"] == "Connection refused: port 3306"
    assert body["detected_timezone"] is None


@pytest.mark.asyncio
async def test_add_datasource_requires_timezone(make_client, db):
    """POST /datasources 不含 timezone 字段 → 422 (Pydantic 必填校验)."""
    ns = Namespace(name="probe_tz_req", slug="probe-tz-req")
    db.add(ns)
    await db.flush()
    client = await make_client(role="super_admin", user_id=1)

    resp = await client.post(
        f"/api/namespaces/{ns.id}/datasources",
        json={
            "db_type": "mysql",
            "host": "h",
            "port": 3306,
            "database": "orders_db",
            "username": "u",
            "password": "p",
            # timezone 故意缺失
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_probe_namespace_not_found(make_client, db):
    """不存在的 ns_id → 404."""
    client = await make_client(role="super_admin", user_id=1)
    mock_driver = AsyncMock()
    mock_driver.probe_connectivity = AsyncMock(
        return_value=ProbeResult(connected=True, detected_timezone="UTC")
    )
    with patch("app.api.namespace.get_driver", return_value=mock_driver):
        resp = await client.post(_probe_url(999999), json=_PROBE_BODY)
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_probe_unsupported_db_type_returns_422(make_client, db):
    """非法 db_type → 422 (Pydantic field_validator 拦截), 不走 get_driver 致 500.

    回归保护: DataSourceProbeIn.db_type 加 field_validator(SUPPORTED_DB_TYPES) 前端点
    会 get_driver(bad) 抛 UnsupportedDataSourceTypeError → 500. 修复后 422 优先.
    """
    ns = Namespace(name="probe_bad_dbtype", slug="probe-bad-dbtype")
    db.add(ns)
    await db.flush()
    client = await make_client(role="super_admin", user_id=1)

    # get_driver 不应被调用 (校验在 handler 前拒绝); 若被调用说明校验未生效
    with patch("app.api.namespace.get_driver") as get_driver_mock:
        resp = await client.post(
            _probe_url(ns.id), json={**_PROBE_BODY, "db_type": "postgres"}
        )
    assert resp.status_code == 422, resp.text
    get_driver_mock.assert_not_called()
