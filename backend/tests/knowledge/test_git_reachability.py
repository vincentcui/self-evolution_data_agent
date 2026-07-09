"""git_reachability — 掩码函数 + 仓库可达性校验测试"""
import subprocess
from unittest.mock import patch, MagicMock

from app.knowledge.git_reachability import mask_token, check_repo_reachable


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


class TestCheckRepoReachable:
    @patch("app.knowledge.git_reachability.subprocess.run")
    def test_reachable_public_repo(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="abc123\trefs/heads/master\n")
        ok, msg = check_repo_reachable("https://github.com/user/repo.git", token="")
        assert ok is True
        assert msg == ""

    @patch("app.knowledge.git_reachability.subprocess.run")
    def test_auth_failed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=128, stderr="fatal: Authentication failed for https://github.com/user/repo.git/")
        ok, msg = check_repo_reachable("https://github.com/user/repo.git", token="bad_token")
        assert ok is False
        assert "无效" in msg or "不存在" in msg

    @patch("app.knowledge.git_reachability.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)
        ok, msg = check_repo_reachable("https://github.com/user/repo.git", token="", timeout=10)
        assert ok is False
        assert "超时" in msg

    @patch("app.knowledge.git_reachability.subprocess.run")
    def test_network_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=128, stderr="fatal: unable to access 'https://github.com/': Could not resolve host: github.com")
        ok, msg = check_repo_reachable("https://github.com/user/repo.git", token="")
        assert ok is False
        assert "网络" in msg or "连接" in msg
