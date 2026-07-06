"""check-ready 端点和 registry ready 语义测试."""
from __future__ import annotations

import pytest

from app.models.model_config import ModelConfig


@pytest.mark.asyncio
async def test_check_ready_false_when_no_active_config(make_client, db):
    """DB 无 active config 时 check-ready.ready=false."""
    client = await make_client(role="super_admin")
    resp = await client.get("/api/model-config/check-ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False


@pytest.mark.asyncio
async def test_check_ready_chat_true_after_active_chat(make_client, db):
    """DB 有 active CHAT config 后 chat_model_ready=true."""
    row = ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="test-key",
        model_name="test-chat", model_type="CHAT",
        is_active=True, is_deleted=False,
    )
    db.add(row)
    await db.commit()

    client = await make_client(role="super_admin")
    resp = await client.get("/api/model-config/check-ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["chat_model_ready"] is True
    assert data["embedding_model_ready"] is False
    assert data["ready"] is False
