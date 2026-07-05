"""模型注册中心 — 独立的大模型管理与调用封装.

配置来源与边界（重要）：
- 所有 LLM 调用统一走 registry，从 model_configs 表读取激活配置。
- DB 中无 active config → RuntimeError 引导管理员前往 Web UI 配置。
- 项目启动不依赖 LLM 配置：无 active config 时保持未就绪，
  管理员随时通过 Web UI 添加激活，即时生效无需重启。

设计原则：
- llm.py / embedding.py 的 client 构造已切到 registry.get_chat_client/get_embedding_client
- 内存缓存 + cfg-keyed 单槽 cache + 双检锁保证并发安全
- Chat 支持运行时热切换；Embedding 仅启动时初始化一次（切换需重嵌入）

切换策略：
- Chat：支持运行时热切换，新请求即时生效，无需重建任何索引。
- Embedding：仅在首次激活或服务启动时加载，不支持直接热切换。
  切换 Embedding 模型会导致 ChromaDB 旧向量与新查询向量不兼容，
  必须先完成知识库重嵌入（scripts/reembed_after_model_change.py），首期禁止直接切换。

使用示例：
    from app.engine.model_registry import registry

    # 就绪检查
    ready = registry.is_ready()
    # Chat client (由 llm.py::_get_openai_client 内部调用)
    client = registry.get_chat_client()
    # Embedding client (由 embedding.py::DashScopeEmbeddingFunction 内部调用)
    emb_client = registry.get_embedding_client()
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)


def _build_proxy_url(cfg: dict[str, Any]) -> str | None:
    """根据配置构造 HTTP 代理 URL, proxy_enabled=False 或缺 host 时返回 None."""
    if not cfg.get("proxy_enabled"):
        return None
    host = cfg.get("proxy_host")
    if not host:
        return None
    port = cfg.get("proxy_port") or 80
    user = cfg.get("proxy_username")
    pwd = cfg.get("proxy_password") or ""
    auth = f"{user}:{pwd}@" if user else ""
    return f"http://{auth}{host}:{port}"


class ModelRegistry:
    """大模型注册中心：持有激活配置 + 缓存客户端实例，支持热切换."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 激活配置（从 DB 加载）
        self._chat_config: dict[str, Any] | None = None
        self._embedding_config: dict[str, Any] | None = None
        # 运行时客户端缓存（配置未变时复用，切换时置 None）
        self._chat_client: Any | None = None       # openai.OpenAI
        self._embedding_client: Any | None = None  # openai.OpenAI
        # cfg-keyed cache key — 切换配置后 key 变化, 触发 client 重建
        self._chat_client_key: tuple | None = None
        self._embedding_client_key: tuple | None = None

    # ── 配置刷新（热切换入口）────────────────────────────────

    def refresh_chat(self, config: dict[str, Any] | None) -> None:
        """更新激活 Chat 配置，清空缓存以触发下次重建."""
        with self._lock:
            self._chat_config = config
            self._chat_client = None
            self._chat_client_key = None
        log.info("[model_registry] Chat 配置已更新: %s",
                 config.get("model_name") if config else "已清空")

    def refresh_embedding(self, config: dict[str, Any] | None) -> None:
        """加载 Embedding 配置（仅用于首次激活或启动恢复，不作为热切换入口）."""
        with self._lock:
            self._embedding_config = config
            self._embedding_client = None
            self._embedding_client_key = None
        log.info("[model_registry] Embedding 配置已更新: %s",
                 config.get("model_name") if config else "已清空")

    # ── 就绪状态查询 ─────────────────────────────────────────

    def is_ready(self) -> dict[str, bool]:
        """返回 Chat / Embedding 是否各有激活配置."""
        return {
            "chat_ready": self._chat_config is not None,
            "embedding_ready": self._embedding_config is not None,
            "ready": self._chat_config is not None and self._embedding_config is not None,
        }

    # ── 公开只读配置 ─────────────────────────────────────────

    @property
    def chat_config(self) -> dict[str, Any] | None:
        """当前激活的 Chat 配置 (含 protocol/model_name/base_url/api_key/temperature/max_tokens)."""
        return self._chat_config

    @property
    def embedding_config(self) -> dict[str, Any] | None:
        """当前激活的 Embedding 配置."""
        return self._embedding_config

    def get_chat_client(self, cfg: dict[str, Any] | None = None) -> Any:
        """返回当前激活 Chat 客户端 (OpenAI 或 Anthropic). 无激活配置时抛 RuntimeError.

        cfg 只读一次 (快照), 贯穿传递到 _get_chat_client — 保证此次请求
        model_name 和 client endpoint 一致。调用方可传入已验证的 cfg 快照
        (避免重复读 chat_config); 不传时内部从 self._chat_config 取。
        热切换瞬间进行中的请求可能用旧 config, 下一个请求自动取新 config (最终一致性).
        """
        if cfg is None:
            cfg = self._chat_config
        if cfg is None:
            raise RuntimeError(
                "无激活的 Chat 模型配置，请前往「模型管理」页面添加并激活 CHAT 类型配置。"
            )
        return self._get_chat_client(cfg)

    def get_embedding_client(self, cfg: dict[str, Any] | None = None) -> Any:
        """返回当前激活 Embedding 客户端 (OpenAI). 无激活配置时抛 RuntimeError."""
        if cfg is None:
            cfg = self._embedding_config
        if cfg is None:
            raise RuntimeError(
                "无激活的 Embedding 模型配置，请前往「模型管理」页面添加并激活 EMBEDDING 类型配置。"
            )
        return self._get_embedding_client(cfg)

    def _get_chat_client(self, cfg: dict[str, Any]) -> Any:
        """懒加载并缓存 Chat 客户端 (线程安全, 单槽 cfg-keyed cache).

        相同 cfg_key=(protocol,api_key,base_url) → 复用已缓存 client;
        cfg_key 变化 → 驱逐旧 client 重建。单槽够用——同一时刻只有一个激活
        CHAT 配置; 换 cfg 时 refresh_chat 已清缓存, 不会有两个 cfg 并发争槽。
        """
        cfg_key = (cfg.get("protocol", "openai"),
                    cfg.get("api_key"), cfg.get("base_url"))
        if self._chat_client is not None and self._chat_client_key == cfg_key:
            return self._chat_client

        with self._lock:
            if self._chat_client is not None and self._chat_client_key == cfg_key:
                return self._chat_client
            from app.engine.llm_client_factory import build_anthropic_client, build_openai_client
            proxy_url = _build_proxy_url(cfg)
            proto = cfg.get("protocol", "openai")
            if proto == "anthropic":
                self._chat_client = build_anthropic_client(
                    cfg["api_key"], cfg["base_url"],
                    timeout=settings.llm_client_timeout_secs, proxy_url=proxy_url)
            else:
                base_url = cfg["base_url"]
                path = cfg.get("completions_path") or ""
                if path and path != "/v1/chat/completions":
                    base_url = base_url.rstrip("/") + path
                self._chat_client = build_openai_client(
                    cfg["api_key"], base_url,
                    timeout=settings.llm_client_timeout_secs, proxy_url=proxy_url)
            self._chat_client_key = cfg_key
        return self._chat_client


    def _get_embedding_client(self, cfg: dict[str, Any]) -> Any:
        """懒加载并缓存 Embedding OpenAI 客户端 (线程安全, 单槽 cfg-keyed cache).

        相同 cfg_key=(api_key,base_url) → 复用已缓存 client;
        cfg_key 变化 → 驱逐旧 client 重建。单槽够用——Embedding 不支持热切换,
        启动后 cfg_key 不变。
        """
        cfg_key = (cfg.get("api_key"), cfg.get("base_url"))
        if self._embedding_client is not None and self._embedding_client_key == cfg_key:
            return self._embedding_client

        with self._lock:
            if self._embedding_client is not None and self._embedding_client_key == cfg_key:
                return self._embedding_client
            from app.engine.llm_client_factory import build_openai_client
            base_url = cfg["base_url"]
            path = cfg.get("embeddings_path") or ""
            if path and path != "/v1/embeddings":
                base_url = base_url.rstrip("/") + path
            proxy_url = _build_proxy_url(cfg)
            self._embedding_client = build_openai_client(
                cfg["api_key"], base_url,
                timeout=settings.llm_client_timeout_secs, proxy_url=proxy_url)
            self._embedding_client_key = cfg_key
        return self._embedding_client

    # ── 启动时从 DB 恢复激活配置 ──────────────────────────────

    async def load_from_db(self) -> None:
        """应用启动时调用，从 DB 恢复激活配置到内存（重启后热切换状态不丢失）.

        DB 中无 active config 时不报错，registry 保持未就绪状态。
        管理员通过 Web UI 添加并激活首个模型配置后，即时生效无需重启。
        """
        try:
            from sqlalchemy import select

            from app.db.metadata import async_session
            from app.models.model_config import ModelConfig

            async with async_session() as db:
                rows = (await db.execute(
                    select(ModelConfig).where(
                        ModelConfig.is_active.is_(True),
                        ModelConfig.is_deleted.is_(False),
                    )
                )).scalars().all()

            for row in rows:
                cfg = self._row_to_dict(row)
                if row.model_type == "CHAT":
                    self.refresh_chat(cfg)
                elif row.model_type == "EMBEDDING":
                    self.refresh_embedding(cfg)

            ready = self.is_ready()
            log.info(
                "[model_registry] 启动加载完成 chat_ready=%s embedding_ready=%s active_count=%d",
                ready["chat_ready"], ready["embedding_ready"], len(rows),
            )
        except Exception as exc:
            log.warning("[model_registry] 启动加载失败（不影响原有 LLM 链路）: %s", exc)

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """ModelConfig ORM 行 → 配置 dict（api_key 由 EncryptedString 透明解密）."""
        return {
            "id": row.id,
            "provider": row.provider,
            "base_url": row.base_url,
            "api_key": row.api_key,        # EncryptedString TypeDecorator 已透明解密
            "model_name": row.model_name,
            "model_type": row.model_type,
            "protocol": row.protocol,
            "temperature": float(row.temperature) if row.temperature is not None else 0.1,
            "max_tokens": row.max_tokens or settings.llm_max_tokens_default,
            "completions_path": row.completions_path,
            "embeddings_path": row.embeddings_path,
            "proxy_enabled": row.proxy_enabled,
            "proxy_host": row.proxy_host,
            "proxy_port": row.proxy_port,
            "proxy_username": row.proxy_username,
            "proxy_password": row.proxy_password,
        }



# ── 进程级单例 ────────────────────────────────────────────────
# 所有调用方 import 此对象即可，无需关心内部状态
registry = ModelRegistry()
