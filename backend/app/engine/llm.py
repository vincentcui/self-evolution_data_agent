"""
LLM 统一抽象层 — 所有 LLM 调用的唯一入口
消灭散落各处的 OpenAI() 实例化, 一个函数路由一切

设计哲学:
    不用类继承, 不搞策略模式 — 一个函数 + 一个 if 就够了
    过度抽象是简单问题的复杂化, 两个 provider 不值得一个工厂

追踪:
    Langfuse 启用时, chat_completion / chat_completion_checked 自动记录
    generation 观测 (model/input/output/usage). 未启用时 @observe 变成 no-op,
    调用方无需感知. 所有下游 LLM 调用都必然经过这两个入口, 覆盖全链路.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import anthropic
import httpx
import openai
from langfuse import observe
from openai import OpenAI

from app.config import settings
from app.engine.json_parser import parse_llm_json
from app.tracing import get_client as _lf_client

logger = logging.getLogger(__name__)

# ── LLM 超时异常族 (单一真相源) ──────────────────────────────────────────
# provider SDK 各自把底层 httpx.TimeoutException 吞掉后 re-raise 自有 APITimeoutError
# (anthropic._base_client / openai 皆如此), 二者互不为子类, 必须逐一显式列.
# 主循环 (agent_loop) 引用此 tuple 做超时降级 — 新增 provider 只在此处补一行,
# 不在调用方硬编码 SDK 异常类 (防 provider 耦合泄漏 + 漏列一个即整链炸).
LLM_TIMEOUT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    httpx.TimeoutException,
    openai.APITimeoutError,
    anthropic.APITimeoutError,
)


class EmptyLLMResponseError(RuntimeError):
    """LLM returned empty/null content — retryable transient error."""
    pass

# ── thinking 模式控制 ──────────────────────────────────────────────
# 仅内部使用, 外部调用方通过 thinking 参数控制

def _build_extra_body(thinking: bool, existing: dict | None) -> dict | None:
    """OpenAI 路径：thinking 参数 → extra_body 注入.

    thinking=False → {"thinking": {"type": "disabled"}} 合并 existing
    thinking=True  → 移除已有 thinking 键, 返回 existing 原样
    """
    result = dict(existing) if existing else {}
    result.pop("thinking", None)
    if not thinking:
        result["thinking"] = {"type": "disabled"}
    return result or None


def _claude_thinking_cfg(thinking: bool) -> dict | None:
    """Claude 路径：thinking=True → enabled + budget_tokens."""
    if thinking:
        return {
            "type": "enabled",
            "budget_tokens": settings.llm_claude_thinking_budget_tokens,
        }
    return None

# ── Bedrock proxy tool_use_id 合规校验 ──────────────────────────────────────
# Bedrock proxy 对 tool_use_id 强制 ^[a-zA-Z0-9_-]+$ 校验;
# Anthropic 官方 toolu_xxx 本身合规, 某些 proxy 路径会 mangle 前缀致 422.
_TOOL_ID_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")


def _sanitize_tool_use_id(raw_id: str) -> str:
    """清洗 tool_use_id, 确保符合 Bedrock proxy ^[a-zA-Z0-9_-]+$ 约束."""
    if not raw_id:
        return "tool_id_unknown"
    return _TOOL_ID_UNSAFE.sub("_", raw_id)


# ── 客户端构造 ──
# cfg 参数: 由公开入口读一次后传入全链, 禁止内部独立读 registry.chat_config.
# 不传 cfg 时从 registry 取 (向后兼容, 仅旧调用路径用).


def _get_openai_client(
    cfg: dict[str, Any] | None = None, namespace_id: int | None = None,
) -> OpenAI:
    """返回 OpenAI 兼容客户端 (从 registry 激活配置构造, 热切换自动重建)."""
    # import 必须无条件: 否则 cfg 非 None 时跳过分支, registry 未绑定 → UnboundLocalError
    from app.engine.model_registry import registry
    if cfg is None:
        cfg = registry.resolve_chat_config(namespace_id)
    # 未显式传 namespace_id 时, 从 cfg 自身携带的 namespace_id 推导,
    # 保证 client 缓存落到正确的 namespace 槽 (分级配置隔离 + 删除时精准清槽).
    if namespace_id is None and cfg is not None:
        namespace_id = cfg.get("namespace_id")
    if cfg is None or cfg.get("protocol", "openai") != "openai":
        raise RuntimeError(
            "无激活的 openai Chat 配置，请前往「模型管理」页面添加并激活 CHAT 类型配置。"
        )
    client = registry.get_chat_client(cfg, namespace_id=namespace_id)
    return client  # type: ignore[return-value]


def _get_claude_client(
    cfg: dict[str, Any] | None = None, namespace_id: int | None = None,
) -> anthropic.Anthropic:
    """返回 Anthropic 客户端 (从 registry 激活配置构造, 热切换自动重建)."""
    # import 必须无条件: 否则 cfg 非 None 时跳过分支, registry 未绑定 → UnboundLocalError
    from app.engine.model_registry import registry
    if cfg is None:
        cfg = registry.resolve_chat_config(namespace_id)
    # 未显式传 namespace_id 时, 从 cfg 自身携带的 namespace_id 推导,
    # 保证 client 缓存落到正确的 namespace 槽 (分级配置隔离 + 删除时精准清槽).
    if namespace_id is None and cfg is not None:
        namespace_id = cfg.get("namespace_id")
    if cfg is None or cfg.get("protocol") != "anthropic":
        raise RuntimeError(
            "无激活的 anthropic Chat 配置，请前往「模型管理」页面添加并激活 CHAT 类型配置。"
        )
    client = registry.get_chat_client(cfg, namespace_id=namespace_id)
    return client  # type: ignore[return-value]


# ── 客户端工厂 re-export (真实定义在 llm_client_factory.py, 消除循环依赖) ──

from app.engine.llm_client_factory import build_anthropic_client, build_openai_client  # noqa: E402


def build_chat_client(
    api_key: str, base_url: str, protocol: str = "openai", *,
    timeout: float, proxy_url: str | None = None,
) -> "OpenAI | anthropic.Anthropic":
    """临时 LLM 客户端工厂 (不缓存, 不读 settings, 用于连接测试等一次性场景).

    分派到 build_openai_client / build_anthropic_client; 返回 union, 调用方需
    按 protocol 自行 narrow, 或直接调具体工厂拿确定类型。
    """
    if protocol == "anthropic":
        return build_anthropic_client(api_key, base_url, timeout=timeout, proxy_url=proxy_url)
    return build_openai_client(api_key, base_url, timeout=timeout, proxy_url=proxy_url)


# ════════════════════════════════════════════
#  Langfuse generation 元数据回填
#  — 在 @observe 上下文内安全调用, 无 trace 时 get_client() 返回 None
# ════════════════════════════════════════════

def _last_input_block(messages: list[dict]) -> list[dict]:
    """仅取本轮 generation 的"新增输入" — 首条 system + 最后一段非 assistant 消息.

    背景: agent_loop 多轮迭代每轮把累积 messages 全量塞进 generation span 的 input,
    第 12 轮已 = system + 12×(assistant + N tool_result), payload 易破 MB, 触发
    OTLP 5s 超时. langfuse trace 视图能从父 chain 串起每轮 generation, 单轮 generation
    只需自描述本轮输入即可, 历史上下文是冗余.

    规则:
      - 首条若为 system, 保留 (定位 agent persona)
      - 从尾向前扫, 收集"最后一个 assistant 之后的所有消息" (即本轮真正的新增 input)
      - 若整列无 assistant, 退化为最后一条 user 消息
    """
    if not messages:
        return []
    head: list[dict] = []
    if messages[0].get("role") == "system":
        head.append(messages[0])

    last_asst = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            last_asst = i
            break
    tail = messages[last_asst + 1:] if last_asst >= 0 else messages[-1:]
    return head + tail


def _record_generation(
    *, model: str, messages: list[dict], output: str | dict,
    input_tokens: int | None = None, output_tokens: int | None = None,
    tools: list[dict] | None = None,
) -> None:
    lf = _lf_client()
    if lf is None:
        return
    try:
        usage: dict = {}
        if input_tokens is not None:
            usage["input"] = input_tokens
        if output_tokens is not None:
            usage["output"] = output_tokens
        if input_tokens is not None and output_tokens is not None:
            usage["total"] = input_tokens + output_tokens
        metadata = {"tools": tools} if tools else None
        lf.update_current_generation(
            model=model,
            input=_last_input_block(messages),
            output=output,
            usage_details=usage or None,
            metadata=metadata,
        )
    except Exception:
        pass


# ════════════════════════════════════════════
#  P1-14: LLM transient 错误分类
#  429 限流 / 5xx / Timeout / Connection / 空响应 重试;
#  其余 4xx (bad_request / auth_error 等业务错误) 不重试.
# ════════════════════════════════════════════

_HTTP_TOO_MANY_REQUESTS = 429  # noqa: hardcode  # 限流状态码 (协议常量)


def _is_transient_llm_error(exc: BaseException) -> bool:
    """判断异常是否 transient (429 限流 / 5xx / Timeout / Connection / 空响应), 应重试.

    429 限流是教科书级瞬态错误 — 上游在说"慢一点", 退避后重试即可成功.
    其余 4xx (bad_request / auth_error) 是永久业务错误, 不重试.
    """
    if isinstance(exc, EmptyLLMResponseError):
        return True

    # ── OpenAI 兼容 (默认线路: 任何 OpenAI-shaped 端点) ──
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code == _HTTP_TOO_MANY_REQUESTS or exc.status_code >= 500

    # ── Anthropic (Claude) ──
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code == _HTTP_TOO_MANY_REQUESTS or exc.status_code >= 500

    return False


def _status_code_of(exc: BaseException) -> int | None:
    """从 SDK 异常取 HTTP status_code (无 response 属性返回 None)."""
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None)


def _retry_after_secs(exc: BaseException) -> float | None:
    """解析 response 的 Retry-After 头 (秒). 缺失/非法返回 None.

    httpx.Headers 大小写不敏感, headers.get("retry-after") 即可命中.
    """
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _retry_budget(exc: BaseException) -> int:
    """该异常类型的最大重试次数. 429 限流给专属更大预算, 其余瞬态用通用预算."""
    if _status_code_of(exc) == _HTTP_TOO_MANY_REQUESTS:
        return settings.llm_rate_limit_retry_max
    return settings.llm_retry_max


def _retry_wait_secs(exc: BaseException, attempt: int) -> float:
    """重试退避秒数.

    429 限流: 优先尊重 Retry-After (cap 60s), 否则 5/10/20 指数退避 —
      限流窗口需更长冷却, 区别于 SDK 自带的亚秒级重试.
    其余瞬态 (5xx/Timeout/Connection): 1/2/4 ... cap 10s 短退避.
    """
    if _status_code_of(exc) == _HTTP_TOO_MANY_REQUESTS:
        ra = _retry_after_secs(exc)
        if ra is not None:
            return min(ra, 60.0)
        return min(2 ** attempt * 5, 20.0)
    return min(2 ** attempt, 10.0)


# ════════════════════════════════════════════
#  主接口 — 每次调用落一个 generation 观测
# ════════════════════════════════════════════

@observe(as_type="generation", name="chat_completion", capture_input=False, capture_output=False)
def chat_completion(
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    provider: str | None = None,
    extra_body: dict | None = None,
    thinking: bool | None = None,
    namespace_id: int | None = None,
) -> str:
    """统一聊天补全接口。

    thinking 控制思考模式: None=取 settings 默认, True/False=显式开/关。
    extra_body 仅用于非 thinking 的厂商扩展参数, 其中的 thinking 键会被覆盖。
    namespace_id: 请求所属命名空间, 用于按优先级解析配置 (namespace > 全局)。
    """
    from app.engine.model_registry import registry
    cfg = registry.resolve_chat_config(namespace_id)
    if cfg is None:
        raise RuntimeError(
            "无激活的 Chat 模型配置，请前往「模型管理」页面添加并激活 CHAT 类型配置。"
        )
    thinking = settings.llm_thinking_enabled if thinking is None else thinking
    provider = provider or cfg["protocol"]
    if provider == "anthropic":
        return _claude_chat_with_retry(messages, cfg, temperature, max_tokens, thinking=thinking)
    return _openai_chat_with_retry(messages, cfg, temperature, max_tokens,
                                   extra_body=_build_extra_body(thinking, extra_body))


# ════════════════════════════════════════════
#  OpenAI — OpenAI Chat Completions 协议, 直通
#  (DashScope / DeepSeek / vLLM / 官方 OpenAI 等任意兼容端点)
# ════════════════════════════════════════════

def _openai_chat(messages: list[dict], cfg: dict[str, Any],
                 temperature: float | None = None, max_tokens: int | None = None,
                 extra_body: dict | None = None) -> str:
    client = _get_openai_client(cfg)
    model = cfg["model_name"]
    temp = temperature if temperature is not None else cfg.get("temperature", 0.1)
    mt = max_tokens if max_tokens is not None else cfg.get("max_tokens", settings.llm_max_tokens_default)

    # ── L3 extra_body 退化: 代理不支持时移除重试 (如 thinking:disabled 被拒) ──
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temp,
            max_tokens=mt,
            extra_body=extra_body,
        )
    except openai.BadRequestError as e:
        if extra_body is not None:
            logger.warning(
                "OpenAI extra_body 被代理拒绝 (status=%s), 移除后重试: %s",
                getattr(e, 'status_code', '?'), e,
            )
            resp = client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temp,
                max_tokens=mt,
                extra_body=None,
            )
        else:
            raise

    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    if not text.strip():
        # Record to Langfuse BEFORE raising — preserves input for diagnostics
        _record_generation(
            model=model, messages=messages, output="[EMPTY_RESPONSE]",
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )
        raise EmptyLLMResponseError(
            f"OpenAI-compatible endpoint returned empty content (model={model}, "
            f"finish_reason={resp.choices[0].finish_reason})"
        )
    _record_generation(
        model=model, messages=messages, output=text,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
    )
    return text


def _openai_chat_with_retry(messages: list[dict], cfg: dict[str, Any],
                            temperature: float | None = None, max_tokens: int | None = None,
                            extra_body: dict | None = None) -> str:
    """OpenAI-compatible chat + transient-error retry (按异常类型取退避预算)."""
    attempt = 0
    while True:
        try:
            return _openai_chat(messages, cfg, temperature, max_tokens, extra_body=extra_body)
        except Exception as e:
            if not _is_transient_llm_error(e):
                raise
            budget = _retry_budget(e)
            if attempt >= budget:
                raise
            wait_secs = _retry_wait_secs(e, attempt)
            logger.warning(
                "openai transient error retry %d/%d after %.1fs: %s",
                attempt + 1, budget, wait_secs, e,
            )
            time.sleep(wait_secs)
            attempt += 1


# ════════════════════════════════════════════
#  Claude content 解析 — 无差别提取 TextBlock
#  ── 不干预 thinking 决策, 但保证拿得到结果.
#     · [TextBlock]                 → 取 text
#     · [ThinkingBlock, TextBlock]  → 取 text (忽略 thinking)
#     · [TextBlock, ToolUse, ...]   → 拼接全部 text
#     · [ThinkingBlock]             → 返回空, 上层判空触发降级重试
# ════════════════════════════════════════════

def _extract_claude_text(content) -> str:
    return "".join(
        b.text for b in (content or [])
        if getattr(b, "type", None) == "text" and hasattr(b, "text")
    )


def _block_types(content) -> list[str]:
    return [getattr(b, "type", type(b).__name__) for b in (content or [])]


# ════════════════════════════════════════════
#  Claude — Anthropic API, 需要适配 message 格式
#  核心差异: system 不在 messages 里, 是独立参数
# ════════════════════════════════════════════

def _claude_chat(messages: list[dict], cfg: dict[str, Any],
                 temperature: float | None = None, max_tokens: int | None = None,
                 thinking: bool = False) -> str:
    client = _get_claude_client(cfg)
    model = cfg["model_name"]
    temp = temperature if temperature is not None else cfg.get("temperature", 0.1)
    mt = max_tokens if max_tokens is not None else cfg.get("max_tokens", settings.llm_max_tokens_default)

    # 提取 system message (Claude API 要求独立传)
    system_text = ""
    user_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text += msg["content"] + "\n"
        else:
            user_messages.append({"role": msg["role"], "content": msg["content"]})

    # Claude 要求第一条必须是 user
    if not user_messages or user_messages[0]["role"] != "user":
        user_messages.insert(0, {"role": "user", "content": "请根据以上要求回答。"})
    system_param = system_text.strip() or None
    thinking_cfg = _claude_thinking_cfg(thinking)

    # ── 调用内核 (含 empty-text retry + Langfuse recording) ──
    def _call(tcfg: dict | None) -> str:
        # 大 max_tokens 必须走流式, 否则 SDK 预估超 10min 会直接拒绝
        # SDK 公式: expected_time = 3600 × max_tokens / 128000, > 600s 即 raise
        # 解出真实临界 21333, 留 buffer 取 21000
        if mt > 21000:
            text_parts: list[str] = []
            input_tokens: int | None = None
            output_tokens: int | None = None
            final = None
            with client.messages.stream(
                model=model,
                system=system_param,  # type: ignore[arg-type]
                messages=user_messages,  # type: ignore[arg-type]
                temperature=temp,
                max_tokens=mt,
                thinking=tcfg,  # type: ignore[arg-type]
            ) as stream:
                for text in stream.text_stream:
                    text_parts.append(text)
                final = stream.get_final_message()
                if final and final.usage:
                    input_tokens = final.usage.input_tokens
                    output_tokens = final.usage.output_tokens
            result = "".join(text_parts)
            if not result:
                blocks = _block_types(final.content) if final else []
                stop = final.stop_reason if final else None
                thinking_preview = ""
                for b in (final.content if final else []) or []:
                    if getattr(b, "type", None) == "thinking":
                        t = getattr(b, "thinking", "") or ""
                        thinking_preview = t[:500]
                        break
                diag = (
                    f"<EMPTY_TEXT> blocks={blocks} stop_reason={stop} "
                    f"in_tok={input_tokens} out_tok={output_tokens} "
                    f"thinking_preview={thinking_preview!r}"
                )
                logger.warning("Claude 流式返回空文本: %s", diag)
                _record_generation(
                    model=model, messages=messages, output=diag,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                )
                raise EmptyLLMResponseError(
                    f"Claude 流式返回空文本 blocks={blocks} stop_reason={stop} "
                    f"out_tok={output_tokens}"
                )
            _record_generation(
                model=model, messages=messages, output=result,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
            return result

        # ── 非流式: 空文本重试一次 ──
        resp = client.messages.create(
            model=model,
            system=system_param,  # type: ignore[arg-type]
            messages=user_messages,  # type: ignore[arg-type]
            temperature=temp,
            max_tokens=mt,
            thinking=tcfg,  # type: ignore[arg-type]
        )
        text = _extract_claude_text(resp.content)
        if not text:
            blocks1 = _block_types(resp.content)
            stop1 = resp.stop_reason
            logger.warning(
                "Claude 首次空文本 blocks=%s stop=%s — 原参数重试", blocks1, stop1,
            )
            resp = client.messages.create(
                model=model,
                system=system_param,  # type: ignore[arg-type]
                messages=user_messages,  # type: ignore[arg-type]
                temperature=temp,
                max_tokens=mt,
                thinking=tcfg,  # type: ignore[arg-type]
            )
            text = _extract_claude_text(resp.content)
            if not text:
                blocks2 = _block_types(resp.content)
                stop2 = resp.stop_reason
                diag = (
                    f"<EMPTY_TEXT> first=(blocks={blocks1},stop={stop1}) "
                    f"retry=(blocks={blocks2},stop={stop2})"
                )
                _record_generation(
                    model=model, messages=messages, output=diag,
                    input_tokens=resp.usage.input_tokens if resp.usage else None,
                    output_tokens=resp.usage.output_tokens if resp.usage else None,
                )
                raise EmptyLLMResponseError(
                    f"Claude 两次调用均无 TextBlock "
                    f"first=(blocks={blocks1},stop={stop1}) "
                    f"retry=(blocks={blocks2},stop={stop2})"
                )

        _record_generation(
            model=model, messages=messages, output=text,
            input_tokens=resp.usage.input_tokens if resp.usage else None,
            output_tokens=resp.usage.output_tokens if resp.usage else None,
        )
        return text

    # ── L3 thinking 退化: 代理/模型不支持时自动关闭重试 ──
    try:
        return _call(thinking_cfg)
    except anthropic.BadRequestError as e:
        if thinking_cfg is not None:
            logger.warning(
                "Claude thinking 被代理拒绝 (status=%s), 关闭后重试: %s",
                getattr(e, 'status_code', '?'), e,
            )
            return _call(None)
        raise


def _claude_chat_with_retry(
    messages: list[dict], cfg: dict[str, Any],
    temperature: float | None = None, max_tokens: int | None = None,
    thinking: bool = False,
) -> str:
    """Claude chat with transient-error retry (按异常类型取退避预算)."""
    attempt = 0
    while True:
        try:
            return _claude_chat(messages, cfg, temperature, max_tokens, thinking=thinking)
        except Exception as e:
            if not _is_transient_llm_error(e):
                raise
            budget = _retry_budget(e)
            if attempt >= budget:
                raise
            wait_secs = _retry_wait_secs(e, attempt)
            logger.warning(
                "claude transient error retry %d/%d after %.1fs: %s",
                attempt + 1, budget, wait_secs, e,
            )
            time.sleep(wait_secs)
            attempt += 1


# ════════════════════════════════════════════
#  带截断检测的接口 — 零侵入, 新增不改旧
# ════════════════════════════════════════════

class LLMResponse:
    """LLM 响应 + 截断元数据"""
    __slots__ = ("text", "truncated")

    def __init__(self, text: str, truncated: bool):
        self.text = text
        self.truncated = truncated


@observe(
    as_type="generation",
    name="chat_completion_checked",
    capture_input=False,
    capture_output=False,
)
def chat_completion_checked(
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    provider: str | None = None,
    extra_body: dict | None = None,
    thinking: bool | None = None,
    namespace_id: int | None = None,
) -> LLMResponse:
    """同 chat_completion, 额外返回截断状态。thinking 同上。
    namespace_id: 请求所属命名空间, 用于按优先级解析配置。
    """
    from app.engine.model_registry import registry

    cfg = registry.resolve_chat_config(namespace_id)
    if cfg is None:
        raise RuntimeError(
            "无激活的 Chat 模型配置，请前往「模型管理」页面添加并激活 CHAT 类型配置。"
        )
    thinking = settings.llm_thinking_enabled if thinking is None else thinking
    provider = provider or cfg["protocol"]

    if provider == "anthropic":
        return _claude_chat_checked(messages, cfg, temperature, max_tokens, thinking=thinking)
    return _openai_chat_checked(messages, cfg, temperature, max_tokens,
                                extra_body=_build_extra_body(thinking, extra_body))


def _openai_chat_checked(
    messages: list[dict], cfg: dict[str, Any],
    temperature: float | None = None, max_tokens: int | None = None,
    extra_body: dict | None = None,
) -> LLMResponse:
    client = _get_openai_client(cfg)
    model = cfg["model_name"]
    temp = temperature if temperature is not None else cfg.get("temperature", 0.1)
    mt = max_tokens if max_tokens is not None else cfg.get("max_tokens", settings.llm_max_tokens_default)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        temperature=temp,
        max_tokens=mt,
        extra_body=extra_body,
    )
    truncated = resp.choices[0].finish_reason == "length"
    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    _record_generation(
        model=model, messages=messages, output=text,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
    )
    return LLMResponse(text, truncated)


def _claude_chat_checked(
    messages: list[dict], cfg: dict[str, Any],
    temperature: float | None = None, max_tokens: int | None = None,
    thinking: bool = False,
) -> LLMResponse:
    client = _get_claude_client(cfg)
    model = cfg["model_name"]
    temp = temperature if temperature is not None else cfg.get("temperature", 0.1)
    mt = max_tokens if max_tokens is not None else cfg.get("max_tokens", settings.llm_max_tokens_default)

    system_text = ""
    user_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text += msg["content"] + "\n"
        else:
            user_messages.append({"role": msg["role"], "content": msg["content"]})

    if not user_messages or user_messages[0]["role"] != "user":
        user_messages.insert(0, {"role": "user", "content": "请根据以上要求回答。"})
    system_param = system_text.strip() or None
    thinking_cfg = _claude_thinking_cfg(thinking)

    # ── 首次: 不干预 thinking ──
    resp = client.messages.create(
        model=model,
        system=system_param,  # type: ignore[arg-type]
        messages=user_messages,  # type: ignore[arg-type]
        temperature=temp,
        max_tokens=mt,
        thinking=thinking_cfg,  # type: ignore[arg-type]
    )
    text = _extract_claude_text(resp.content)

    # ── 空文本 → 假设服务端偶发抖动, 原参数重试一次 ──
    if not text:
        blocks1 = _block_types(resp.content)
        stop1 = resp.stop_reason
        logger.warning(
            "Claude checked 首次空文本 blocks=%s stop=%s — 原参数重试", blocks1, stop1,
        )
        resp = client.messages.create(
            model=model,
            system=system_param,  # type: ignore[arg-type]
            messages=user_messages,  # type: ignore[arg-type]
            temperature=temp,
            max_tokens=mt,
            thinking=thinking_cfg,  # type: ignore[arg-type]
        )
        text = _extract_claude_text(resp.content)
        if not text:
            blocks2 = _block_types(resp.content)
            stop2 = resp.stop_reason
            diag = (
                f"<EMPTY_TEXT> first=(blocks={blocks1},stop={stop1}) "
                f"retry=(blocks={blocks2},stop={stop2})"
            )
            _record_generation(
                model=model, messages=messages, output=diag,
                input_tokens=resp.usage.input_tokens if resp.usage else None,
                output_tokens=resp.usage.output_tokens if resp.usage else None,
            )
            raise EmptyLLMResponseError(
                f"Claude checked 两次调用均无 TextBlock "
                f"first=(blocks={blocks1},stop={stop1}) "
                f"retry=(blocks={blocks2},stop={stop2})"
            )

    truncated = resp.stop_reason == "max_tokens"
    _record_generation(
        model=model, messages=messages, output=text,
        input_tokens=resp.usage.input_tokens if resp.usage else None,
        output_tokens=resp.usage.output_tokens if resp.usage else None,
    )
    return LLMResponse(text, truncated)


# ════════════════════════════════════════════
#  Stage 4 Task 1 — Tool-use 适配层
#  中性 tool spec → provider 转换 → ToolUseResponse
# ════════════════════════════════════════════


@dataclass
class ToolCall:
    id: str                     # tool_call_id, 用于结果回喂时与 tool_result 配对
    name: str                   # tool 名
    input: dict                 # tool 参数 (已 JSON 解码)
    parse_error: str | None = None  # JSON 解析失败时的诊断信号, 下游据此跳过工具执行


@dataclass
class ToolUseResponse:
    text: str                       # LLM 文本回复 (可能为空)
    tool_calls: list[ToolCall]
    stop_reason: str                # "tool_use" | "end_turn" | "max_tokens" | "stop" | "tool_calls"
    usage: dict = field(default_factory=dict)
    reasoning_content: str | None = None  # DeepSeek 思考模式下多轮回传需要


@observe(
    as_type="generation",
    name="chat_completion_with_tools",
    capture_input=False,
    capture_output=False,
)
async def chat_completion_with_tools(
    messages: list[dict],
    tools: list[dict],
    provider: str | None = None,
    stream_callback: Callable[[dict], Awaitable[None]] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra_body: dict | None = None,
    thinking: bool | None = None,
    namespace_id: int | None = None,
) -> ToolUseResponse:
    """统一 tool_use 入口。thinking 控制思考模式: None=取 settings 默认, True/False=显式开/关。

    extra_body 仅用于非 thinking 的厂商扩展参数, 其中的 thinking 键会被覆盖。
    namespace_id: 请求所属命名空间, 用于按优先级解析配置。
    """
    from app.engine.model_registry import registry
    cfg = registry.resolve_chat_config(namespace_id)
    if cfg is None:
        raise RuntimeError(
            "无激活的 Chat 模型配置，请前往「模型管理」页面添加并激活 CHAT 类型配置。"
        )
    thinking = settings.llm_thinking_enabled if thinking is None else thinking
    provider = provider or cfg["protocol"]
    if provider == "anthropic":
        resp = await asyncio.to_thread(
            _claude_tool_use, messages, tools, cfg, temperature, max_tokens, thinking=thinking,
        )
    else:
        resp = await asyncio.to_thread(
            _openai_tool_use, messages, tools, cfg, temperature, max_tokens,
            extra_body=_build_extra_body(thinking, extra_body),
        )
    _coerce_tool_call_args(resp.tool_calls, tools)
    return resp


# ── OpenAI-compatible (Chat Completions function calling) ──

def build_assistant_message(response: ToolUseResponse, *,
                             tool_calls: list[ToolCall] | None = None) -> dict:
    """构造中性 assistant 消息，含 reasoning_content（如有）。

    收敛 agent_loop / extraction_agent / explorer / trainer 四处重复的
    dict 构造 + reasoning_content 条件注入逻辑，保证多轮回传字段一致性。

    tool_calls 默认取 response.tool_calls；extraction_agent 等需要按实际
    执行结果筛选时可显式传入 processed_tcs。
    """
    tcs = tool_calls if tool_calls is not None else response.tool_calls
    msg: dict = {
        "role": "assistant",
        "content": response.text or "",
        "tool_calls": [
            {"id": tc.id, "name": tc.name, "input": tc.input}
            for tc in tcs
        ],
    }
    if response.reasoning_content:
        msg["reasoning_content"] = response.reasoning_content
    return msg


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """把中性消息格式适配为 OpenAI Chat Completions 线格式.

    中性 assistant 消息 (agent_loop 产出):
        {"role": "assistant", "content": str, "tool_calls": [...], "reasoning_content": str|None}
    OpenAI 线格式: reasoning_content 需随 assistant 消息回传 (DeepSeek 思考模式要求).

    system / user / tool 消息本就符合 OpenAI, 原样透传.
    """
    converted: list[dict] = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            msg: dict = {
                "role": "assistant",
                "content": m.get("content") or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["input"], ensure_ascii=False),
                        },
                    }
                    for tc in m["tool_calls"]
                ],
            }
            rc = m.get("reasoning_content")
            if rc:
                msg["reasoning_content"] = rc
            converted.append(msg)
        else:
            converted.append(m)
    return converted


def _openai_tool_use(
    messages: list[dict], tools: list[dict],
    cfg: dict[str, Any],
    temperature: float | None = None, max_tokens: int | None = None,
    extra_body: dict | None = None,
) -> ToolUseResponse:
    client = _get_openai_client(cfg)
    model = cfg["model_name"]
    temp = temperature if temperature is not None else cfg.get("temperature", 0.1)
    mt = max_tokens if max_tokens is not None else cfg.get("max_tokens", settings.llm_max_tokens_default)
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]
    resp = client.chat.completions.create(
        model=model,
        messages=_to_openai_messages(messages),  # type: ignore[arg-type]
        tools=openai_tools,  # type: ignore[arg-type]
        temperature=temp,
        max_tokens=mt,
        extra_body=extra_body,
    )
    choice = resp.choices[0]
    msg = choice.message
    text = msg.content or ""
    reasoning_content = getattr(msg, "reasoning_content", None) or None
    raw_calls = getattr(msg, "tool_calls", None) or []
    tool_calls = [
        ToolCall(
            id=tc.id,
            name=tc.function.name,
            input=parsed,
            parse_error=parse_error,
        )
        for tc in raw_calls
        for parsed, parse_error in [_safe_json_loads(tc.function.arguments)]
    ]
    usage = getattr(resp, "usage", None)
    usage_dict = {
        "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }
    _record_generation(
        model=model, messages=messages,
        output={"text": text, "tool_calls": [tc.__dict__ for tc in tool_calls]},
        input_tokens=usage_dict["input_tokens"], output_tokens=usage_dict["output_tokens"],
        tools=openai_tools,
    )
    return ToolUseResponse(
        text=text,
        tool_calls=tool_calls,
        stop_reason=choice.finish_reason or "stop",
        usage=usage_dict,
        reasoning_content=reasoning_content,
    )


# ── Claude (Anthropic native tool_use blocks) ──

def _claude_tool_use(
    messages: list[dict], tools: list[dict],
    cfg: dict[str, Any],
    temperature: float | None = None, max_tokens: int | None = None,
    thinking: bool = False,
) -> ToolUseResponse:
    client = _get_claude_client(cfg)
    model = cfg["model_name"]
    temp = temperature if temperature is not None else cfg.get("temperature", 0.1)
    mt = max_tokens if max_tokens is not None else cfg.get("max_tokens", settings.llm_max_tokens_default)

    system_text = ""
    user_messages: list[dict] = []
    tool_results_buffer: list[dict] = []  # 缓存连续的 tool results

    for m in messages:
        if m["role"] == "system":
            system_text += m["content"] + "\n"
        elif m["role"] == "tool":
            # 收集 tool result 到 buffer
            tool_results_buffer.append({
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m["content"],
            })
        elif m["role"] == "assistant":
            # 遇到 assistant 消息，先 flush tool results buffer
            if tool_results_buffer:
                user_messages.append({
                    "role": "user",
                    "content": tool_results_buffer.copy()
                })
                tool_results_buffer.clear()

            # 转换 assistant 消息：OpenAI 格式 → Claude 格式
            # OpenAI: {"role": "assistant", "content": str, "tool_calls": [...]}
            # Claude:  {"role": "assistant", "content":
            #            [{"type": "text", ...}, {"type": "tool_use", ...}]}
            content_blocks = []
            if m.get("content"):
                content_blocks.append({"type": "text", "text": m["content"]})

            for tc in m.get("tool_calls", []):
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })

            user_messages.append({
                "role": "assistant",
                "content": content_blocks
            })
        else:
            # 其他消息（user），先 flush buffer
            if tool_results_buffer:
                user_messages.append({
                    "role": "user",
                    "content": tool_results_buffer.copy()
                })
                tool_results_buffer.clear()
            user_messages.append(m)

    # 最后 flush 剩余的 tool results
    if tool_results_buffer:
        user_messages.append({
            "role": "user",
            "content": tool_results_buffer.copy()
        })

    if not user_messages or user_messages[0]["role"] != "user":
        user_messages.insert(0, {"role": "user", "content": "请根据以上要求回答。"})
    system_param = system_text.strip() or anthropic.NOT_GIVEN

    # Claude 的 input_schema 字段名与中性 spec 一致, 直接透传
    claude_tools = [
        {"name": t["name"], "description": t.get("description", ""),
         "input_schema": t["input_schema"]}
        for t in tools
    ]
    thinking_cfg = _claude_thinking_cfg(thinking)

    resp = client.messages.create(
        model=model,
        system=system_param,  # type: ignore[arg-type]
        messages=user_messages,  # type: ignore[arg-type]
        tools=claude_tools,  # type: ignore[arg-type]
        temperature=temp,
        max_tokens=mt,
        thinking=thinking_cfg,  # type: ignore[arg-type]
    )

    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in resp.content or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif btype == "tool_use":
            raw_id = getattr(block, "id", "") or ""
            tool_calls.append(ToolCall(
                id=_sanitize_tool_use_id(raw_id),
                name=getattr(block, "name"),
                input=dict(getattr(block, "input", {}) or {}),
            ))

    text = "".join(text_parts)
    usage_dict = {
        "input_tokens": resp.usage.input_tokens if resp.usage else 0,
        "output_tokens": resp.usage.output_tokens if resp.usage else 0,
    }
    _record_generation(
        model=model, messages=messages,
        output={"text": text, "tool_calls": [tc.__dict__ for tc in tool_calls]},
        input_tokens=usage_dict["input_tokens"], output_tokens=usage_dict["output_tokens"],
        tools=claude_tools,
    )
    return ToolUseResponse(
        text=text,
        tool_calls=tool_calls,
        stop_reason=resp.stop_reason or "end_turn",
        usage=usage_dict,
    )


def _safe_json_loads(raw: str | None) -> tuple[dict, str | None]:
    """解析 tool_use arguments JSON.

    Returns:
        (parsed_dict, error_diagnostic_or_None)

    error_diagnostic 仅包含 JSON 解码错误 + 原始参数诊断片段 (≤ ~400 字符),
    供下游直接喂给 LLM, 让其自行判断失败原因并调整策略。
    """
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
        return (parsed if isinstance(parsed, dict) else {}, None)
    except json.JSONDecodeError as e:
        logger.warning("OpenAI-compatible tool arguments not valid JSON: %r", raw[:200])
        threshold = 280  # noqa: hardcode
        if len(raw) > threshold:
            snippet = (
                f"↓ 参数头部 (前 200 字符):\n{raw[:200]}\n"
                f"↓ 参数尾部 (后 80 字符):\n{raw[-80:]}"
            )
        else:
            snippet = f"↓ 完整参数:\n{raw}"
        return {}, f"工具参数 JSON 解析失败: {e}\n{snippet}"


def _coerce_tool_call_args(tool_calls: list[ToolCall], tools: list[dict]) -> None:
    """还原被 provider 适配层拍平成字符串的嵌套 object/array 入参 (就地修改).

    部分 Anthropic-兼容代理 (如 DeepSeek modelproxy) 会把 tool_use 入参中声明为
    object/array 的嵌套字段拍平成 JSON 字符串, 导致下游 `query.get(...)` 等在
    str 上调用 dict 方法报 `'str' object has no attribute 'get'`. 这里按 tool spec
    的 input_schema 声明类型, 把本应是 object/array 却收到 str 的字段用统一解析器
    还原. 解析失败保持原值, 交给后续自然报错, 不掩盖真正畸形的输入.
    """
    prop_types: dict[str, dict[str, str | None]] = {}
    for spec in tools:
        name = spec.get("name")
        props = (spec.get("input_schema") or {}).get("properties") or {}
        if name:
            prop_types[name] = {
                k: v.get("type") for k, v in props.items() if isinstance(v, dict)
            }

    for tc in tool_calls:
        types = prop_types.get(tc.name)
        if not types or not isinstance(tc.input, dict):
            continue
        for key, value in tc.input.items():
            if not isinstance(value, str):
                continue
            declared = types.get(key)
            if declared not in ("object", "array"):
                continue
            parsed = parse_llm_json(value, expect="dict" if declared == "object" else "list")
            if parsed is not None:
                tc.input[key] = parsed

