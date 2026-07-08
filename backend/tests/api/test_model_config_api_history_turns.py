"""模型管理 API 暴露 max_history_turns 字段 — add/update/list 契约测试."""
from __future__ import annotations

import pytest


def _chat_body(**over):
    body = {
        "provider": "openai", "base_url": "http://x", "api_key": "sk-x",
        "model_name": "gpt-4o", "model_type": "CHAT", "protocol": "openai",
        "temperature": 0.0, "max_tokens": 12288,
    }
    body.update(over)
    return body


@pytest.mark.asyncio
async def test_create_defaults_max_history_turns_5(make_client):
    client = await make_client(role="super_admin", user_id=1)
    resp = await client.post("/api/model-config/add", json=_chat_body())
    assert resp.status_code == 201, resp.text
    assert resp.json()["max_history_turns"] == 5


@pytest.mark.asyncio
async def test_create_with_custom_max_history_turns(make_client):
    client = await make_client(role="super_admin", user_id=1)
    resp = await client.post("/api/model-config/add", json=_chat_body(max_history_turns=10))
    assert resp.status_code == 201
    assert resp.json()["max_history_turns"] == 10


@pytest.mark.asyncio
async def test_update_max_history_turns(make_client):
    client = await make_client(role="super_admin", user_id=1)
    r1 = await client.post("/api/model-config/add", json=_chat_body())
    cid = r1.json()["id"]
    r2 = await client.put("/api/model-config/update",
                          json=_chat_body(id=cid, max_history_turns=8))
    assert r2.status_code == 200, r2.text
    assert r2.json()["max_history_turns"] == 8


@pytest.mark.asyncio
async def test_list_returns_max_history_turns(make_client):
    client = await make_client(role="super_admin", user_id=1)
    await client.post("/api/model-config/add", json=_chat_body(max_history_turns=7))
    r = await client.get("/api/model-config/list")
    assert r.status_code == 200
    assert any(c["max_history_turns"] == 7 for c in r.json())
