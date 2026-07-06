"""模型配置审计日志写入测试."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.model_config import ModelConfig
from app.models.model_config_audit_log import ModelConfigAuditLog


@pytest.mark.asyncio
async def test_add_config_writes_create_audit(make_client, db):
    """新增配置写 create 审计."""
    client = await make_client(role="super_admin", user_id=1)
    resp = await client.post("/api/model-config/add", json={
        "provider": "openai",
        "base_url": "https://example.invalid/v1",
        "api_key": "sk-test-audit-create",
        "model_name": "gpt-audit-create",
        "model_type": "CHAT",
        "protocol": "openai",
    })
    assert resp.status_code == 201
    config_id = resp.json()["id"]

    logs = (await db.execute(
        select(ModelConfigAuditLog).where(
            ModelConfigAuditLog.config_id == config_id,
            ModelConfigAuditLog.action == "create",
        )
    )).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.action == "create"
    assert log.model_type == "CHAT"
    assert log.provider == "openai"
    assert log.before_json is None
    assert log.after_json is not None
    # 确认 after_json 不含明文 api_key
    assert "sk-test-audit-create" not in log.after_json


@pytest.mark.asyncio
async def test_update_config_writes_update_audit(make_client, db):
    """更新配置写 update 审计（不含明文 key）."""
    # 先新增一条配置
    row = ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="sk-original-key",
        model_name="gpt-before-update", model_type="CHAT",
        is_active=False, is_deleted=False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    client = await make_client(role="super_admin", user_id=1)
    resp = await client.put("/api/model-config/update", json={
        "id": row.id,
        "provider": "openai",
        "base_url": "https://example.invalid/v1",
        "api_key": "****",  # 打码 key，不更新
        "model_name": "gpt-after-update",
        "model_type": "CHAT",
        "protocol": "openai",
    })
    assert resp.status_code == 200

    logs = (await db.execute(
        select(ModelConfigAuditLog).where(
            ModelConfigAuditLog.config_id == row.id,
            ModelConfigAuditLog.action == "update",
        )
    )).scalars().all()
    assert len(logs) == 1
    audit = logs[0]
    assert audit.before_json is not None
    assert audit.after_json is not None
    # before 应含旧 model_name
    assert "gpt-before-update" in audit.before_json
    # after 应含新 model_name
    assert "gpt-after-update" in audit.after_json
    # 不含明文 api_key
    assert "sk-original-key" not in audit.before_json
    assert "sk-original-key" not in audit.after_json


@pytest.mark.asyncio
async def test_delete_config_writes_delete_audit(make_client, db):
    """删除配置写 delete 审计."""
    row = ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="sk-delete-test",
        model_name="gpt-to-delete", model_type="CHAT",
        is_active=False, is_deleted=False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    config_id = row.id

    client = await make_client(role="super_admin", user_id=1)
    resp = await client.delete(f"/api/model-config/{config_id}")
    assert resp.status_code == 204

    logs = (await db.execute(
        select(ModelConfigAuditLog).where(
            ModelConfigAuditLog.config_id == config_id,
            ModelConfigAuditLog.action == "delete",
        )
    )).scalars().all()
    assert len(logs) == 1
    audit = logs[0]
    assert audit.before_json is not None
    assert audit.after_json is None
    # 不含明文 api_key
    assert "sk-delete-test" not in audit.before_json


@pytest.mark.asyncio
async def test_activate_config_writes_activate_audit(make_client, db):
    """激活配置写 activate 审计."""
    row = ModelConfig(
        provider="openai", protocol="openai",
        base_url="https://example.invalid/v1", api_key="sk-activate-test",
        model_name="gpt-to-activate", model_type="CHAT",
        is_active=False, is_deleted=False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    config_id = row.id

    client = await make_client(role="super_admin", user_id=1)
    resp = await client.post(f"/api/model-config/activate/{config_id}")
    assert resp.status_code == 200

    logs = (await db.execute(
        select(ModelConfigAuditLog).where(
            ModelConfigAuditLog.config_id == config_id,
            ModelConfigAuditLog.action == "activate",
        )
    )).scalars().all()
    assert len(logs) == 1
    audit = logs[0]
    assert audit.action == "activate"
    assert audit.after_json is not None
    # 不含明文 api_key
    assert "sk-activate-test" not in audit.after_json
