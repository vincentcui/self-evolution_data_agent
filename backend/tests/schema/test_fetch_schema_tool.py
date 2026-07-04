"""P2.T12: fetch_schema 工具改造 — 返回 relationships + description + timezone + db_profile."""
import json

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from app.engine.tools.data_access_tools import fetch_schema
from app.models import DataSource, SchemaCanonicalObject

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def setup_ns_ds(test_session, namespace_factory):
    """创建 namespace + datasource, 返回 (ns, ds)."""
    ns = await namespace_factory()
    ds = DataSource(
        namespace_id=ns.id,
        db_type="mysql",
        host="localhost",
        port=3306,
        database="test_db",
        username="x",
        password="y",
        timezone="UTC",
    )
    test_session.add(ds)
    await test_session.flush()
    return ns, ds


@pytest_asyncio.fixture
async def setup_ns_ds_with_profile(test_session, namespace_factory):
    """创建带 timezone + db_profile_json 的 datasource, 用于时区/库画像回路测试."""
    ns = await namespace_factory()
    profile = {
        "version": "8.0",
        "charset": "utf8mb4",
        "flavor": "mysql",
        "object_count": 5,
        "profiled_at": "2026-07-01T00:00:00",
    }
    ds = DataSource(
        namespace_id=ns.id,
        db_type="mysql",
        host="localhost",
        port=3306,
        database="shop_db",
        username="x",
        password="y",
        timezone="Asia/Shanghai",
        db_profile_json=json.dumps(profile),
    )
    test_session.add(ds)
    await test_session.flush()
    return ns, ds


@pytest_asyncio.fixture
async def canonical_with_relationships(test_session, setup_ns_ds):
    """创建带 relationships_json 的 SchemaCanonicalObject."""
    ns, ds = setup_ns_ds
    relationships = [
        {
            "from_target": "t_order",
            "from_field": "user_id",
            "to_target": "t_user",
            "to_field": "id",
            "relation_type": "many_to_one",
        }
    ]
    sco = SchemaCanonicalObject(
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
        fields_json=json.dumps([{"name": "id", "type": "bigint"}]),
        indexes_json=json.dumps([]),
        description="订单主表",
        relationships_json=json.dumps(relationships),
        sample_count=100,
    )
    test_session.add(sco)
    await test_session.flush()
    return ns, ds, sco


async def test_fetch_schema_returns_relationships_from_canonical(
    test_session, canonical_with_relationships
):
    """canonical 路径应返回 relationships 字段."""
    ns, ds, sco = canonical_with_relationships

    result = await fetch_schema(
        db=test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
    )

    assert result["source"] == "canonical"
    assert result["relationships"] == [
        {
            "from_target": "t_order",
            "from_field": "user_id",
            "to_target": "t_user",
            "to_field": "id",
            "relation_type": "many_to_one",
        }
    ]


async def test_fetch_schema_returns_description(
    test_session, canonical_with_relationships
):
    """canonical 路径应返回 description 字段."""
    ns, ds, sco = canonical_with_relationships

    result = await fetch_schema(
        db=test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
    )

    assert result["source"] == "canonical"
    assert result["description"] == "订单主表"


async def test_fetch_schema_fallback_has_empty_relationships(test_session, setup_ns_ds):
    """driver introspect fallback 路径应返回空 relationships 数组."""
    ns, ds = setup_ns_ds

    fake_schema = {
        "target": "t_new_table",
        "database": "test_db",
        "fields": [{"name": "id", "type": "int"}],
        "indexes": [],
        "sample_count": 0,
    }

    with patch("app.engine.tools.data_access_tools.get_driver") as get_driver_mock:
        driver_mock = AsyncMock()
        driver_mock.fetch_schema = AsyncMock(return_value=fake_schema)
        get_driver_mock.return_value = driver_mock

        result = await fetch_schema(
            db=test_session,
            namespace_id=ns.id,
            db_type="mysql",
            database="test_db",
            target="t_new_table",
        )

    assert result["source"] == "introspect"
    assert result["relationships"] == []
    # 原有字段仍在
    assert result["fields"] == [{"name": "id", "type": "int"}]
    assert result["target"] == "t_new_table"


async def test_fetch_schema_returns_timezone_and_db_profile(
    test_session, setup_ns_ds_with_profile
):
    """canonical 路径返回 timezone + db_profile schema 投影 (version/charset/flavor, 无 object_count)."""
    ns, ds = setup_ns_ds_with_profile

    # 建 SchemaCanonicalObject 使 canonical 分支命中
    sco = SchemaCanonicalObject(
        namespace_id=ns.id,
        db_type="mysql",
        database="shop_db",
        target="t_order",
        fields_json=json.dumps([{"name": "id", "type": "bigint"}]),
        indexes_json=json.dumps([]),
        description="订单表",
        relationships_json=json.dumps([]),
        sample_count=10,
    )
    test_session.add(sco)
    await test_session.flush()

    result = await fetch_schema(
        db=test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="shop_db",
        target="t_order",
    )

    assert result["source"] == "canonical"
    assert result["timezone"] == "Asia/Shanghai"
    # schema 投影: 保留 version/charset/flavor
    assert result["db_profile"] == {"version": "8.0", "charset": "utf8mb4", "flavor": "mysql"}
    # schema 投影: 不含 object_count / profiled_at
    assert "object_count" not in result["db_profile"]
    assert "profiled_at" not in result["db_profile"]


async def test_fetch_schema_canonical_merges_caps(test_session, namespace_factory):
    """canonical 路径 db_profile = schema 投影 ∪ caps 投影 (plan03 修订 design 决策#11).

    Mongo profile 含 caps 键 (unsupported_ops 等) 时, fetch_schema merge 后
    LLM 一次拿全 schema + 能力限制, 生成 pipeline 避 unsupported_ops 坑.
    用 Mongo profile 验证 (MySQL 无 caps 键的 merge 通过纯属巧合, 不覆盖此路径).
    """
    ns = await namespace_factory()
    profile = {
        "version": "5.0.0",
        "flavor": "documentdb",
        "charset": "utf8",
        "object_count": 12,
        "profiled_at": "2026-07-01T00:00:00",
        "unsupported_ops": ["$out", "$merge"],
        "unsupported_stage_variants": [{"stage": "$lookup", "variant": "let_pipeline"}],
        "syntax_constraints": [],
        "equivalent_hints": [
            {"restriction": "$lookup.let_pipeline", "suggestion": "用 $id_str 多步关联"}
        ],
    }
    ds = DataSource(
        namespace_id=ns.id,
        db_type="mongodb",
        host="localhost",
        port=27017,
        database="shop_db",
        username="x",
        password="y",
        timezone="Asia/Shanghai",
        db_profile_json=json.dumps(profile),
    )
    test_session.add(ds)
    sco = SchemaCanonicalObject(
        namespace_id=ns.id,
        db_type="mongodb",
        database="shop_db",
        target="orders",
        fields_json=json.dumps([{"name": "_id", "type": "objectId"}]),
        indexes_json=json.dumps([]),
        description="订单集合",
        relationships_json=json.dumps([]),
        sample_count=10,
    )
    test_session.add(sco)
    await test_session.flush()

    result = await fetch_schema(
        db=test_session,
        namespace_id=ns.id,
        db_type="mongodb",
        database="shop_db",
        target="orders",
    )

    assert result["source"] == "canonical"
    # schema 投影键 (version/flavor/charset) + caps 投影键 (caps 四限制, flavor 重叠) 全存活
    merged = result["db_profile"]
    assert merged["version"] == "5.0.0"
    assert merged["charset"] == "utf8"
    assert merged["flavor"] == "documentdb"
    assert merged["unsupported_ops"] == ["$out", "$merge"]
    assert merged["unsupported_stage_variants"] == [
        {"stage": "$lookup", "variant": "let_pipeline"}
    ]
    assert merged["syntax_constraints"] == []
    assert merged["equivalent_hints"] == [
        {"restriction": "$lookup.let_pipeline", "suggestion": "用 $id_str 多步关联"}
    ]
    # 投影过滤: object_count / profiled_at 不暴露给 LLM
    assert "object_count" not in merged
    assert "profiled_at" not in merged
