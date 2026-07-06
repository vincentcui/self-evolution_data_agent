"""PATCH relationships — contract + pydantic + handler routing."""
from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import Namespace, SchemaCanonicalObject


@pytest.mark.asyncio
async def test_patch_relationships_via_httpx(test_session: AsyncSession):
    """真路由: PATCH body{relationships} → 200 + user_locked."""
    ns = Namespace(slug="test_ns_r", name="Test NS R")
    test_session.add(ns)
    await test_session.flush()  # 取 ns.id 再建 SCO (namespace_id 必须匹配 PATCH 路径)
    sco = SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mysql", database="db_a",
        target="t_order", relationships_json="[]", user_locked=False,
    )
    test_session.add(sco)
    await test_session.commit()
    ns_id = ns.id
    sco_id = sco.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        from app.auth import require_ns_manage
        from app.db.metadata import get_db
        from app.models import User

        app.dependency_overrides[get_db] = lambda: test_session
        admin = User(id=1, username="admin", role="admin")
        app.dependency_overrides[require_ns_manage] = lambda: admin

        body = {"relationships": [{
            "from_target": "t_order", "from_field": "user_id",
            "to_db_type": "mysql", "to_database": "db_a",
            "to_target": "t_user", "to_field": "id",
            "relation_type": "many_to_one",
        }]}
        resp = await client.patch(
            f"/api/namespaces/{ns_id}/schema-canonical/{sco_id}",
            json=body,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["relationships"] == body["relationships"]
        assert data["user_locked"] is True

        await test_session.refresh(sco)
        assert sco.user_locked
        rels = json.loads(sco.relationships_json)
        assert len(rels) == 1
        assert rels[0]["relation_type"] == "many_to_one"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_patch_invalid_relation_type_422():
    """Pydantic Literal 校验: 非法 relation_type → pydantic reject."""
    from app.api.schema_canonical import SchemaCanonicalPatch

    with pytest.raises(Exception):
        SchemaCanonicalPatch(relationships=[{
            "from_target": "t", "from_field": "f",
            "to_db_type": "mysql", "to_database": "db",
            "to_target": "t2", "to_field": "id",
            "relation_type": "invalid_type",
        }])


@pytest.mark.asyncio
async def test_patch_missing_required_field_422():
    """missing to_target → pydantic rejects."""
    from app.api.schema_canonical import SchemaCanonicalPatch

    with pytest.raises(Exception):
        SchemaCanonicalPatch(relationships=[{
            "from_target": "t", "from_field": "f",
            "to_db_type": "mysql", "to_database": "db",
            "to_field": "id",
            "relation_type": "many_to_one",
        }])
