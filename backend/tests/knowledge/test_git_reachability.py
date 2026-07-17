"""git_reachability — 掩码函数 + 仓库可达性校验测试"""
from unittest.mock import MagicMock, patch

import httpx

from app.knowledge.git_reachability import check_repo_reachable, mask_token


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
    """SSH 协议未启用 — 明确报错, 不静默放行 (容器无 ssh 客户端)."""

    def test_ssh_git_at_rejected(self):
        ok, msg = check_repo_reachable("git@github.com:user/repo.git", token="")
        assert ok is False
        assert "SSH" in msg

    def test_ssh_protocol_rejected(self):
        ok, msg = check_repo_reachable("ssh://git@gitlab.example.com/u/r.git", token="tok")
        assert ok is False
        assert "SSH" in msg


class TestUnsupportedProtocol:
    """非 http(s)/ssh 协议 → 明确报错."""

    def test_ftp_rejected(self):
        ok, msg = check_repo_reachable("ftp://gitlab.example.com/u/r.git", token="tok")
        assert ok is False
        assert "不支持" in msg


class TestGenericHttp:
    """http:// (非 TLS) 同样走 HEAD 真校验 (内网明文 gitlab 修复点)."""

    def test_http_reachable(self):
        with patch("app.knowledge.git_reachability._http.head") as mock_head:
            mock_head.return_value = _resp(200, is_success=True)
            ok, _ = check_repo_reachable("http://gitlab.example.com/u/r.git", token="tok")
            assert ok is True

    def test_http_auth_failed(self):
        with patch("app.knowledge.git_reachability._http.head") as mock_head:
            mock_head.return_value = _resp(401)
            ok, msg = check_repo_reachable("http://gitlab.example.com/u/r.git", token="bad")
            assert ok is False
            assert "无效" in msg or "不存在" in msg
