"""add_repo — 可达性校验测试 (含负向路径)"""
from unittest.mock import patch

import pytest
import pytest_asyncio

from app.api.knowledge import _enrich_repo_out
from app.models.git_repo import GitRepo
from app.models.namespace import Namespace


@pytest_asyncio.fixture
async def ns_id(db):
    """创建测试命名空间并返回 id."""
    ns = Namespace(name="test-reachability", slug="test-reachability")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    return ns.id


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
    """可达性校验失败 → 阻止添加, 返回 422."""

    @pytest.mark.asyncio
    async def test_add_repo_auth_failed_422(self, make_client, ns_id):
        """认证失败 → 422"""
        client = await make_client(role="super_admin", user_id=1, username="admin")

        with patch("app.api.knowledge.check_repo_reachable") as mock_check:
            mock_check.return_value = (False, "Git 访问令牌无效或仓库不存在")
            resp = await client.post(f"/api/namespaces/{ns_id}/repos", json={
                "url": "https://github.com/private/repo.git",
                "branch": "master",
                "git_token": "bad_token",
            })
            assert resp.status_code == 422
            assert "无效" in resp.json()["detail"] or "不存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_add_repo_no_token_private_422(self, make_client, ns_id):
        """无 token 访问私有仓库 → 422 提示需要 token"""
        client = await make_client(role="super_admin", user_id=1, username="admin")

        with patch("app.api.knowledge.check_repo_reachable") as mock_check:
            mock_check.return_value = (False, "私有仓库需要配置 Git 访问令牌")
            resp = await client.post(f"/api/namespaces/{ns_id}/repos", json={
                "url": "https://github.com/private/repo.git",
                "branch": "master",
            })
            assert resp.status_code == 422
            assert "私有仓库" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_add_repo_network_error_422(self, make_client, ns_id):
        """网络不可达 → 422"""
        client = await make_client(role="super_admin", user_id=1, username="admin")

        with patch("app.api.knowledge.check_repo_reachable") as mock_check:
            mock_check.return_value = (False, "无法连接 Git 仓库，请检查 URL 和网络")
            resp = await client.post(f"/api/namespaces/{ns_id}/repos", json={
                "url": "https://github.com/nonexistent/repo.git",
                "branch": "master",
            })
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_add_repo_timeout_422(self, make_client, ns_id):
        """超时 → 422"""
        client = await make_client(role="super_admin", user_id=1, username="admin")

        with patch("app.api.knowledge.check_repo_reachable") as mock_check:
            mock_check.return_value = (False, "Git 仓库连接超时")
            resp = await client.post(f"/api/namespaces/{ns_id}/repos", json={
                "url": "https://github.com/slow/repo.git",
                "branch": "master",
                "git_token": "ghp_xxx",
            })
            assert resp.status_code == 422
            assert "超时" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_add_repo_ssh_url_422(self, make_client, ns_id):
        """SSH URL → 422 (真实 check_repo_reachable 不 mock, git@ 不打网络直接拒)"""
        client = await make_client(role="super_admin", user_id=1, username="admin")
        resp = await client.post(f"/api/namespaces/{ns_id}/repos", json={
            "url": "git@gitlab.example.com:u/r.git",
            "branch": "master",
        })
        assert resp.status_code == 422
        assert "SSH" in resp.json()["detail"]


class TestTestReachabilityEndpoint:
    """test-reachability 端点负向路径."""

    @pytest.mark.asyncio
    async def test_reachability_endpoint_auth_failure(self, make_client, ns_id):
        """测试可达性端点 — token 无效"""
        client = await make_client(role="super_admin", user_id=1, username="admin")

        with patch("app.api.knowledge.check_repo_reachable") as mock_check:
            mock_check.return_value = (False, "Git 访问令牌无效或仓库不存在")
            resp = await client.post(f"/api/namespaces/{ns_id}/repos/test-reachability", json={
                "url": "https://github.com/test/repo.git",
                "git_token": "bad_token",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False
            assert "无效" in data["message"] or "不存在" in data["message"]

    @pytest.mark.asyncio
    async def test_reachability_endpoint_success(self, make_client, ns_id):
        """测试可达性端点 — 仓库可达"""
        client = await make_client(role="super_admin", user_id=1, username="admin")

        with patch("app.api.knowledge.check_repo_reachable") as mock_check:
            mock_check.return_value = (True, "")
            resp = await client.post(f"/api/namespaces/{ns_id}/repos/test-reachability", json={
                "url": "https://github.com/public/repo.git",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert "可达" in data["message"]
