"""
统一 embedding 入口 — DashScope text-embedding-v4 (OpenAI 兼容协议)

设计原则:
- 进程级单例, 所有 ChromaDB collection 共用同一 EmbeddingFunction
- 凭证从 model_configs 表 + ModelRegistry 读取 (Web UI 模型管理页面配置)
- 遵循 ChromaDB EmbeddingFunction Protocol,
  直接传入 client.get_or_create_collection(embedding_function=...)

DashScope /embeddings 批量限制 (text-embedding-v4): 单次最多 10 条输入.
超过 10 条按 10 为批次切片串行调用, 结果按原顺序拼接.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings, Space
from langfuse import observe

from app.tracing import get_client as _lf_client

if TYPE_CHECKING:
    from openai import OpenAI

log = logging.getLogger("embedding")

# DashScope text-embedding-v4 单次最多 10 条输入
_DASHSCOPE_BATCH_SIZE = 10


class DashScopeEmbeddingFunction(EmbeddingFunction[Documents]):
    """
    ChromaDB EmbeddingFunction 实现 — 调 DashScope OpenAI 兼容 /embeddings endpoint.

    线程安全: openai.OpenAI 内部用 httpx, 本对象持有 client 引用; ChromaDB 从多线程调用
    __call__ 时共享底层连接池, 无状态冲突.
    """

    _client: "OpenAI | None" = None
    _model: str = ""

    def __init__(self) -> None:
        from app.engine.model_registry import registry

        cfg = registry.embedding_config
        if cfg is None:
            raise RuntimeError(
                "无激活的 Embedding 模型配置，请前往「模型管理」页面添加并激活 EMBEDDING 类型配置。"
            )
        self._client = registry.get_embedding_client()
        self._model = cfg["model_name"]
        self._base_url = cfg.get("base_url")
        log.info("embedding provider 就绪 model=%s base_url=%s",
                 self._model, self._base_url)

    @observe(as_type="embedding", name="embedding", capture_input=False, capture_output=False)
    def __call__(self, input: Documents) -> Embeddings:
        texts = [t for t in input]
        if not texts:
            return []

        vectors: Embeddings = []
        total_tokens = 0
        for start in range(0, len(texts), _DASHSCOPE_BATCH_SIZE):
            batch = texts[start:start + _DASHSCOPE_BATCH_SIZE]
            assert self._client is not None
            resp = self._client.embeddings.create(
                model=self._model,
                input=batch,
                encoding_format="float",
            )
            # OpenAI SDK 保证 data 按输入顺序返回, 但 index 字段仍作为兜底
            ordered = sorted(resp.data, key=lambda d: d.index)
            vectors.extend(np.asarray(d.embedding, dtype=np.float32) for d in ordered)
            usage = getattr(resp, "usage", None)
            if usage is not None:
                total_tokens += getattr(usage, "total_tokens", 0) or 0

        # ── Langfuse 元数据回填 ──
        lf = _lf_client()
        if lf is not None:
            try:
                lf.update_current_generation(
                    model=self._model,
                    input={"batch_count": len(texts)},
                    output={"dim": int(vectors[0].shape[0]) if vectors else 0},
                    usage_details=(
                        {"input": total_tokens, "total": total_tokens}
                        if total_tokens else None
                    ),
                )
            except Exception:
                pass

        return vectors

    @staticmethod
    def name() -> str:
        return "dashscope-embedding-v4"

    # ── ChromaDB 0.5+ 要求 build_from_config / get_config 用于持久化 ──

    @staticmethod
    def build_from_config(config: dict) -> "DashScopeEmbeddingFunction":
        _ = config  # 当前无参可解析 — 所有配置从 registry 读
        return DashScopeEmbeddingFunction()

    def get_config(self) -> dict:
        return {"model": self._model, "base_url": self._base_url}

    def default_space(self) -> Space:
        return "cosine"


# ════════════════════════════════════════════
#  进程级单例 — 所有消费者共用同一 EmbeddingFunction
# ════════════════════════════════════════════

_instance: DashScopeEmbeddingFunction | None = None


def get_embedding_function() -> DashScopeEmbeddingFunction:
    """
    返回进程级单例 EmbeddingFunction.

    调用点:
    - registry.get_knowledge_collection (ns_{slug}_knowledge)
    - knowledge_retriever.delete_knowledge_entry / retrieve_layer3 的 get_collection
    """
    global _instance
    if _instance is None:
        _instance = DashScopeEmbeddingFunction()
    return _instance
