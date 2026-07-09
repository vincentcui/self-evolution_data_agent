"""git_reachability — 掩码函数 + 仓库可达性校验测试"""
from unittest.mock import patch, MagicMock

import httpx

from app.knowledge.git_reachability import mask_token, check_repo_reachable


def _resp(status_code: int, is_success: bool = False, is_redirect: bool = False):
    m = MagicMock()
    m.status_code = status_code
    m.is_success = is_success
    m.is_redirect = is_redirect
    return m


class TestMaskToken:
    def test_empty_token(self):
        assert mask_token("") == ""

    def test_short_token_all_masked(self):
        assert mask_token("abc") == "****"

    def test_exactly_7_chars_all_masked(self):
        assert mask_token("1234567") == "****"

    def test_long_token_prefix_suffix(self):
        assert mask_token("ghp_abcdefghij") == "ghp_****hij"

    def test_exactly_8_chars(self):
        assert mask_token("12345678") == "1234****678"


class TestGitHubApi:
    """GitHub URL → 走 API (/repos/{owner}/{repo})."""

    def test_public_repo_no_token(self):
        with patch("app.knowledge.git_reachability._http.get") as mock_get:
            mock_get.return_value = _resp(200, is_success=True)
            ok, _ = check_repo_reachable("https://github.com/octocat/Hello-World.git", token="")
            assert ok is True

    def test_private_repo_valid_token(self):
        with patch("app.knowledge.git_reachability._http.get") as mock_get:
            mock_get.return_value = _resp(200, is_success=True)
            ok, _ = check_repo_reachable("https://github.com/user/private.git", token="valid_token")
            assert ok is True

    def test_auth_failed(self):
        with patch("app.knowledge.git_reachability._http.get") as mock_get:
            mock_get.return_value = _resp(401)
            ok, msg = check_repo_reachable("https://github.com/user/repo.git", token="bad_token")
            assert ok is False
            assert "无效" in msg or "不存在" in msg

    def test_403_forbidden(self):
        with patch("app.knowledge.git_reachability._http.get") as mock_get:
            mock_get.return_value = _resp(403)
            ok, _ = check_repo_reachable("https://github.com/user/repo.git", token="bad_token")
            assert ok is False

    def test_private_no_token(self):
        with patch("app.knowledge.git_reachability._http.get") as mock_get:
            mock_get.return_value = _resp(404)
            ok, msg = check_repo_reachable("https://github.com/user/private.git", token="")
            assert ok is False
            assert "私有仓库" in msg

    def test_not_found_with_token(self):
        with patch("app.knowledge.git_reachability._http.get") as mock_get:
            mock_get.return_value = _resp(404)
            ok, msg = check_repo_reachable("https://github.com/user/repo.git", token="valid_token")
            assert ok is False
            assert "不存在" in msg or "无权" in msg

    def test_timeout(self):
        with patch("app.knowledge.git_reachability._http.get") as mock_get:
            mock_get.side_effect = httpx.TimeoutException("timeout")
            ok, msg = check_repo_reachable("https://github.com/user/repo.git", timeout=10)
            assert ok is False
            assert "超时" in msg

    def test_connect_error(self):
        with patch("app.knowledge.git_reachability._http.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("failed")
            ok, msg = check_repo_reachable("https://github.com/user/repo.git", token="")
            assert ok is False


class TestGenericHttps:
    """非 GitHub HTTPS URL → 走 HEAD 请求."""

    def test_reachable(self):
        with patch("app.knowledge.git_reachability._http.head") as mock_head:
            mock_head.return_value = _resp(200, is_success=True)
            ok, _ = check_repo_reachable("https://gitlab.com/user/repo.git", token="")
            assert ok is True

    def test_auth_failed(self):
        with patch("app.knowledge.git_reachability._http.head") as mock_head:
            mock_head.return_value = _resp(401)
            ok, msg = check_repo_reachable("https://gitlab.com/user/repo.git", token="bad")
            assert ok is False

    def test_404_no_token(self):
        with patch("app.knowledge.git_reachability._http.head") as mock_head:
            mock_head.return_value = _resp(404)
            ok, msg = check_repo_reachable("https://gitlab.com/user/repo.git", token="")
            assert ok is False
            assert "私有仓库" in msg

    def test_404_with_token(self):
        with patch("app.knowledge.git_reachability._http.head") as mock_head:
            mock_head.return_value = _resp(404)
            ok, msg = check_repo_reachable("https://gitlab.com/user/repo.git", token="tok")
            assert ok is False
            assert "不存在" in msg or "无权" in msg

    def test_timeout(self):
        with patch("app.knowledge.git_reachability._http.head") as mock_head:
            mock_head.side_effect = httpx.TimeoutException("timeout")
            ok, msg = check_repo_reachable("https://gitlab.com/user/repo.git", timeout=10)
            assert ok is False
            assert "超时" in msg

    def test_connect_error(self):
        with patch("app.knowledge.git_reachability._http.head") as mock_head:
            mock_head.side_effect = httpx.ConnectError("failed")
            ok, msg = check_repo_reachable("https://gitlab.com/user/repo.git", token="")
            assert ok is False


class TestSsh:
    def test_ssh_url_skips(self):
        ok, _ = check_repo_reachable("git@github.com:user/repo.git", token="")
        assert ok is True
