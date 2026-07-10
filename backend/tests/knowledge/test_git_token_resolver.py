"""git_token_resolver — 全局 token 查询测试"""
from unittest.mock import AsyncMock, patch

import pytest

from app.knowledge.git_token_resolver import get_global_git_token


class TestGetGlobalGitToken:
    @pytest.mark.asyncio
    async def test_returns_active_config_token(self):
        """配置中心有激活 token → 返回该 token"""
        mock_db = AsyncMock()
        mock_db.scalar = AsyncMock(return_value="config_center_token")

        result = await get_global_git_token(mock_db)
        assert result == "config_center_token"

    @pytest.mark.asyncio
    async def test_falls_back_to_settings_when_no_active(self):
        """配置中心无激活 token → 回退到 settings.git_token"""
        mock_db = AsyncMock()
        mock_db.scalar = AsyncMock(return_value=None)

        with patch("app.knowledge.git_token_resolver.settings") as mock_settings:
            mock_settings.git_token = "env_fallback_token"
            result = await get_global_git_token(mock_db)
            assert result == "env_fallback_token"

    @pytest.mark.asyncio
    async def test_returns_empty_when_all_empty(self):
        """配置中心无激活 + env 也空 → 返回空字符串"""
        mock_db = AsyncMock()
        mock_db.scalar = AsyncMock(return_value=None)

        with patch("app.knowledge.git_token_resolver.settings") as mock_settings:
            mock_settings.git_token = ""
            result = await get_global_git_token(mock_db)
            assert result == ""
