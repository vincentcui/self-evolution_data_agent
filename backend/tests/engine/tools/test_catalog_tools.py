"""catalog_tools — list_databases timezone + db_profile 投影契约 (真实 PG, 无 mock)."""
import json

import pytest

from app.engine.tools.catalog_tools import list_databases
from app.models import DataSource, Namespace


async def _seed_ns(db, slug: str) -> int:
    ns = Namespace(name=slug, slug=slug)
    db.add(ns)
    await db.flush()
    return ns.id


@pytest.mark.asyncio
async def test_list_databases_returns_timezone_and_projected_profile(db):
    ns_id = await _seed_ns(db, "t-listdb-tz")
    db.add(DataSource(
        namespace_id=ns_id, db_type="mysql", host="h", port=3306,
        database="shop_db", username="u", password="p",
        timezone="Asia/Shanghai",
        db_profile_json=json.dumps({
            "version": "8.0",
            "charset": "utf8mb4",
            "object_count": 5,
            "profiled_at": "x",
        }),
    ))
    await db.flush()
    r = await list_databases(db=db, namespace_id=ns_id)
    assert r["count"] == 1
    d = r["databases"][0]
    assert d["timezone"] == "Asia/Shanghai"
    assert d["db_profile"] == {"version": "8.0", "charset": "utf8mb4", "object_count": 5}
    assert "profiled_at" not in d["db_profile"]
