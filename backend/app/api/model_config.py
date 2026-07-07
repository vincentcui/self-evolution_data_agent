"""模型配置管理 API — CRUD + 激活 + 连接测试 + 就绪检查.

端点列表：
  GET    /api/model-config/list          列表（API Key 脱敏）
  POST   /api/model-config/add           新增
  PUT    /api/model-config/update        更新（****跳过 Key 更新）
  DELETE /api/model-config/{id}          逻辑删除
  POST   /api/model-config/activate/{id} 激活并热切换
  POST   /api/model-config/test          测试连接（不入库）
  GET    /api/model-config/check-ready   就绪检查
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_admin_or_above
from app.config import settings
from app.db.metadata import get_db
from app.models.base import local_now
from app.models.model_config import DEFAULT_MAX_HISTORY_TURNS, ModelConfig
from app.models.user import User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/model-config", tags=["model-config"])

_MASK = "****"


# ══════════════════════════════════════════════════════════════
#  Pydantic schemas
# ══════════════════════════════════════════════════════════════

class ModelConfigIn(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    base_url: str = Field(..., min_length=1, max_length=512)
    api_key: str = Field(..., min_length=1)
    model_name: str = Field(..., min_length=1, max_length=128)
    model_type: str = Field("CHAT", pattern=r"^(CHAT|EMBEDDING)$")
    protocol: str = Field("openai", pattern=r"^(openai|anthropic)$")
    temperature: float | None = 0.0
    max_tokens: int | None = Field(default_factory=lambda: settings.llm_max_tokens_default)
    max_history_turns: int = Field(default=DEFAULT_MAX_HISTORY_TURNS, ge=0)
    completions_path: str | None = None
    embeddings_path: str | None = None
    proxy_enabled: bool = False
    proxy_host: str | None = None
    proxy_port: int | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    namespace_id: int | None = None


class ModelConfigUpdate(ModelConfigIn):
    """更新时包含 id，model_type 不可修改（忽略传入值）."""
    id: int


class ModelConfigTestBody(ModelConfigIn):
    """测试连接请求体 — 编辑场景额外携带 id，用于 key 被打码时从 DB 取真实值."""
    id: int | None = None


class ModelConfigOut(BaseModel):
    id: int
    provider: str
    base_url: str
    api_key: str          # 返回时已脱敏
    model_name: str
    model_type: str
    protocol: str
    temperature: float | None
    max_tokens: int | None
    max_history_turns: int
    is_active: bool
    namespace_id: int | None = None
    namespace_name: str | None = None
    completions_path: str | None
    embeddings_path: str | None
    proxy_enabled: bool
    proxy_host: str | None
    proxy_port: int | None
    proxy_username: str | None
    created_at: datetime
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class CheckReadyOut(BaseModel):
    chat_model_ready: bool
    embedding_model_ready: bool
    ready: bool


# ══════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════

def _mask_key(key: str) -> str:
    """部分脱敏：保留前 4 位和后 4 位，中间替换为 ****."""
    if not key or len(key) <= 8:
        return _MASK
    return key[:4] + _MASK + key[-4:]


def protocol_for_provider(provider: str, requested: str = "openai") -> str:
    """根据 provider 名称推断协议标识.

    - ``anthropic`` → 强制 ``"anthropic"``
    - ``custom``    → 信任前端传入的 ``requested``（允许接 Claude 兼容端点）
    - 其他          → 强制 ``"openai"``
    """
    p = provider.strip().lower()
    if p == "anthropic":
        return "anthropic"
    if p == "custom":
        return requested if requested in ("openai", "anthropic") else "openai"
    return "openai"


def _safe_config_dict(row: ModelConfig) -> dict:
    """把 ModelConfig 转成可安全写入审计的 dict（脱敏敏感字段）."""
    return {
        "provider": row.provider,
        "protocol": row.protocol,
        "base_url": row.base_url,
        "model_name": row.model_name,
        "model_type": row.model_type,
        "namespace_id": row.namespace_id,
        "temperature": float(row.temperature) if row.temperature is not None else None,
        "max_tokens": row.max_tokens,
        "max_history_turns": row.max_history_turns,
        "completions_path": row.completions_path,
        "embeddings_path": row.embeddings_path,
        "proxy_enabled": row.proxy_enabled,
        "proxy_host": row.proxy_host,
        "proxy_port": row.proxy_port,
        "proxy_username": row.proxy_username,
        "api_key_masked": _mask_key(row.api_key),
        "proxy_password_set": row.proxy_password is not None,
    }


async def _write_audit(
    db: AsyncSession,
    action: str,
    actor: User,
    row: ModelConfig | None = None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
) -> None:
    """写入一条审计记录（SAVEPOINT 隔离，审计失败不阻断业务事务）."""
    try:
        from app.models.model_config_audit_log import ModelConfigAuditLog
        entry = ModelConfigAuditLog(
            config_id=row.id if row else None,
            actor_id=actor.id if actor else None,
            action=action,
            model_type=row.model_type if row else None,
            provider=row.provider if row else None,
            protocol=row.protocol if row else None,
            model_name=row.model_name if row else None,
            before_json=json.dumps(before, ensure_ascii=False) if before else None,
            after_json=json.dumps(after, ensure_ascii=False) if after else None,
            reason=reason,
        )
        async with db.begin_nested():   # SAVEPOINT — 审计失败只回滚自身
            db.add(entry)
    except Exception as exc:           # 审计不应阻断业务主链路
        log.warning("[model_config] 审计写入失败（非致命）action=%s: %s", action, exc)


def _to_out(row: ModelConfig) -> ModelConfigOut:
    return ModelConfigOut(
        id=row.id,
        provider=row.provider,
        base_url=row.base_url,
        api_key=_mask_key(row.api_key),
        model_name=row.model_name,
        model_type=row.model_type,
        protocol=row.protocol,
        temperature=float(row.temperature) if row.temperature is not None else 0.0,
        max_tokens=row.max_tokens,
        max_history_turns=row.max_history_turns,
        is_active=row.is_active,
        namespace_id=row.namespace_id,
        namespace_name=None,  # 可后续 join 填充，或前端用 namespace_id 关联
        completions_path=row.completions_path,
        embeddings_path=row.embeddings_path,
        proxy_enabled=row.proxy_enabled,
        proxy_host=row.proxy_host,
        proxy_port=row.proxy_port,
        proxy_username=row.proxy_username,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _get_or_404(db: AsyncSession, config_id: int) -> ModelConfig:
    row = await db.get(ModelConfig, config_id)
    if not row or row.is_deleted:
        raise HTTPException(404, "模型配置不存在")
    return row


# ══════════════════════════════════════════════════════════════
#  API 端点
# ══════════════════════════════════════════════════════════════

@router.get("/list", response_model=list[ModelConfigOut])
async def list_configs(
    namespace_id: int | None = None,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取未删除的模型配置列表（API Key 脱敏），可按 namespace 过滤."""
    query = select(ModelConfig).where(ModelConfig.is_deleted.is_(False))
    if namespace_id is not None:
        query = query.where(ModelConfig.namespace_id == namespace_id)
    query = query.order_by(ModelConfig.model_type, ModelConfig.id)
    rows = (await db.execute(query)).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("/add", response_model=ModelConfigOut, status_code=201)
async def add_config(
    body: ModelConfigIn,
    _user: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """新增模型配置（不自动激活，需手动激活）."""
    proto = protocol_for_provider(body.provider, body.protocol)
    if proto == "anthropic" and body.model_type == "EMBEDDING":
        raise HTTPException(400, "Anthropic 协议不支持 EMBEDDING 类型")

    # EMBEDDING 强制 namespace_id = None (代码层约束)
    ns_id = body.namespace_id if body.model_type == "CHAT" else None

    # namespace 存在性校验 (避免 FK IntegrityError → 500)
    if ns_id is not None:
        from app.models.namespace import Namespace
        ns_exists = await db.scalar(
            select(Namespace.id).where(Namespace.id == ns_id)
        )
        if ns_exists is None:
            raise HTTPException(400, f"命名空间不存在: {ns_id}")

    row = ModelConfig(
        provider=body.provider.strip(),
        base_url=body.base_url.strip(),
        api_key=body.api_key.strip(),
        model_name=body.model_name.strip(),
        model_type=body.model_type,
        protocol=proto,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        max_history_turns=body.max_history_turns,
        completions_path=body.completions_path,
        embeddings_path=body.embeddings_path,
        proxy_enabled=body.proxy_enabled,
        proxy_host=body.proxy_host,
        proxy_port=body.proxy_port,
        proxy_username=body.proxy_username,
        proxy_password=body.proxy_password,
        is_active=False,
        is_deleted=False,
        namespace_id=ns_id,
    )
    db.add(row)
    await db.flush()  # 获取 row.id（自增主键）
    await _write_audit(db, "create", _user, row=row, after=_safe_config_dict(row))
    await db.commit()
    await db.refresh(row)
    log.info("[model_config] 新增 id=%d provider=%s type=%s protocol=%s",
             row.id, row.provider, row.model_type, row.protocol)
    return _to_out(row)


@router.put("/update", response_model=ModelConfigOut)
async def update_config(
    body: ModelConfigUpdate,
    _user: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """更新模型配置。API Key 若传 **** 则跳过更新（保留原值）."""
    row = await _get_or_404(db, body.id)

    # 已激活的 Embedding 配置不允许直接修改（会导致已有向量与新查询不兼容）
    if row.model_type == "EMBEDDING" and row.is_active:
        log.warning("[model_config] blocked update: active Embedding id=%d", body.id)
        raise HTTPException(
            409,
            "已激活的 Embedding 配置不允许直接修改，修改需要重建知识库索引",
        )

    proto = protocol_for_provider(body.provider, body.protocol)
    if proto == "anthropic" and row.model_type == "EMBEDDING":
        raise HTTPException(400, "Anthropic 协议不支持 EMBEDDING 类型")

    # 在修改前拍快照
    before_snapshot = _safe_config_dict(row)

    row.provider = body.provider.strip()
    row.protocol = proto
    row.base_url = body.base_url.strip()
    if _MASK not in body.api_key.strip():
        row.api_key = body.api_key.strip()
    row.model_name = body.model_name.strip()
    # model_type 不允许修改
    # namespace_id 不允许修改 (创建时绑定)
    if body.namespace_id is not None and body.namespace_id != row.namespace_id:
        raise HTTPException(400, "namespace_id 不允许修改，请删除后重新创建")
    row.temperature = body.temperature
    row.max_tokens = body.max_tokens
    row.max_history_turns = body.max_history_turns
    row.completions_path = body.completions_path
    row.embeddings_path = body.embeddings_path
    row.proxy_enabled = body.proxy_enabled
    row.proxy_host = body.proxy_host
    row.proxy_port = body.proxy_port
    row.proxy_username = body.proxy_username
    if body.proxy_password and body.proxy_password != _MASK:
        row.proxy_password = body.proxy_password
    row.updated_at = local_now()
    await _write_audit(
        db,
        "update",
        _user,
        row=row,
        before=before_snapshot,
        after=_safe_config_dict(row),
    )
    await db.commit()
    await db.refresh(row)

    # 若当前已激活，热刷新注册中心
    if row.is_active:
        await _do_refresh(row)

    return _to_out(row)


@router.delete("/{config_id}", status_code=204)
async def delete_config(
    config_id: int,
    _user: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """逻辑删除模型配置。Chat 可直接删除；active Embedding 禁止删除。"""
    row = await _get_or_404(db, config_id)

    # 已激活的 Embedding 配置不允许直接删除（ChromaDB 里仍有旧向量）
    if row.model_type == "EMBEDDING" and row.is_active:
        log.warning("[model_config] blocked delete: active Embedding id=%d", config_id)
        raise HTTPException(
            409,
            "已激活的 Embedding 配置不允许直接删除，删除需要重建知识库索引",
        )

    was_active = row.is_active
    model_type = row.model_type
    ns_id = row.namespace_id
    before_snapshot = _safe_config_dict(row)
    row.is_deleted = True
    row.is_active = False
    row.updated_at = local_now()
    await _write_audit(db, "delete", _user, row=row, before=before_snapshot)
    await db.commit()
    if was_active:
        _clear_registry(model_type, namespace_id=ns_id)


@router.post("/activate/{config_id}", response_model=ModelConfigOut)
async def activate_config(
    config_id: int,
    _user: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """激活指定配置。Chat 支持热切换；Embedding 仅允许首次激活（无 active 时）。"""
    row = await _get_or_404(db, config_id)

    # Embedding：已有其他 active 配置时禁止切换
    if row.model_type == "EMBEDDING":
        other_active = await db.scalar(
            select(ModelConfig).where(
                ModelConfig.model_type == "EMBEDDING",
                ModelConfig.is_active.is_(True),
                ModelConfig.is_deleted.is_(False),
                ModelConfig.id != config_id,
            )
        )
        if other_active is not None:
            log.warning("[model_config] blocked activate: Embedding hot-switch id=%d", config_id)
            raise HTTPException(
                409,
                "Embedding 模型切换需要重建知识库索引，首期不支持直接热切换",
            )

    # 先禁用同模型类型 + 同 namespace_id 组内其他配置
    others = (await db.execute(
        select(ModelConfig).where(
            ModelConfig.model_type == row.model_type,
            ModelConfig.namespace_id == row.namespace_id,
            ModelConfig.is_active.is_(True),
            ModelConfig.id != config_id,
            ModelConfig.is_deleted.is_(False),
        )
    )).scalars().all()
    for other in others:
        other.is_active = False
        other.updated_at = local_now()
        await _write_audit(db, "deactivate", _user, row=other)

    # flush 先把禁用语句发到 DB，确保唯一索引校验时旧记录已变为 False
    await db.flush()

    row.is_active = True
    row.updated_at = local_now()
    await _write_audit(db, "activate", _user, row=row, after=_safe_config_dict(row))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "激活失败，请刷新后重试") from exc
    await db.refresh(row)

    # 热切换
    await _do_refresh(row)
    log.info("[model_config] 激活 id=%d provider=%s model=%s type=%s",
             row.id, row.provider, row.model_name, row.model_type)
    return _to_out(row)


@router.post("/test")
async def test_connection(
    body: ModelConfigTestBody,
    _user: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """测试连接（不入库，创建临时实例发送测试请求）.

    编辑场景：前端展示脱敏 key（如 76da****SxQG），测试时 body.id 不为 None，
    后端检测到 api_key 含 **** 即从 DB 取真实 key，避免用打码字符串发请求失败。
    """
    api_key = body.api_key.strip()

    # key 被打码 + 有 id → 从 DB 取真实 key
    if _MASK in api_key and body.id is not None:
        row = await db.get(ModelConfig, body.id)
        if row and not row.is_deleted:
            api_key = row.api_key
        else:
            return {"success": False, "message": "配置不存在，无法获取 API Key"}

    if not api_key or _MASK in api_key:
        return {"success": False, "message": "API Key 无效，请重新输入完整密钥"}

    proto = protocol_for_provider(body.provider, body.protocol)
    cfg: dict[str, Any] = {
        "base_url": body.base_url.strip(),
        "api_key": api_key,
        "model_name": body.model_name.strip(),
        "model_type": body.model_type,
        "protocol": proto,
        "temperature": body.temperature or 0.0,
        "max_tokens": body.max_tokens or settings.llm_max_tokens_default,
        "completions_path": body.completions_path,
        "embeddings_path": body.embeddings_path,
        "proxy_url": _build_proxy_url_from_body(body),
    }
    try:
        if body.model_type == "CHAT":
            if proto == "anthropic":
                _test_anthropic_chat(cfg)
            else:
                _test_openai_chat(cfg)
        else:
            _test_openai_embedding(cfg)
        return {"success": True, "message": "连接成功"}
    except Exception as exc:
        msg = _friendly_error(str(exc))
        return {"success": False, "message": msg}


@router.get("/check-ready", response_model=CheckReadyOut)
async def check_ready(
    namespace_id: int | None = None,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """检查 Chat / Embedding 是否就绪。namespace_id 非 None 时检查该 namespace
    的 Chat 配置 (含全局兜底)；Embedding 始终检查全局。

    只检查 DB, 不读取 env / settings；env 有配置但 DB 无 active config 时仍返回 false。
    """
    # 检查全局 EMBEDDING
    emb_ok = await db.scalar(
        select(ModelConfig.id).where(
            ModelConfig.model_type == "EMBEDDING",
            ModelConfig.is_active.is_(True),
            ModelConfig.is_deleted.is_(False),
        )
    )
    emb_ok = emb_ok is not None

    # 检查 CHAT: namespace 级别 + 全局兜底
    chat_ok = False
    if namespace_id is not None:
        ns_chat = await db.scalar(
            select(ModelConfig.id).where(
                ModelConfig.model_type == "CHAT",
                ModelConfig.namespace_id == namespace_id,
                ModelConfig.is_active.is_(True),
                ModelConfig.is_deleted.is_(False),
            )
        )
        chat_ok = ns_chat is not None

    if not chat_ok:
        # 检查全局兜底
        global_chat = await db.scalar(
            select(ModelConfig.id).where(
                ModelConfig.model_type == "CHAT",
                ModelConfig.namespace_id.is_(None),
                ModelConfig.is_active.is_(True),
                ModelConfig.is_deleted.is_(False),
            )
        )
        chat_ok = global_chat is not None

    return CheckReadyOut(
        chat_model_ready=chat_ok,
        embedding_model_ready=emb_ok,
        ready=chat_ok and emb_ok,
    )


# ══════════════════════════════════════════════════════════════
#  内部工具
# ══════════════════════════════════════════════════════════════

def _build_proxy_url_from_body(body: ModelConfigTestBody) -> str | None:
    """从请求体构造代理 URL — 委托 registry._build_proxy_url, 单一真相源."""
    from app.engine.model_registry import _build_proxy_url

    return _build_proxy_url({
        "proxy_enabled": body.proxy_enabled,
        "proxy_host": body.proxy_host,
        "proxy_port": body.proxy_port,
        "proxy_username": body.proxy_username,
        "proxy_password": body.proxy_password,
    })


def _friendly_error(raw: str) -> str:
    """将原始异常信息映射为友好提示."""
    raw_lower = raw.lower()
    if "401" in raw or "unauthorized" in raw_lower or "invalid api key" in raw_lower:
        return "鉴权失败，请检查 API Key"
    if "404" in raw or "not found" in raw_lower:
        return "接口未找到，请检查 BaseURL 或路径"
    if "429" in raw or "rate limit" in raw_lower or "quota" in raw_lower:
        return "请求过多或余额不足"
    if "timeout" in raw_lower or "timed out" in raw_lower:
        return "连接超时，请检查 BaseURL 或网络"
    return f"连接失败: {raw[:200]}"


def _test_openai_chat(cfg: dict[str, Any]) -> None:
    """发送 Hello 测试 OpenAI 兼容 Chat 连接, 失败即抛异常."""
    from app.engine.llm import build_openai_client
    base_url = cfg["base_url"]
    path = cfg.get("completions_path") or "/v1/chat/completions"
    if path != "/v1/chat/completions":
        base_url = base_url.rstrip("/") + path
    client = build_openai_client(
        cfg["api_key"], base_url,
        timeout=settings.llm_connect_test_timeout_secs,
        proxy_url=cfg.get("proxy_url"),
    )
    client.chat.completions.create(
        model=cfg["model_name"],
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=8,
    )


def _test_anthropic_chat(cfg: dict[str, Any]) -> None:
    """发送 Hello 测试 Anthropic Chat 连接, 失败即抛异常."""
    from app.engine.llm import build_anthropic_client
    client = build_anthropic_client(
        cfg["api_key"], cfg["base_url"],
        timeout=settings.llm_connect_test_timeout_secs,
        proxy_url=cfg.get("proxy_url"),
    )
    client.messages.create(
        model=cfg["model_name"],
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=8,
    )


def _test_openai_embedding(cfg: dict[str, Any]) -> None:
    """发送 Test 测试 Embedding 连接, 失败即抛异常."""
    from app.engine.llm import build_openai_client
    client = build_openai_client(
        cfg["api_key"], cfg["base_url"],
        timeout=settings.llm_connect_test_timeout_secs,
        proxy_url=cfg.get("proxy_url"),
    )
    client.embeddings.create(model=cfg["model_name"], input="Test")


async def _do_refresh(row: ModelConfig) -> None:
    """激活/更新后热刷新注册中心."""
    from app.engine.model_registry import registry
    cfg = registry._row_to_dict(row)
    if row.model_type == "CHAT":
        registry.refresh_chat(cfg, namespace_id=row.namespace_id)
    else:
        registry.refresh_embedding(cfg)


def _clear_registry(model_type: str, namespace_id: int | None = None) -> None:
    """删除激活配置时清空注册中心对应槽位."""
    from app.engine.model_registry import registry
    if model_type == "CHAT":
        registry.refresh_chat(None, namespace_id=namespace_id)
    else:
        registry.refresh_embedding(None)
