"""git_token_config API 测试"""
from unittest.mock import MagicMock

import pytest

from app.api.git_token_config import _to_out
from app.models.git_token_config import GitTokenConfig
from app.models.user import User


class TestGitTokenConfigOut:
    def test_to_out_masks_token(self):
        cfg = MagicMock()
        cfg.id = 1
        cfg.name = "GitHub PAT"
        cfg.token = "ghp_abcdefghij"
        cfg.description = ""
        cfg.is_active = True
        cfg.created_at = "2026-01-01T00:00:00"
        cfg.updated_at = None
        cfg.created_by = 1
        out = _to_out(cfg)
        assert out.token_masked == "ghp_****hij"

    def test_to_out_empty_token(self):
        cfg = MagicMock()
        cfg.id = 1
        cfg.name = "test"
        cfg.token = ""
        cfg.description = ""
        cfg.is_active = False
        cfg.created_at = "2026-01-01T00:00:00"
        cfg.updated_at = None
        cfg.created_by = None
        out = _to_out(cfg)
        assert out.token_masked == ""


@pytest.mark.asyncio
async def test_created_by_fk_on_delete_set_null(db):
    """删 user 后 git_token_config 存活, created_by=NULL (FK ON DELETE SET NULL).

    回归守卫 (review #1): model 层 ondelete='SET NULL' 必须在 DB 层生效,
    否则删 user 报 IntegrityError (FK NO ACTION). model 层声明 + migration_034
    repair 双保险.
    """
    user = User(username="fk_tester", role="super_admin", password_hash="x")
    db.add(user)
    await db.commit()
    await db.refresh(user)

    cfg = GitTokenConfig(name="test", token="ghp_xxx", created_by=user.id)
    db.add(cfg)
    await db.commit()
    await db.refresh(cfg)
    cfg_id = cfg.id

    await db.delete(user)
    await db.commit()

    # expire 缓存后 fresh query, 确认 DB 层 FK ondelete 行为 (非 session 缓存)
    db.expire_all()
    from sqlalchemy import select
    survived = (await db.execute(
        select(GitTokenConfig).where(GitTokenConfig.id == cfg_id)
    )).scalar_one_or_none()
    assert survived is not None, "config 应存活 (FK SET NULL, 非级联删)"
    assert survived.created_by is None, "created_by 应被 SET NULL"
