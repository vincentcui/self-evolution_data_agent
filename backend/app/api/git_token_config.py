"""全局 Git Token 配置中心 API — CRUD + 激活 + 测试可达性 (super_admin only)。

端点列表:
  GET    /api/git-token-config/list           列表 (token 脱敏)
  POST   /api/git-token-config/add            新增
  PUT    /api/git-token-config/update         更新 (**** 跳过 token 更新)
  DELETE /api/git-token-config/{id}           逻辑删除
  POST   /api/git-token-config/activate/{id}  激活 (先禁用其他)
  POST   /api/git-token-config/test           测试 token 可达性 (不入库)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_super_admin
from app.db.metadata import get_db
from app.knowledge.git_reachability import check_repo_reachable, mask_token
from app.models.base import local_now
from app.models.git_token_config import GitTokenConfig
from app.models.user import User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/git-token-config", tags=["git-token-config"])

_MASK = "****"


class GitTokenConfigIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    token: str = Field(..., min_length=1)
    description: str = ""


class GitTokenConfigUpdate(BaseModel):
    id: int
    name: str = Field(..., min_length=1, max_length=128)
    token: str = Field(..., min_length=1)
    description: str = ""


class GitTokenConfigOut(BaseModel):
    id: int
    name: str
    token_masked: str
    description: str
    is_active: bool
    created_at: datetime
    updated_at: datetime | None
    created_by: int | None

    model_config = {"from_attributes": True}


class GitTokenTestBody(BaseModel):
    id: int | None = None   # 已有配置的 id (从 DB 取真实 token, 前端 token 被打码)
    url: str = Field(..., min_length=1)


def _to_out(row: GitTokenConfig) -> GitTokenConfigOut:
    return GitTokenConfigOut(
        id=row.id,
        name=row.name,
        token_masked=mask_token(row.token),
        description=row.description,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
        created_by=row.created_by,
    )


async def _get_or_404(db: AsyncSession, config_id: int) -> GitTokenConfig:
    row = await db.get(GitTokenConfig, config_id)
    if not row or row.is_deleted:
        raise HTTPException(404, "Git Token 配置不存在")
    return row


@router.get("/list", response_model=list[GitTokenConfigOut])
async def list_configs(
    user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """未删除的全局 Git Token 配置列表 (token 脱敏)。"""
    rows = (await db.execute(
        select(GitTokenConfig)
        .where(GitTokenConfig.is_deleted.is_(False))
        .order_by(GitTokenConfig.id)
    )).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("/add", response_model=GitTokenConfigOut, status_code=201)
async def add_config(
    body: GitTokenConfigIn,
    user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """新增全局 Git Token 配置 (不自动激活)。"""
    row = GitTokenConfig(
        name=body.name.strip(),
        token=body.token.strip(),
        description=body.description,
        is_active=False,
        is_deleted=False,
        created_by=user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    log.info("[git_token_config] 新增 id=%d name=%s", row.id, row.name)
    return _to_out(row)


@router.put("/update", response_model=GitTokenConfigOut)
async def update_config(
    body: GitTokenConfigUpdate,
    user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新配置。token 传 **** 则跳过更新 (保留原值)。"""
    row = await _get_or_404(db, body.id)
    row.name = body.name.strip()
    if _MASK not in body.token.strip():
        row.token = body.token.strip()
    row.description = body.description
    row.updated_at = local_now()
    await db.commit()
    await db.refresh(row)
    return _to_out(row)


@router.delete("/{config_id}", status_code=204)
async def delete_config(
    config_id: int,
    user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """逻辑删除。"""
    row = await _get_or_404(db, config_id)
    row.is_deleted = True
    row.is_active = False
    row.updated_at = local_now()
    await db.commit()


@router.post("/activate/{config_id}", response_model=GitTokenConfigOut)
async def activate_config(
    config_id: int,
    user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """激活指定配置 (先禁用其他 is_active=True 的记录)。"""
    row = await _get_or_404(db, config_id)
    # 禁用其他
    others = (await db.execute(
        select(GitTokenConfig).where(
            GitTokenConfig.is_active.is_(True),
            GitTokenConfig.is_deleted.is_(False),
            GitTokenConfig.id != config_id,
        )
    )).scalars().all()
    for other in others:
        other.is_active = False
        other.updated_at = local_now()
    row.is_active = True
    row.updated_at = local_now()
    await db.commit()
    await db.refresh(row)
    log.info("[git_token_config] 激活 id=%d name=%s", row.id, row.name)
    return _to_out(row)


@router.post("/test")
async def test_token_reachability(
    body: GitTokenTestBody,
    user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """测试 token 能否访问指定 Git 仓库 (不入库, 借鉴 model-config /test 模式).

    id 非 None 时从 DB 取真实 token (前端 token 被打码, 同 ModelConfig /test 模式).
    """
    if body.id is not None:
        row = await _get_or_404(db, body.id)
        token = row.token
    else:
        raise HTTPException(400, "请指定要测试的 Git Token 配置")

    is_reachable, error_msg = await asyncio.to_thread(
        check_repo_reachable, body.url, token,
    )
    if is_reachable:
        return {"success": True, "message": "Token 有效，仓库可达"}
    return {"success": False, "message": error_msg}
