"""Task 4 — create_knowledge API 强制 parse_payload 校验.

手动 API example 缺 final_query_plan 时 422; 合法 payload 201.
"""
import json
from unittest.mock import patch

import pytest

from app.models import Namespace


def _refine_result(refined: str, description: str = "", overflow: bool = False):
    return type("R", (), {"refined": refined, "description": description, "overflow": overflow})()


def _conflict_report(items):
    return type("C", (), {"items": items})()


@pytest.mark.asyncio
async def test_create_knowledge_example_rejects_payload_missing_final_query_plan(db, admin_client):
    """手动 API example 缺 final_query_plan 时 422."""
    ns = Namespace(name="t", slug="t")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    resp = await admin_client.post("/api/knowledge", json={
        "entry_type": "example",
        "content": "查在售商品",
        "namespace_id": ns.id,
        "payload": {"question_pattern": "查在售商品"},  # 缺 final_query_plan
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_knowledge_example_accepts_valid_payload(db, admin_client):
    ns = Namespace(name="t", slug="t")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    with patch(
        "app.api.knowledge.refine_knowledge",
        return_value=_refine_result("查在售商品", ""),
    ), patch(
        "app.api.knowledge.detect_conflicts",
        return_value=_conflict_report([]),
    ):
        resp = await admin_client.post("/api/knowledge", json={
            "entry_type": "example",
            "content": "查在售商品",
            "namespace_id": ns.id,
            "payload": {
                "question_pattern": "查在售商品",
                "final_query_plan": {"steps": [{
                    "db_type": "mongodb", "database": "shop",
                    "collection": "products", "operation": "filter",
                    "query": {"filter": {"active": True}},
                }]},
            },
        })
    assert resp.status_code in (200, 201)
