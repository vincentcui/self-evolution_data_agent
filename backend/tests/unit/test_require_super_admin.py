"""require_super_admin 依赖测试"""
import pytest
from unittest.mock import MagicMock

from app.auth import require_super_admin, ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_USER


class TestRequireSuperAdmin:
    @pytest.mark.asyncio
    async def test_super_admin_passes(self):
        user = MagicMock(role=ROLE_SUPER_ADMIN)
        result = await require_super_admin(user)
        assert result is user

    @pytest.mark.asyncio
    async def test_admin_rejected(self):
        from fastapi import HTTPException
        user = MagicMock(role=ROLE_ADMIN)
        with pytest.raises(HTTPException) as exc_info:
            await require_super_admin(user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_user_rejected(self):
        from fastapi import HTTPException
        user = MagicMock(role=ROLE_USER)
        with pytest.raises(HTTPException) as exc_info:
            await require_super_admin(user)
        assert exc_info.value.status_code == 403
