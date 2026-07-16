"""
G8 — 人工 create route_hint 端到端门 (spec 2026-07-08 route_hint payload DRY 化).

覆盖 POST /api/knowledge (entry_type=route_hint) 走 refine + conflict 既有路径
(非 terminology 特殊路径), payload 原样落库 (json.dumps),
验证响应 entry.payload 是新 {collection_path: list[CollectionRef], navigation_note} 形态。
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
async def test_create_route_hint_payload_persists_dry_shape(db, admin_client):
    """201 + entry.entry_type=route_hint + payload 落库为 DRY 新形态两字段."""
    ns = Namespace(name="t", slug="t")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    with patch(
        "app.api.knowledge.refine_knowledge",
        return_value=_refine_result("category→product→sku 多跳导航", ""),
    ), patch(
        "app.api.knowledge.detect_conflicts",
        return_value=_conflict_report([]),
    ):
        r = await admin_client.post("/api/knowledge", json={
            "entry_type": "route_hint",
            "content": "category→product→sku 多跳导航",
            "namespace_id": ns.id,
            "payload": {
                "collection_path": [
                    {"database": "shop", "collection": "categories"},
                    {"database": "shop", "collection": "products"},
                    {"database": "shop", "collection": "skus"},
                ],
                "navigation_note": (
                    "category._id ↔ product.categoryId, "
                    "类别在 product.categories[] 数组需 $unwind"
                ),
            },
            "tier": "normal",
        })

    assert r.status_code == 201
    body = r.json()
    assert body["entry"]["entry_type"] == "route_hint"
    assert body["entry"]["status"] == "proposed"
    assert body["entry"]["source"] == "manual"

    stored = json.loads(json.dumps(body["entry"]["payload"]))
    assert set(stored.keys()) == {"collection_path", "navigation_note"}
    assert stored["collection_path"] == [
        {"database": "shop", "collection": "categories"},
        {"database": "shop", "collection": "products"},
        {"database": "shop", "collection": "skus"},
    ]
