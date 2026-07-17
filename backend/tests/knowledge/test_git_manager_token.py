"""
git_manager — _inject_token + _format_userinfo 契约测试.

覆盖:
- _format_userinfo: host label 精确匹配 gitlab → oauth2:{token}, 其他 → 裸 {token}
- _inject_token: http/https 注入 + host 推断 + git@/ssh 抛 ValueError
                 + 空 token 透传 + 重复注入守卫 + 端口保留
"""
import pytest

from app.knowledge.git_manager import _format_userinfo, _inject_token

# ──────────────────────────────────────────────────────────────
# _format_userinfo: host 推断 (不绑死具体 host)
# ──────────────────────────────────────────────────────────────

class TestFormatUserinfo:
    @pytest.mark.parametrize("host", [
        "gitlab.com",
        "gitlab.example.com",
        "gitlab.company.com",
        "corp.gitlab.io",
    ])
    def test_gitlab_host_use_oauth2(self, host):
        """GitLab 实例 (label 精确含 gitlab) → oauth2:{token}"""
        assert _format_userinfo(host, "tok") == "oauth2:tok"

    @pytest.mark.parametrize("host", [
        "github.com",
        "gitee.com",
        "example.com",
        "my-gitlab.com",   # label 是 my-gitlab, 不命中
        "gitlabfoo.com",   # label 是 gitlabfoo, 不命中
        "",
    ])
    def test_non_gitlab_host_use_bare_token(self, host):
        """非 GitLab → 裸 {token} (与旧逻辑一致, 零回归)"""
        assert _format_userinfo(host, "tok") == "tok"


# ──────────────────────────────────────────────────────────────
# _inject_token: 协议 + host 矩阵
# ──────────────────────────────────────────────────────────────

class TestInjectToken:
    def test_no_token_returns_original(self):
        url = "https://github.com/user/repo.git"
        assert _inject_token(url, token="") == url

    def test_no_token_none_returns_original(self):
        url = "https://github.com/user/repo.git"
        assert _inject_token(url) == url

    def test_https_github_bare_token(self):
        url = "https://github.com/user/repo.git"
        result = _inject_token(url, token="ghp_abc123")
        assert result == "https://ghp_abc123@github.com/user/repo.git"

    def test_https_gitlab_oauth2(self):
        """GitLab 实例 → oauth2:{token} (GitLab PAT 标准格式)"""
        assert _inject_token("https://gitlab.example.com/u/r.git", token="tok") \
            == "https://oauth2:tok@gitlab.example.com/u/r.git"

    def test_http_gitlab_oauth2(self):
        """http:// 同样注入 (内网明文 gitlab 核心修复点)"""
        assert _inject_token("http://gitlab.example.com/u/r.git", token="tok") \
            == "http://oauth2:tok@gitlab.example.com/u/r.git"

    def test_http_github_bare_token(self):
        assert _inject_token("http://github.com/u/r.git", token="tok") \
            == "http://tok@github.com/u/r.git"

    def test_gitee_bare_token(self):
        assert _inject_token("https://gitee.com/u/r.git", token="tok") \
            == "https://tok@gitee.com/u/r.git"

    def test_port_preserved(self):
        """netloc 含端口时保留"""
        assert _inject_token("http://gitlab.example.com:8080/u/r.git", token="tok") \
            == "http://oauth2:tok@gitlab.example.com:8080/u/r.git"

    def test_already_has_credentials(self):
        url = "https://user@github.com/user/repo.git"
        assert _inject_token(url, token="ghp_abc123") == url

    def test_already_injected_oauth2_guard(self):
        """URL 已含 oauth2: 凭据时不重复注入"""
        assert _inject_token("https://oauth2:tok@gitlab.example.com/u/r.git", token="new") \
            == "https://oauth2:tok@gitlab.example.com/u/r.git"

    @pytest.mark.parametrize("url", [
        "git@github.com:user/repo.git",
        "git@gitlab.example.com:u/r.git",
        "ssh://git@gitlab.example.com/u/r.git",
        "git+ssh://git@gitlab.example.com/u/r.git",
    ])
    def test_ssh_protocol_raises(self, url):
        """SSH 协议未启用, 抛 ValueError 含 'SSH'"""
        with pytest.raises(ValueError, match="SSH"):
            _inject_token(url, token="tok")

    def test_unsupported_protocol_raises(self):
        with pytest.raises(ValueError, match="不支持"):
            _inject_token("ftp://gitlab.example.com/u/r.git", token="tok")

    def test_ssh_empty_token_passthrough(self):
        """空 token + git@ 原样返回 (公开仓库不因协议阻断, 由可达性校验拦截)"""
        assert _inject_token("git@gitlab.example.com:u/r.git", token="") \
            == "git@gitlab.example.com:u/r.git"
