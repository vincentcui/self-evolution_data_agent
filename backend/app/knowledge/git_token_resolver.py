"""全局 Git Token 解析 — 从配置中心 DB 查询已激活 token, 回退到环境变量"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.git_token_config import GitTokenConfig


async def get_global_git_token(db: AsyncSession) -> str:
    """查询配置中心已激活的全局 Git Token, 无则回退到 settings.git_token。

    优先级: 配置中心 (is_active=True) > 环境变量 IS_GIT_TOKEN
    """
    row = await db.scalar(
        select(GitTokenConfig.token).where(
            GitTokenConfig.is_active.is_(True),
            GitTokenConfig.is_deleted.is_(False),
        )
    )
    return row or settings.git_token
