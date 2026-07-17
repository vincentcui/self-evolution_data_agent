"""L2 契约测试 — git_token_config 403 + add_repo 422 (httpx ASGITransport).

使用项目 make_client fixture (override get_current_user), 真实路由 + 真实 require_super_admin 判定 + savepoint 隔离。
"""
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def super_client(make_client):
    """super_admin fixture — 可访问 git_token_config 端点"""
    return await make_client(role="super_admin", user_id=1, username="admin")


@pytest_asyncio.fixture
async def admin_client(make_client):
    """普通 admin fixture — 不可访问 git_token_config 端点"""
    return await make_client(role="admin", user_id=2, username="normal_admin")


@pytest_asyncio.fixture
async def user_client(make_client):
    """普通 user fixture — 不可访问 git_token_config 端点"""
    return await make_client(role="user", user_id=3, username="normal_user")


class TestGitTokenConfigAccessControl:
    """非 super_admin 访问 git_token_config 端点 → 403."""

    @pytest.mark.asyncio
    async def test_admin_list_returns_403(self, admin_client):
        resp = await admin_client.get("/api/git-token-config/list")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_user_list_returns_403(self, user_client):
        resp = await user_client.get("/api/git-token-config/list")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_super_admin_list_returns_200(self, super_client):
        resp = await super_client.get("/api/git-token-config/list")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_add_returns_403(self, admin_client):
        resp = await admin_client.post("/api/git-token-config/add", json={
            "name": "test", "token": "ghp_xxx", "description": "test",
        })
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_user_activate_returns_403(self, user_client):
        resp = await user_client.post("/api/git-token-config/activate/1")
        assert resp.status_code == 403


class TestGitTokenConfigCRUDLifecycle:
    """super_admin 完整 CRUD + 激活流程."""

    @pytest.mark.asyncio
    async def test_create_activate_delete(self, super_client):
        # 新增
        create_resp = await super_client.post("/api/git-token-config/add", json={
            "name": "l2-contract-test",
            "token": "ghp_l2_test_token_xxx",
            "description": "L2 contract test",
        })
        assert create_resp.status_code == 201
        created = create_resp.json()
        cfg_id = created["id"]
        assert created["name"] == "l2-contract-test"
        assert created["token_masked"] != "ghp_l2_test_token_xxx"  # 脱敏
        assert created["is_active"] is False

        # 激活
        activate_resp = await super_client.post(f"/api/git-token-config/activate/{cfg_id}")
        assert activate_resp.status_code == 200
        assert activate_resp.json()["is_active"] is True

        # 列表确认
        list_resp = await super_client.get("/api/git-token-config/list")
        assert list_resp.status_code == 200
        found = [c for c in list_resp.json() if c["id"] == cfg_id]
        assert len(found) == 1
        assert found[0]["is_active"] is True

        # 更新 (带 **** 跳过 token)
        update_resp = await super_client.put("/api/git-token-config/update", json={
            "id": cfg_id,
            "name": "l2-contract-test-updated",
            "token": "****",
            "description": "updated description",
        })
        assert update_resp.status_code == 200
        assert update_resp.json()["name"] == "l2-contract-test-updated"
        assert update_resp.json()["description"] == "updated description"

        # 删除 (逻辑删除)
        delete_resp = await super_client.delete(f"/api/git-token-config/{cfg_id}")
        assert delete_resp.status_code == 204

        # 删除后列表不包含
        list_after = await super_client.get("/api/git-token-config/list")
        assert list_after.status_code == 200
        assert not any(c["id"] == cfg_id for c in list_after.json())


class TestGitTokenConfigOutMasking:
    """_to_out 掩码逻辑验证 (单元级, 无需 client)."""

    def test_masked_token_correct(self):
        from unittest.mock import MagicMock

        from app.api.git_token_config import _to_out

        cfg = MagicMock()
        cfg.id = 1
        cfg.name = "test"
        cfg.token = "ghp_abcdefghij"
        cfg.description = ""
        cfg.is_active = True
        cfg.created_at = "2026-01-01T00:00:00"
        cfg.updated_at = None
        cfg.created_by = 1
        out = _to_out(cfg)
        assert out.token_masked == "ghp_****hij"
        assert out.token_masked != cfg.token  # 明文不泄露


class TestAddRepoReachabilityLogic:
    """add_repo 可达性校验逻辑验证 — 不满足条件时阻止添加."""

    def _resp(self, status_code: int, is_success: bool = False, is_redirect: bool = False):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.status_code = status_code
        m.is_success = is_success
        m.is_redirect = is_redirect
        return m

    def test_public_repo_no_token_allowed(self):
        """公开仓库无 token → check_repo_reachable 返回 True (GitHub API 200)"""
        from unittest.mock import patch

        from app.knowledge.git_reachability import check_repo_reachable

        with patch("app.knowledge.git_reachability._http.get") as mock_get:
            mock_get.return_value = self._resp(200, is_success=True)
            ok, _ = check_repo_reachable("https://github.com/public/repo.git", token="")
            assert ok is True

    def test_private_repo_no_token_blocked(self):
        """私有仓库无 token → check_repo_reachable 返回 False (GitHub API 404)"""
        from unittest.mock import patch

        from app.knowledge.git_reachability import check_repo_reachable

        with patch("app.knowledge.git_reachability._http.get") as mock_get:
            mock_get.return_value = self._resp(404)
            ok, msg = check_repo_reachable("https://github.com/private/repo.git", token="")
            assert ok is False
            assert "私有仓库" in msg

    def test_ssh_url_blocked(self):
        """SSH URL → check_repo_reachable 返回 False (SSH 协议未启用)"""
        from app.knowledge.git_reachability import check_repo_reachable
        ok, msg = check_repo_reachable("git@github.com:user/repo.git", token="")
        assert ok is False
        assert "SSH" in msg
