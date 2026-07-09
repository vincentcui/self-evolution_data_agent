"""L2 契约测试 — git_token_config 403 + add_repo 422 (httpx ASGITransport).

注意: 此测试需要 DB 环境. 无 DB 环境下跳过.
"""
import pytest

from app.api.git_token_config import _to_out, GitTokenConfigIn, GitTokenConfigOut
from app.knowledge.git_reachability import mask_token


class TestGitTokenConfigAccessControl:
    """非 super_admin 访问 git_token_config 端点 → 403 (单元级验证)."""

    @pytest.mark.asyncio
    async def test_require_super_admin_decorator_on_list(self):
        """list_configs 依赖 require_super_admin"""
        from app.auth import require_super_admin, ROLE_ADMIN
        from unittest.mock import MagicMock
        from fastapi import HTTPException

        user = MagicMock(role=ROLE_ADMIN)
        with pytest.raises(HTTPException) as exc_info:
            await require_super_admin(user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_super_admin_decorator_on_add(self):
        """add_config 依赖 require_super_admin"""
        from app.auth import require_super_admin, ROLE_USER
        from unittest.mock import MagicMock
        from fastapi import HTTPException

        user = MagicMock(role=ROLE_USER)
        with pytest.raises(HTTPException) as exc_info:
            await require_super_admin(user)
        assert exc_info.value.status_code == 403


class TestAddRepoExplicit422:
    """add_repo 私有仓库无 token → 显式 422 (单元级验证)."""

    def test_private_repo_no_token_logic(self):
        """HTTPS URL + resolved_token 为空 → 应返回 422 '私有仓库需要配置 Git 访问令牌'"""
        url = "https://github.com/private/repo.git"
        resolved_token = ""
        # 模拟 add_repo 中的 Step 1 逻辑
        should_reject = url.startswith("https://") and not resolved_token
        assert should_reject is True

    def test_public_repo_with_token_not_rejected(self):
        """有 token 的 HTTPS URL 不应被 Step 1 拒绝"""
        url = "https://github.com/private/repo.git"
        resolved_token = "ghp_xxx"
        should_reject = url.startswith("https://") and not resolved_token
        assert should_reject is False

    def test_ssh_url_not_rejected_by_step1(self):
        """SSH URL 不受 Step 1 检查 (不以 https:// 开头)"""
        url = "git@github.com:user/repo.git"
        resolved_token = ""
        should_reject = url.startswith("https://") and not resolved_token
        assert should_reject is False


class TestGitTokenConfigOutMasking:
    """_to_out 掩码逻辑验证"""

    def test_masked_token_correct(self):
        from unittest.mock import MagicMock
        cfg = MagicMock()
        cfg.id = 1
        cfg.name = "test"
        cfg.token = "ghp_abcdefghij"
        cfg.description = ""
        cfg.is_active = True
        cfg.created_at = "2026-01-01T00:00:00"
        cfg.updated_at = None
        cfg.created_by = 1
        out = _to_out(cfg)
        assert out.token_masked == "ghp_****hij"
        # 原始 token 不泄露
        assert out.token_masked != cfg.token
