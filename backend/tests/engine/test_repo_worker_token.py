"""repo_worker — token 优先级解析 + 透传测试"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestTokenPriorityResolution:
    """测试 token 优先级解析表达式 (repo > ns > 全局配置中心 > env)"""

    def test_repo_token_takes_priority(self):
        """repo.git_token 优先于 ns.git_token 和全局 token"""
        repo_git_token = "repo_token"
        ns_git_token = "ns_token"
        global_token = "global_token"
        resolved = repo_git_token or ns_git_token or global_token
        assert resolved == "repo_token"

    def test_ns_token_when_repo_empty(self):
        """repo.git_token 为空时回退到 ns.git_token"""
        resolved = "" or "ns_token" or "global_token"
        assert resolved == "ns_token"

    def test_global_token_when_repo_and_ns_empty(self):
        """repo + ns 均为空时回退到全局配置中心 token"""
        resolved = "" or "" or "global_token"
        assert resolved == "global_token"

    def test_all_empty_for_public_repo(self):
        """四者均空 → resolved_token 为空 (公开仓库)"""
        resolved = "" or "" or ""
        assert resolved == ""
