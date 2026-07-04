"""API 契约: POST/GET datasources 响应结构 (含 description+db_profile, 不含 password).

用 patch 注入 fake driver 返回连通画像, CI 不连真实库."""
from unittest.mock import patch

import pytest

from app.models import DataSource, Namespace


class _FakeDriver:
    async def fetch_db_profile(self, ds):
        return {"connected": True, "version": "8.0.36", "charset": "utf8mb4",
                "object_count": 5, "profiled_at": "2026-06-14T10:00:00"}


@pytest.mark.asyncio
async def test_post_datasource_response_contract(make_client, db):
    """201 响应含 description+db_profile, 不含 password."""
    ns = Namespace(name="t-contract", slug="t-contract")
    db.add(ns)
    await db.flush()
    client = await make_client(role="super_admin", user_id=1)

    with patch("app.api.namespace.get_driver", return_value=_FakeDriver()):
        resp = await client.post(
            f"/api/namespaces/{ns.id}/datasources",
            json={"db_type": "mysql", "host": "h", "port": 3306,
                  "database": "d", "username": "u", "password": "p",
                  "description": "契约测试库", "timezone": "Asia/Shanghai"},
        )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["description"] == "契约测试库"
    assert data["db_profile"]["object_count"] == 5
    assert data["db_profile"]["version"] == "8.0.36"
    assert "connected" not in data["db_profile"]  # 连通标志是建源决策用, 不持久化
    assert "password" not in data  # 永不暴露


@pytest.mark.asyncio
async def test_get_datasources_response_contract(make_client, db):
    """GET 列表每项含 description+db_profile, 不含 password."""
    import json
    ns = Namespace(name="t-contract-get", slug="t-contract-get")
    db.add(ns)
    await db.flush()
    db.add(DataSource(
        namespace_id=ns.id, db_type="mysql", host="h", port=3306,
        database="d", username="u", password="p", description="列表库",
        db_profile_json=json.dumps({"version": "8.0", "object_count": 3}),
        timezone="Asia/Shanghai",
    ))
    await db.flush()
    client = await make_client(role="super_admin", user_id=1)

    resp = await client.get(f"/api/namespaces/{ns.id}/datasources")
    assert resp.status_code == 200, resp.text
    item = resp.json()[0]
    assert item["description"] == "列表库"
    assert item["db_profile"]["object_count"] == 3
    assert "password" not in item


@pytest.mark.asyncio
async def test_refresh_schema_updates_db_profile(make_client, db):
    """刷新 schema 顺带刷新 db_profile (object_count 变化反映到画像)."""
    import json
    ns = Namespace(name="t-refresh-prof", slug="t-refresh-prof")
    db.add(ns)
    await db.flush()
    ds = DataSource(
        namespace_id=ns.id, db_type="mysql", host="h", port=3306,
        database="d", username="u", password="p", description="x",
        db_profile_json=json.dumps({"version": "8.0", "object_count": 1}),
        timezone="Asia/Shanghai",
    )
    db.add(ds)
    await db.flush()
    client = await make_client(role="super_admin", user_id=1)

    # fake: refresh_mysql_canonicals 返回 9, fetch_db_profile 返回新 object_count
    async def _fake_refresh(*a, **k):
        return 9
    class _FakeDriver2:
        async def fetch_db_profile(self, _ds):
            return {"connected": True, "version": "8.0.36", "object_count": 9,
                    "profiled_at": "2026-06-14T11:00:00"}

    with patch("app.knowledge.schema_canonical.refresh_mysql_canonicals", _fake_refresh), \
         patch("app.api.namespace.get_driver", return_value=_FakeDriver2()):
        resp = await client.post(f"/api/namespaces/{ns.id}/datasources/{ds.id}/refresh-schema")
    assert resp.status_code == 200, resp.text
    await db.refresh(ds)
    assert json.loads(ds.db_profile_json)["object_count"] == 9  # 画像已更新


@pytest.mark.asyncio
async def test_refresh_schema_mongodb_still_refreshes_profile(make_client, db):
    """MongoDB 源 refresh-schema: canonical 不支持 (success=False), 但 db_profile 仍刷新.

    锚定 Spec §9 — 刷新 schema 顺带重算 db_profile 对 db_type 中立, 不应被
    MySQL-only 早返跳过 (回归保护: 早返曾在 profile 刷新之前)."""
    import json
    ns = Namespace(name="t-refresh-mongo", slug="t-refresh-mongo")
    db.add(ns)
    await db.flush()
    ds = DataSource(
        namespace_id=ns.id, db_type="mongodb", host="h", port=27017,
        database="d", username="u", password="p", description="x",
        db_profile_json=json.dumps({"version": "5.0", "object_count": 2}),
        timezone="Asia/Shanghai",
    )
    db.add(ds)
    await db.flush()
    client = await make_client(role="super_admin", user_id=1)

    class _FakeMongoDriver:
        async def fetch_db_profile(self, _ds):
            return {"connected": True, "version": "5.0.0", "flavor": "documentdb",
                    "object_count": 7, "profiled_at": "2026-06-14T12:00:00"}

    with patch("app.api.namespace.get_driver", return_value=_FakeMongoDriver()):
        resp = await client.post(f"/api/namespaces/{ns.id}/datasources/{ds.id}/refresh-schema")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is False  # canonical 刷新仍不支持 mongodb
    await db.refresh(ds)
    prof = json.loads(ds.db_profile_json)
    assert prof["object_count"] == 7  # 画像已刷新 (不被早返跳过)
    assert prof["flavor"] == "documentdb"
    assert "connected" not in prof  # 连通标志不持久化
