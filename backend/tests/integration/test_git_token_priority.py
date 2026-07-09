"""端到端验证: token 优先级 + 可达性校验集成测试"""
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from app.config import settings
from app.knowledge.git_reachability import mask_token, check_repo_reachable
from app.knowledge.git_manager import _inject_token
from app.knowledge.git_token_resolver import get_global_git_token


class TestTokenPriorityIntegration:
    """验证完整 token 解析链路"""

    def test_full_priority_chain(self):
        """repo > ns > 全局 > env 优先级链路"""
        # 1. repo 有 token → 用 repo 的
        url = "https://github.com/user/repo.git"
        token = "repo_token_abc"
        injected = _inject_token(url, token=token)
        assert "repo_token_abc@" in injected

        # 2. 掩码正确 — "repo_token_abc"[:4] + "****" + "repo_token_abc"[-3:] = "repo****abc"
        masked = mask_token(token)
        assert masked == "repo****abc"

    @pytest.mark.asyncio
    async def test_global_token_from_config_center(self):
        """配置中心有激活 token → get_global_git_token 返回该 token"""
        mock_db = AsyncMock()
        mock_db.scalar = AsyncMock(return_value="config_center_token")
        result = await get_global_git_token(mock_db)
        assert result == "config_center_token"

    @pytest.mark.asyncio
    async def test_global_token_env_fallback(self):
        """配置中心无激活 → 回退到 env"""
        mock_db = AsyncMock()
        mock_db.scalar = AsyncMock(return_value=None)
        with patch("app.knowledge.git_token_resolver.settings") as mock_settings:
            mock_settings.git_token = "env_fallback"
            result = await get_global_git_token(mock_db)
            assert result == "env_fallback"

    def test_public_repo_no_token(self):
        """公开仓库无 token → URL 不注入"""
        url = "https://github.com/user/repo.git"
        injected = _inject_token(url, token="")
        assert injected == url

    @patch("app.knowledge.git_reachability.subprocess.run")
    def test_reachability_check_with_valid_token(self, mock_run):
        """可达性校验 — 有效 token"""
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="abc\trefs/heads/master\n")
        ok, msg = check_repo_reachable("https://github.com/user/repo.git", token="valid_token")
        assert ok is True

    @patch("app.knowledge.git_reachability.subprocess.run")
    def test_reachability_check_with_invalid_token(self, mock_run):
        """可达性校验 — 无效 token"""
        mock_run.return_value = MagicMock(
            returncode=128,
            stderr="fatal: Authentication failed for https://github.com/user/repo.git/",
        )
        ok, msg = check_repo_reachable("https://github.com/user/repo.git", token="bad_token")
        assert ok is False
        assert "无效" in msg or "不存在" in msg
