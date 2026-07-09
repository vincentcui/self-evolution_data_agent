"""git_manager — token 参数化测试"""
from app.knowledge.git_manager import _inject_token


class TestInjectToken:
    def test_no_token_returns_original(self):
        url = "https://github.com/user/repo.git"
        assert _inject_token(url, token="") == url

    def test_no_token_none_returns_original(self):
        url = "https://github.com/user/repo.git"
        assert _inject_token(url) == url

    def test_https_with_token(self):
        url = "https://github.com/user/repo.git"
        result = _inject_token(url, token="ghp_abc123")
        assert result == "https://ghp_abc123@github.com/user/repo.git"

    def test_non_https_ignored(self):
        url = "git@github.com:user/repo.git"
        assert _inject_token(url, token="ghp_abc123") == url

    def test_already_has_credentials(self):
        url = "https://user@github.com/user/repo.git"
        assert _inject_token(url, token="ghp_abc123") == url
