"""测试 namespace 删除后 registry 内存槽被主动清除."""
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_delete_namespace_clears_registry_slot(admin_client):
    """删除 namespace (dry_run=False) 时应调用 registry.refresh_chat(None, namespace_id=ns_id)."""
    ns_resp = await admin_client.post(
        "/api/namespaces",
        json={"name": "test-ns-cleanup", "slug": "test-ns-cleanup", "description": "test"},
    )
    ns_id = ns_resp.json()["id"]

    with patch("app.engine.model_registry.registry.refresh_chat") as mock_refresh:
        resp = await admin_client.delete(
            f"/api/namespaces/{ns_id}?dry_run=false"
        )
        assert resp.status_code in (200, 204)
        mock_refresh.assert_called_with(None, namespace_id=ns_id)
