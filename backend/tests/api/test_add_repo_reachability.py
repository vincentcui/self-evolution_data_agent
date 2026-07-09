"""add_repo — 可达性校验测试 (含负向路径)"""
from unittest.mock import patch

import pytest

from app.api.knowledge import _enrich_repo_out
from app.models.git_repo import GitRepo
from app.knowledge.git_reachability import check_repo_reachable


class TestAddRepoReachability:
    def test_mask_token_in_enrich_repo_out(self):
        """验证 _enrich_repo_out 包含 git_token_masked"""
        repo = GitRepo(
            id=1, namespace_id=1, url="https://github.com/test/repo.git",
            branch="master", git_token="ghp_abcdefghij",
        )
        out = _enrich_repo_out(repo)
        assert out["git_token_masked"] == "ghp_****hij"

    def test_mask_token_empty_when_no_token(self):
        repo = GitRepo(
            id=1, namespace_id=1, url="https://github.com/test/repo.git",
            branch="master", git_token="",
        )
        out = _enrich_repo_out(repo)
        assert out["git_token_masked"] == ""


class TestAddRepoReachabilityNegative:
    """add_repo 可达性校验负向路径 — mock check_repo_reachable 返回失败."""

    @pytest.mark.asyncio
    async def test_add_repo_auth_failed_returns_422(self, make_client):
        """认证失败 → 422 + 正确错误消息"""
        client = await make_client(role="admin", user_id=1, username="admin")

        with patch("app.api.knowledge.check_repo_reachable") as mock_check:
            mock_check.return_value = (False, "Git 访问令牌无效或仓库不存在")
            resp = await client.post("/api/namespaces/1/repos", json={
                "url": "https://github.com/private/repo.git",
                "branch": "master",
                "git_token": "bad_token",
            })
            assert resp.status_code == 422
            assert "无效" in resp.json()["detail"] or "不存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_add_repo_network_error_returns_422(self, make_client):
        """网络不可达 → 422"""
        client = await make_client(role="admin", user_id=1, username="admin")

        with patch("app.api.knowledge.check_repo_reachable") as mock_check:
            mock_check.return_value = (False, "无法连接 Git 仓库，请检查 URL 和网络")
            resp = await client.post("/api/namespaces/1/repos", json={
                "url": "https://github.com/nonexistent/repo.git",
                "branch": "master",
            })
            assert resp.status_code == 422
            assert "连接" in resp.json()["detail"] or "网络" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_add_repo_timeout_returns_422(self, make_client):
        """超时 → 422"""
        client = await make_client(role="admin", user_id=1, username="admin")

        with patch("app.api.knowledge.check_repo_reachable") as mock_check:
            mock_check.return_value = (False, "Git 仓库连接超时")
            resp = await client.post("/api/namespaces/1/repos", json={
                "url": "https://github.com/slow/repo.git",
                "branch": "master",
                "git_token": "ghp_xxx",
            })
            assert resp.status_code == 422
            assert "超时" in resp.json()["detail"]


class TestTestReachabilityEndpoint:
    """test-reachability 端点负向路径."""

    @pytest.mark.asyncio
    async def test_reachability_endpoint_auth_failure(self, make_client):
        """测试可达性端点 — token 无效"""
        client = await make_client(role="admin", user_id=1, username="admin")

        with patch("app.api.knowledge.check_repo_reachable") as mock_check:
            mock_check.return_value = (False, "Git 访问令牌无效或仓库不存在")
            resp = await client.post("/api/namespaces/1/repos/test-reachability", json={
                "url": "https://github.com/test/repo.git",
                "git_token": "bad_token",
            })
            assert resp.status_code == 200  # test 端点不返回 422
            data = resp.json()
            assert data["success"] is False
            assert "无效" in data["message"] or "不存在" in data["message"]

    @pytest.mark.asyncio
    async def test_reachability_endpoint_success(self, make_client):
        """测试可达性端点 — 仓库可达"""
        client = await make_client(role="admin", user_id=1, username="admin")

        with patch("app.api.knowledge.check_repo_reachable") as mock_check:
            mock_check.return_value = (True, "")
            resp = await client.post("/api/namespaces/1/repos/test-reachability", json={
                "url": "https://github.com/public/repo.git",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert "可达" in data["message"]
