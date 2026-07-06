"""Stage 4 Task 2 — Agent loop 主循环骨架.

设计要点 (与 02-agent-loop-design.md §2 对齐):
- 每 iteration 头部 `await asyncio.sleep(0)` — 强制让出, cancel 检查点
- 每 iteration 头部 poll user_correction_queue (abort → CancelledError, redirect → 注入 messages)
- 调 chat_completion_with_tools (或注入的 llm callable, 便于测试)
- 无 tool_calls → break 给最终答案
- 有 tool_calls → asyncio.gather 并发 (Semaphore 限流) → 包装错误回喂
- 死循环检测: 最近 N 次 (window) 同 tool_name + 同 input → break + emit warning
- 3 桶配额兜底: exploratory / decisive / total, 超限 final_answer 带进展提示
- CancelledError: except 块清理 _active_agent_workers + emit cancelled 事件
- finally: 不论何种结束都从注册表移除

关键纪律:
- 业务层不写 `try/except Exception` 误吞 CancelledError
- 长循环 (batched_aggregate 等) 在每 batch 头部自行 sleep(0)
- tool_result 用 OpenAI 通用 `{"role": "tool", "tool_call_id", "content"}` 模板,
  Claude 路径在 chat_completion_with_tools 适配层做转换 (Stage 4 后续)

不变量: tool_trace[].output 始终是 dict (success: tool 自然输出; error: {error_type, error_message}). 消费方可放心走 dict 协议, 无需 isinstance 兜底.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re as _re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from langfuse import observe

from app.config import settings
from app.engine.llm import ToolCall, ToolUseResponse, chat_completion_with_tools
from app.logging_config import trace_id_var

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Forced_Clarify 用户可选动作 (A9.2) — surface 为常量, 不内联字面量
FORCED_CLARIFY_OPTIONS = ["更换查询方案", "收窄查询范围", "终止本次查询"]


# ════════════════════════════════════════════
#  Stage 2 抓手 C: Self-RAG reflection 抽取
# ════════════════════════════════════════════

_REFLECTION_BLOCK = _re.compile(
    r"\[REFLECTION\](.*?)\[/REFLECTION\]", _re.DOTALL | _re.IGNORECASE,
)
_REFLECTION_FIELD = _re.compile(
    r"^\s*(confidence|reason|alternative)\s*[:：]\s*(.+?)\s*$",
    _re.MULTILINE | _re.IGNORECASE,
)


def _extract_reflection(thinking_text: str, tool_name: str) -> dict | None:
    """从 thinking 块的 [REFLECTION]...[/REFLECTION] 锚点提取字段. 解析失败返 None.

    解析规则:
    - 必须存在 [REFLECTION] / [/REFLECTION] 配对锚点 (大小写不敏感)
    - 锚点内逐行匹配 `confidence:` / `reason:` / `alternative:` 字段
    - confidence 转 float 失败 → None (字段缺失而非整体跳过)
    """
    if not thinking_text:
        return None
    block_m = _REFLECTION_BLOCK.search(thinking_text)
    if not block_m:
        return None
    body = block_m.group(1)
    fields: dict[str, str] = {}
    for fm in _REFLECTION_FIELD.finditer(body):
        fields[fm.group(1).lower()] = fm.group(2).strip()

    confidence: float | None = None
    raw_conf = fields.get("confidence")
    if raw_conf:
        try:
            v = float(raw_conf)
            confidence = v if 0.0 <= v <= 1.0 else None
        except ValueError:
            confidence = None

    return {
        "tool_name": tool_name,
        "confidence": confidence,
        "reason": fields.get("reason", "")[:60],
        "alternative": fields.get("alternative", "")[:60],
    }


def _now_iso() -> str:
    """UTC 时间 ISO 格式, 用于 SSE 生命周期事件时间戳."""
    return datetime.now(timezone.utc).isoformat()


# ── 全局 worker 注册表 (cancel 端点用 trace_id 找 task) ──
_active_agent_workers: dict[str, asyncio.Task] = {}


# ════════════════════════════════════════════
#  公共查询接口 (P1-19 A2: api 层走此函数, 不直读私有 dict)
# ════════════════════════════════════════════

def is_agent_running(trace_id: str) -> bool:
    """trace_id 是否有活跃 agent task."""
    return trace_id in _active_agent_workers


def cancel_agent(trace_id: str) -> "tuple[bool, asyncio.Task | None]":
    """请求 cancel 某 trace_id 的 agent task.

    返 (是否 cancel 已发, task 引用供调用方 await grace).
    task 引用允许调用方在发 cancel 后继续 await asyncio.wait_for(task, timeout)
    等 worker finally cleanup 完成, 与原行为一致.
    返 (False, None) 表示 trace_id 不存在或已结束.
    """
    task = _active_agent_workers.get(trace_id)
    if task is None:
        return False, None
    task.cancel()
    return True, task


# ── 类型别名 ──
LLMCallable = Callable[..., Awaitable[ToolUseResponse]]
SSEEmit = Callable[[dict], Awaitable[None]]
ToolFn = Callable[..., Awaitable[Any]]

# 结果可被 present_result.ref 引用的数据型工具: 其输出注入 result_ref(=tool_call_id)
# 供 LLM 复述, 避免要求它复述不透明 tool_call_id (实证: 模型会编造).
_REFERENCEABLE_TOOLS: frozenset[str] = frozenset({"execute_query", "execute_plan"})


@dataclass
class AgentResult:
    final_answer: str
    iterations: int
    # stop_reason 取值:
    #   "end_turn" | "dead_loop" | "stagnation" | "cost_exhausted" | "cancelled"
    #   | "forced_clarify_timeout" | "forced_clarify_exhausted"
    # 已废弃（降级为 warning log，不再硬停）：
    #   "max_exploratory_calls" | "max_decisive_calls" | "max_total_iterations"
    stop_reason: str
    tool_trace: list[dict] = field(default_factory=list)
    usage_total: dict = field(default_factory=dict)


# ════════════════════════════════════════════
# ════════════════════════════════════════════
#  主循环
# ════════════════════════════════════════════

@observe(name="agent_loop", as_type="chain", capture_input=False, capture_output=False)
async def run_agent_loop(
    *,
    trace_id: str,
    question: str,
    tools_registry: dict[str, ToolFn],
    tool_specs: list[dict],
    sse_emit: SSEEmit,
    user_correction_queue: asyncio.Queue,
    system_prompt: str = "",
    llm: LLMCallable | None = None,
    # ── Stage 2 抓手 B: 反馈环 flush 需要 db / namespace_id ──
    db: "AsyncSession | None" = None,
    namespace_id: int | None = None,
    session_id: str | None = None,
) -> AgentResult:
    """Agent 主循环 (与设计文档 §2 完全对齐).

    Args:
        trace_id: 用作 cancel 句柄, 注册到 _active_agent_workers.
        question: 用户原始中文问题.
        tools_registry: tool name → async callable, **kwargs 接收 LLM 给的 input.
        tool_specs: 中性 tool spec 列表 (传给 chat_completion_with_tools).
        sse_emit: SSE 事件推送回调 (Stage 5 与真实 SSE 接).
        user_correction_queue: 用户纠偏事件队列 (abort/redirect/param_override).
        system_prompt: 已构建好的 system prompt 文本.
        llm: 注入点 — 测试用 FakeLLM, 生产留 None 走 chat_completion_with_tools.

    Returns:
        AgentResult — final_answer / iterations / stop_reason / tool_trace.

    Raises:
        asyncio.CancelledError: 用户 abort 或外部 task.cancel().
    """
    _active_agent_workers[trace_id] = asyncio.current_task()  # type: ignore[assignment]
    log_token = trace_id_var.set(trace_id)

    # ── Stage 2 抓手 B: 单点收口上下文 ──
    tool_trace: list[dict] = []
    reflection_log: list[dict] = []
    final_status: str = "completed"

    try:
        logger.info("[agent_loop] trace=%s 开始执行", trace_id)
        await sse_emit({"event": "agent_started", "data": {
            "trace_id": trace_id,
            "started_at": _now_iso(),
        }})
        logger.info("[agent_loop] trace=%s agent_started 已发送", trace_id)

        llm_fn: LLMCallable = llm if llm is not None else chat_completion_with_tools
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": question})

        usage_total: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        recent_tool_calls: list[tuple[str, str, str | None]] = []
        sem = asyncio.Semaphore(settings.agent_loop_max_tool_concurrency)

        # ── 3 桶计数 (interactive 永不入桶) ──
        exploratory_count = 0
        decisive_count = 0

        # ── Error_Class → Forced_Clarify 状态 ──
        from app.engine.tools.error_class import (
            ErrorClassWindow,
            is_error_output,
            normalize_error_class,
        )
        error_window = ErrorClassWindow(settings.agent_loop_error_class_window_size)
        forced_clarify_counts: dict[str, int] = {}

        # 配额: enabled=False 时全部置 maxsize, 仍走相同代码路径
        import sys
        if settings.agent_loop_iteration_limit_enabled:
            cap_explore = settings.agent_loop_max_exploratory_calls
            cap_decisive = settings.agent_loop_max_decisive_calls
            cap_total = settings.agent_loop_max_total_iterations
        else:
            cap_explore = cap_decisive = cap_total = sys.maxsize

        iteration = 0
        while True:
            iteration += 1
            await asyncio.sleep(0)  # cancel 检查点

            # ── 总轮次兜底 ──
            if iteration > cap_total:
                stop_reason = "cost_exhausted"
                await sse_emit({"event": "agent_finished", "data": {
                    "ended_at": _now_iso(),
                    "total_iterations": iteration - 1,
                    "total_tool_calls": len(tool_trace),
                    "stop_reason": stop_reason,
                }})
                return AgentResult(
                    final_answer=f"(已达总轮次成本后墙 {cap_total}, 当前进展见 tool_trace)",
                    iterations=iteration - 1,
                    stop_reason=stop_reason,
                    tool_trace=tool_trace,
                    usage_total=usage_total,
                )

            # ── 用户纠偏 ──
            for corr in _drain_queue(user_correction_queue):
                ctype = corr.get("correction_type")
                if ctype == "abort":
                    raise asyncio.CancelledError("user requested abort")
                if ctype == "redirect":
                    instruction = corr.get("instruction", "")
                    if instruction:
                        messages.append({
                            "role": "user",
                            "content": f"[用户纠偏] {instruction}",
                        })

            # ── LLM ──
            logger.info("[agent_loop] trace=%s iteration=%d 开始调用 LLM", trace_id, iteration)
            response = await llm_fn(
                messages=messages, tools=tool_specs, stream_callback=None,
            )
            logger.info("[agent_loop] trace=%s iteration=%d LLM 调用完成, tool_calls=%d",
                     trace_id, iteration, len(response.tool_calls))
            _accumulate_usage(usage_total, response.usage)

            if response.text:
                await sse_emit({"event": "text_delta", "data": {"delta": response.text}})

            # ── 终止: 无 tool_calls ──
            if not response.tool_calls:
                await sse_emit({"event": "agent_finished", "data": {
                    "ended_at": _now_iso(),
                    "total_iterations": iteration,
                    "total_tool_calls": len(tool_trace),
                    "stop_reason": "end_turn",
                }})
                return AgentResult(
                    final_answer=response.text,
                    iterations=iteration,
                    stop_reason="end_turn",
                    tool_trace=tool_trace,
                    usage_total=usage_total,
                )

            # ── Stage 2 抓手 C: reflection 抽取 ──
            if settings.agent_reflection_enabled and response.text:
                for tc in response.tool_calls:
                    r = _extract_reflection(response.text, tc.name)
                    if r:
                        reflection_log.append(r)

            # ── 桶配额检查 (interactive 不入桶, 未注册 tool 当 exploratory) ──
            # M3: 配额检查保持在执行之前 (拒绝即将超限的下一批); dead_loop 已下移到执行后
            from app.engine.tools.classification import classify_tool
            next_explore = 0
            next_decisive = 0
            for tc in response.tool_calls:
                try:
                    cat = classify_tool(tc.name)
                except KeyError:
                    cat = "exploratory"
                # C1: execute_query 按 mode 动态分桶 — probe/count 是探数据形态/规模,
                # 不产最终结果, 归 exploratory; single/batched 产最终结果, 归 decisive.
                if tc.name == "execute_query":
                    mode = (tc.input or {}).get("mode", "single")
                    cat = "exploratory" if mode in ("probe", "count") else "decisive"
                if cat == "exploratory":
                    next_explore += 1
                elif cat == "decisive":
                    next_decisive += 1

            # ── 桶配额：降级为可观测指标，不再硬停 ──
            # decisive/exploratory 计数无法区分"复杂查询的合法深探"与"无进展空转"，
            # count-based 上限会误杀差一步出结果的复杂查询。
            # 终止权交给：dead_loop（停滞精确特例）+ max_total_iterations（成本后墙）。
            if exploratory_count + next_explore > cap_explore:
                logger.warning(
                    "[agent_loop] 探索类工具调用超软上限 %d (当前=%d)，继续执行",
                    cap_explore, exploratory_count + next_explore,
                )
            if decisive_count + next_decisive > cap_decisive:
                logger.warning(
                    "[agent_loop] 决策类工具调用超软上限 %d (当前=%d)，继续执行",
                    cap_decisive, decisive_count + next_decisive,
                )

            # ── 推 tool_use 事件 ──
            for tc in response.tool_calls:
                await sse_emit({
                    "event": "tool_use",
                    "data": {"tool_call_id": tc.id, "name": tc.name, "input": tc.input},
                })

            # ── 添加 assistant 消息（包含 tool_use）──
            messages.append({
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "input": tc.input}
                    for tc in response.tool_calls
                ],
            })

            # ── 并发执行 ──
            tool_names = [tc.name for tc in response.tool_calls]
            logger.info("[agent_loop] trace=%s iteration=%d 开始执行工具 %s",
                     trace_id, iteration, tool_names)
            for tc in response.tool_calls:
                logger.info("[agent_loop] trace=%s iteration=%d tool=%s input=%s",
                         trace_id, iteration, tc.name,
                         _summarize(tc.input, limit=500))
            results = await asyncio.gather(
                *[_exec_or_reject(tc, tools_registry, sem) for tc in response.tool_calls],
                return_exceptions=False,
            )
            for tc, res in zip(response.tool_calls, results):
                logger.info("[agent_loop] trace=%s iteration=%d tool=%s status=%s output=%s",
                         trace_id, iteration, tc.name, res["status"],
                         _summarize(res["output"], limit=500))

            # ── 累加桶计数 (执行成功/失败一律计入) ──
            exploratory_count += next_explore
            decisive_count += next_decisive

            # ── Stage 2 抓手 B: 反馈环消费 (tool 执行后) ──
            from app.engine.recall_window import window_consume_next_call
            for tc in response.tool_calls:
                try:
                    window_consume_next_call(trace_id, tc.name)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[agent_loop] window_consume fail: %s", e)

            # ── 喂回 + emit tool_result ──
            for tc, res in zip(response.tool_calls, results):
                # 让 LLM 能从结果里读到可复述的 ref (present_result.ref 用它), 而非
                # 要求它复述不透明的 tool_call_id (实证: 模型会编造假 id). result_ref
                # 与 tool_trace 项的 id 一致, 使 finalization 按 ref 反查命中.
                if tc.name in _REFERENCEABLE_TOOLS and isinstance(res.get("output"), dict):
                    res["output"].setdefault("result_ref", tc.id)
                tool_trace.append({"id": tc.id, "name": tc.name, "input": tc.input,
                                    "output": res["output"], "status": res["status"]})
                await sse_emit({
                    "event": "tool_result",
                    "data": {"tool_call_id": tc.id, "name": tc.name,
                             "output": _summarize(res["output"]),
                             "status": res["status"]},
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": bound_tool_content(
                        res["output"],
                        budget_chars=settings.agent_tool_result_max_chars,
                    ),
                })
                # ── 修复 #2: dead_loop 历史在执行后才追加 (仅已执行调用) ──
                recent_tool_calls.append((
                    tc.name,
                    _hash_input(tc.input),
                    _progress_marker(res["output"]),
                ))
                # ── 更新 Error_Class 窗口 (R5.1-5.4, 修复 #1: status==ok 含 error 键也算错误) ──
                error_window.record(
                    normalize_error_class(res["output"])
                    if is_error_output(res["status"], res["output"])
                    else None
                )

            # ── Error_Class 阈值判定 → Forced_Clarify (R5.5, R6.2) — 严格先于 dead_loop ──
            fired_class = error_window.first_over_threshold(
                settings.agent_loop_error_class_threshold
            )
            if fired_class is not None:
                # 同类强制澄清次数上限 (R5.9)
                if (forced_clarify_counts.get(fired_class, 0)
                        >= settings.agent_loop_max_forced_clarify_per_class):
                    stop_reason = "forced_clarify_exhausted"
                    await sse_emit({"event": "agent_finished", "data": {
                        "ended_at": _now_iso(),
                        "total_iterations": iteration,
                        "total_tool_calls": len(tool_trace),
                        "stop_reason": stop_reason,
                    }})
                    return AgentResult(
                        final_answer=f"(已就错误类 {fired_class} 多次澄清仍未解决, 已中止)",
                        iterations=iteration,
                        stop_reason=stop_reason,
                        tool_trace=tool_trace,
                        usage_total=usage_total,
                    )
                forced_clarify_counts[fired_class] = forced_clarify_counts.get(fired_class, 0) + 1
                # caps 仅用于丰富文案; 失败返 None, 绝不阻断澄清 (R5.7)
                caps = await _resolve_caps_for_error(db, namespace_id, tool_trace, fired_class)
                clarify_fn = tools_registry.get("clarify_with_user")
                if clarify_fn is not None:
                    ans = await clarify_fn(
                        question=_forced_clarify_question(
                            fired_class, caps, error_window.count(fired_class)
                        ),
                        options=FORCED_CLARIFY_OPTIONS,
                        reason=f"重复命中错误类 {fired_class}",
                    )
                    if ans.get("timeout"):  # R5.10
                        await _abandon_pending(trace_id)
                        stop_reason = "forced_clarify_timeout"
                        await sse_emit({"event": "agent_finished", "data": {
                            "ended_at": _now_iso(),
                            "total_iterations": iteration,
                            "total_tool_calls": len(tool_trace),
                            "stop_reason": stop_reason,
                        }})
                        return AgentResult(
                            final_answer="(因反复命中同类错误发起澄清, 等待回应超时, 已中止)",
                            iterations=iteration,
                            stop_reason=stop_reason,
                            tool_trace=tool_trace,
                            usage_total=usage_total,
                        )
                    # R5.8: 重置该类计数 + 清 dead_loop 历史 (防紧随误终止)
                    error_window.reset_class(fired_class)
                    recent_tool_calls.clear()
                    messages.append({
                        "role": "user",
                        "content": f"[强制澄清回应] {ans.get('user_answer')}",
                    })
                    continue  # 进入下一迭代

            # ── 死循环/停滞检测 (修复 #2: 下移到执行后 + 错误类阈值判定之后) ──
            window = settings.agent_loop_dead_loop_window
            stop_signal = _loop_stop_signal(recent_tool_calls, window)
            if stop_signal:
                stop_reason, message = stop_signal
                await sse_emit({
                    "event": "warning",
                    "data": {"message": message},
                })
                await sse_emit({"event": "agent_finished", "data": {
                    "ended_at": _now_iso(),
                    "total_iterations": iteration,
                    "total_tool_calls": len(tool_trace),
                    "stop_reason": stop_reason,
                }})
                return AgentResult(
                    final_answer=f"({message}, 请人工介入)",
                    iterations=iteration,
                    stop_reason=stop_reason,
                    tool_trace=tool_trace,
                    usage_total=usage_total,
                )

    except asyncio.CancelledError:
        final_status = "cancelled"
        # ── P0-4 Task 7: cancel 来源区分通过日志, SSE 不发 reason ──
        from app.api.query import _cancel_reason
        reason = _cancel_reason.pop(trace_id, "external")
        logger.info(
            "agent cancelled (source=%s) trace=%s", reason, trace_id,
        )
        await sse_emit({"event": "cancelled", "data": {}})
        await _cleanup_on_cancel(trace_id)
        raise
    except Exception:
        final_status = "failed"
        raise
    finally:
        # ── Stage 2 抓手 B: 单点收口 — 反馈环 flush + trace 持久化 ──
        if db is not None:
            try:
                await _flush_recall_window(db, trace_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("[agent_loop] flush_recall_window fail trace=%s: %s", trace_id, e)
            try:
                await _persist_trace(
                    db=db,
                    trace_id=trace_id,
                    session_id=session_id,
                    namespace_id=namespace_id,
                    user_query=question,
                    tool_trace=tool_trace,
                    reflection_log=reflection_log,
                    status=final_status,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[agent_loop] persist_trace fail trace=%s: %s", trace_id, e)
        _active_agent_workers.pop(trace_id, None)
        trace_id_var.reset(log_token)


# ════════════════════════════════════════════
#  Stage 2 抓手 B: 反馈环 flush + trace 持久化 (单点收口)
# ════════════════════════════════════════════

async def _flush_recall_window(db: "AsyncSession", trace_id: str) -> None:
    """pop 反馈窗口, 批量 UPDATE knowledge_entries 的 recall/adopted/negative 计数.

    在 finally 块单次调用, 防多处 agent_finished 重复写.
    """
    from datetime import datetime

    from sqlalchemy import update

    from app.engine.recall_window import window_pop
    from app.models import KnowledgeEntry

    data = window_pop(trace_id)
    if data is None:
        return

    now = datetime.now()

    # recall_inc: 每个 entry_id 增加 recall_count + 更新 last_recalled_at
    for entry_id, inc in (data.get("recall_inc") or {}).items():
        await db.execute(
            update(KnowledgeEntry)
            .where(KnowledgeEntry.id == entry_id)
            .values(
                recall_count=KnowledgeEntry.recall_count + inc,
                last_recalled_at=now,
            )
        )

    # adopted_inc
    for entry_id, inc in (data.get("adopted_inc") or {}).items():
        await db.execute(
            update(KnowledgeEntry)
            .where(KnowledgeEntry.id == entry_id)
            .values(adopted_count=KnowledgeEntry.adopted_count + inc)
        )

    # negative_inc
    for entry_id, inc in (data.get("negative_inc") or {}).items():
        await db.execute(
            update(KnowledgeEntry)
            .where(KnowledgeEntry.id == entry_id)
            .values(negative_signal_count=KnowledgeEntry.negative_signal_count + inc)
        )

    await db.commit()
    logger.info(
        "[agent_loop] flush_recall_window trace=%s recall=%d adopted=%d negative=%d",
        trace_id,
        len(data.get("recall_inc") or {}),
        len(data.get("adopted_inc") or {}),
        len(data.get("negative_inc") or {}),
    )


async def _persist_trace(
    *,
    db: "AsyncSession",
    trace_id: str,
    namespace_id: int | None,
    user_query: str,
    tool_trace: list[dict],
    reflection_log: list[dict],
    status: str,
    session_id: str | None = None,
) -> None:
    """Task E 实现 agent_traces 入库."""
    import json as _json

    from app.models import AgentTrace

    # ── default=str 兜 BSON Timestamp / datetime / Decimal 等非 JSON 原生类型 ──
    # tool_trace 含 mongo driver 原始返回, 内嵌 bson.Timestamp 会让 json.dumps 抛
    # TypeError, 整个 trace 落不了库 → agent_traces 表常年空, 观测链断裂.
    payload = _json.dumps(
        {"tool_trace": tool_trace}, ensure_ascii=False, default=str,
    )[:settings.agent_trace_max_json_bytes]
    reflection = _json.dumps(
        reflection_log, ensure_ascii=False, default=str,
    )[:settings.agent_trace_max_reflection_bytes]

    trace = AgentTrace(
        trace_id=trace_id,
        session_id=session_id,
        namespace_id=namespace_id,
        user_query=user_query[:4096],
        trace_json=payload,
        reflection_log_json=reflection,
        status=status,
    )
    db.add(trace)
    await db.commit()
    logger.debug(
        "[agent_loop] _persist_trace done trace=%s status=%s tools=%d",
        trace_id, status, len(tool_trace),
    )


# ════════════════════════════════════════════
#  Cancel cleanup hooks (Stage 4 Task 11 — G3 carry-forward)
# ════════════════════════════════════════════

async def _abandon_pending(trace_id: str) -> None:
    """把该 trace 的 pending PendingClarification 翻 abandoned.

    forced_clarify 超时 (R5.10) 与 cancel cleanup 复用此 helper。
    单独 session, 异常吞为 log.warning, 绝不阻断上层 stop_reason 返回 / CancelledError 上抛。
    """
    from sqlalchemy import update

    from app.db.metadata import async_session
    from app.models.pending_clarification import PendingClarification

    try:
        async with async_session() as db:
            await db.execute(
                update(PendingClarification)
                .where(
                    PendingClarification.session_id == trace_id,
                    PendingClarification.status == "pending",
                )
                .values(status="abandoned")
            )
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("_abandon_pending 失败 trace_id=%s: %s", trace_id, e, exc_info=True)


async def _cleanup_on_cancel(trace_id: str) -> None:
    """CancelledError 触发后的兜底清理.

    职责:
      1. 把同 trace_id 的 PendingClarification(status='pending') 翻成 'abandoned'
         (复用 _abandon_pending), 避免遗弃记录卡住未来的会话续跑.
      2. 写一条 KnowledgeAuditLog action='cancel' actor_id=NULL entry_id=NULL
         (entry_id 已升 nullable 兼容 bulk 与系统操作), reason 含 trace_id 便于运维定位.

    设计纪律:
      - cancel 路径神圣不可阻 — 任何异常吞掉转 log.warning, 让 raise CancelledError 顺利上抛.
      - 单独 begin/commit, 与主 task 已 cancel 的 session 完全隔离.
    """
    # ── 1. PendingClarification 翻 abandoned (复用 helper) ──
    await _abandon_pending(trace_id)

    # ── 2. audit_log: action='cancel', entry_id/actor_id 均 NULL ──
    from app.db.metadata import async_session
    from app.knowledge.audit import write_audit

    try:
        async with async_session() as db:
            # to_status NOT NULL → 用 'cancelled' 占位 (与 sse 'cancelled' 事件语义对齐)
            await write_audit(
                db,
                entry_id=None,
                actor_id=None,
                action="cancel",
                from_status=None,
                to_status="cancelled",
                reason=f"agent_loop trace_id={trace_id} user_abort_or_external",
                diff={"trace_id": trace_id},
            )
            await db.commit()
    except Exception as e:  # noqa: BLE001
        # cancel 路径神圣 — cleanup 任何异常吞掉转 log.warning
        logger.warning("agent_loop cancel cleanup 失败 trace_id=%s: %s",
                       trace_id, e, exc_info=True)


# ════════════════════════════════════════════
#  辅助函数
# ════════════════════════════════════════════

def _drain_queue(q: asyncio.Queue) -> list[dict]:
    items: list[dict] = []
    while True:
        try:
            items.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


def _forced_clarify_question(fired_class: str, caps: dict | None, count: int) -> str:
    """构造强制澄清问题文本 (A9.2 / R5.7). caps 非空附能力详情, None 退化为仅错误类。"""
    head = (
        "我反复在同一处出错，已暂停等你定夺。\n"
        f"- 重复错误：{fired_class}（已连续命中 {count} 次）"
    )
    if not caps:
        return head + "\n我暂时拿不到该数据源的能力详情。你想怎么继续？"
    # caps 非空: 列出三类限制 + 对应 suggestion
    hints = {h.get("restriction"): h.get("suggestion") for h in caps.get("equivalent_hints", [])}
    lines: list[str] = []
    for kind in ("unsupported_ops", "unsupported_stage_variants", "syntax_constraints"):
        for item in caps.get(kind, []):
            sug = hints.get(item)
            lines.append(f"  - {item}" + (f" → {sug}" if sug else ""))
    detail = "\n".join(lines) if lines else "  (无显式限制项)"
    return (
        f"{head}\n- 数据源类型：{caps.get('flavor')}，它不支持：\n{detail}\n你想怎么继续？"
    )


async def _resolve_caps_for_error(
    db: "AsyncSession | None",
    namespace_id: int | None,
    tool_trace: list[dict],
    fired_class: str,
) -> dict | None:
    """解析触发 fired_class 的那次失败调用所对应数据源的 ServerCapabilities (A9.1 / R5.7).

    管线: tool_trace 中最近一条归一化后 == fired_class 的失败调用 → 取其 input.db_type/database
          → resolve_ds → get_driver().get_server_capabilities(ds)
    任一步失败/缺失/非 mongo → 返回 None (不阻断澄清)。
    """
    if db is None or namespace_id is None:
        return None
    try:
        from app.engine.tools.error_class import normalize_error_class

        target_call = None
        for call in reversed(tool_trace):
            if call.get("status") != "error" and "error" not in (call.get("output") or {}):
                continue
            if normalize_error_class(call.get("output")) == fired_class:
                target_call = call
                break
        if target_call is None:
            return None
        inp = target_call.get("input") or {}
        db_type = inp.get("db_type")
        database = inp.get("database")
        if not db_type or not database:
            return None

        from app.engine.drivers import get_driver
        from app.engine.tools._resolve_ds import resolve_ds

        ds = await resolve_ds(db, namespace_id, db_type, database)
        if ds is None:
            return None
        driver = get_driver(db_type)
        caps = await driver.get_server_capabilities(ds)
        return dict(caps) if caps is not None else None
    except Exception as e:  # noqa: BLE001 — caps 仅用于丰富文案, 失败不阻断澄清
        logger.warning("_resolve_caps_for_error 失败 fired_class=%s: %s", fired_class, e)
        return None


async def _exec_or_reject(
    tc: ToolCall,
    registry: dict[str, ToolFn],
    sem: asyncio.Semaphore,
) -> dict:
    """JSON 解析失败时短路 — 不执行工具, 直接返回诊断信号给 LLM."""
    if tc.parse_error:
        return {"status": "error", "error_type": "JSON_PARSE_FAILED", "message": tc.parse_error}
    return await _exec_tool(tc, registry, sem)


async def _exec_tool(
    tc: ToolCall,
    registry: dict[str, ToolFn],
    sem: asyncio.Semaphore,
) -> dict:
    """执行单个 tool, 业务错误包装为 status=error 喂回 LLM 自决重试.

    协议不变量: 返回值 output 字段始终是 dict.
      - success: tool 自然输出 (dict, 由 tool 实现自身保证)
      - error:   {"error_type": str, "error_message": str}

    CancelledError 必须 re-raise (不允许吞), 让上层 except 块清理.
    """
    async with sem:
        fn = registry.get(tc.name)
        if fn is None:
            return {
                "status": "error",
                "output": {
                    "error_type": "UnknownToolError",
                    "error_message": f"unknown tool: {tc.name}",
                },
            }
        try:
            out = await fn(**tc.input)
            return {"status": "ok", "output": out}
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("tool %s 抛错: %s", tc.name, e, exc_info=True)
            err: dict[str, Any] = {
                "error_type": type(e).__name__,
                "error_message": str(e),
            }
            # INV-ERRCODE: pymongo OperationFailure.code 等数值码必须保留, 供
            # normalize_error_class 规则 1 直接取 (不退化为消息正则). 见设计 A7.
            code = getattr(e, "code", None)
            if isinstance(code, (int, str)):
                err["error_code"] = code
            return {
                "status": "error",
                "output": err,
            }


def _hash_input(inp: dict) -> str:
    try:
        return json.dumps(inp, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(inp)


def _progress_marker(output: object) -> str | None:
    """返回可用于停滞检测的进展标记；None 表示本次工具调用未产生有效结果。"""
    if output is None:
        return None
    if isinstance(output, str):
        return f"str:{len(output)}" if output.strip() else None
    if isinstance(output, (list, tuple, set)):
        return f"seq:{len(output)}" if output else None
    if not isinstance(output, dict):
        return f"scalar:{type(output).__name__}"
    if output.get("error") or output.get("error_type") or output.get("error_message"):
        return None
    rows = output.get("rows")
    if isinstance(rows, list) and rows:
        return f"rows:{len(rows)}"
    for key in (
        "row_count", "total_row_count", "rendered_row_count",
        "final_total_row_count", "estimated_docs", "hit_count",
        "value_count", "entry_id", "history_id",
    ):
        value = output.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return f"{key}:{value}"
    meaningful = {
        k: v for k, v in output.items()
        if k != "result_ref" and v not in (None, "", [], {}, False)
    }
    return f"dict:{len(meaningful)}" if meaningful else None


def _loop_stop_signal(
    history: list[tuple[str, str, str | None]],
    window: int,
) -> tuple[str, str] | None:
    """返回 (stop_reason, message)，无终止信号则返回 None。"""
    if len(history) < window:
        return None
    last = history[-window:]
    if all((name, inp) == (last[0][0], last[0][1]) for name, inp, _ in last):
        return "dead_loop", f"死循环检测: 连续 {window} 次同 tool+同参"
    if all(marker is None for _, _, marker in last):
        return "stagnation", f"停滞检测: 连续 {window} 次工具调用无有效结果"
    return None


def _is_dead_loop(history: list[tuple[str, str]], window: int) -> bool:
    """兼容旧单测：最近 window 次 tool_call 全部 (name, input_hash) 相同。"""
    return _loop_stop_signal(
        [(name, inp, "progress") for name, inp in history],
        window,
    ) is not None


def _accumulate_usage(total: dict[str, int], delta: dict | None) -> None:
    if not delta:
        return
    for k in ("input_tokens", "output_tokens"):
        if k in delta:
            total[k] = total.get(k, 0) + int(delta[k])


def _summarize(value: object, limit: int = 2000) -> str:
    s = _stringify(value)
    return s if len(s) <= limit else s[:limit] + "..."


def _stringify(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


# ════════════════════════════════════════════
#  回喂 LLM 的 tool 结果字符预算 (上下文溢出护栏)
# ════════════════════════════════════════════
# 单一收口点: 所有 tool (mongo/mysql/plan/schema/knowledge/未来引擎) 的结果都经
# 此处回喂 LLM. 行数上限 (query_row_limit) 只约束 row count, 不约束 payload 体积 ——
# 如 $group 把 8406 条文本收进单行数组, row_count=1 却 >1M tokens, 直接撑爆上下文.
# 此护栏在回喂前按字符预算 dict-aware 收缩, 绝不修改入参 output (tool_trace 保持完整).

_BOUND_LIST_SAMPLE_HEAD = 5     # 超预算时 list 仅保留头部 N 个元素
_BOUND_STR_FIELD_MAX = 2000     # 超预算时单个超长 str 字段截断长度  # noqa: hardcode

_BOUND_TRUNC_NOTE = (
    "结果体积过大, 已截断回传给模型 (完整结果保留在服务端用于最终渲染). "
    "若需基于全量数据推理, 改用 mode=count 数行 / 聚合汇总 / mode=batched 分批, "
    "不要让查询把大量原始文档/文本整体拉回."
)


def _shrink_for_context(value: object) -> object:
    """递归收缩超大 list/str. 通用 — 不假设任何字段名, 故对所有 db 形态生效."""
    if isinstance(value, str):
        if len(value) > _BOUND_STR_FIELD_MAX:
            return value[:_BOUND_STR_FIELD_MAX] + f"...<+{len(value) - _BOUND_STR_FIELD_MAX} chars>"
        return value
    if isinstance(value, list):
        if len(value) > _BOUND_LIST_SAMPLE_HEAD:
            head = [_shrink_for_context(v) for v in value[:_BOUND_LIST_SAMPLE_HEAD]]
            return head + [{"_omitted_elements": len(value) - _BOUND_LIST_SAMPLE_HEAD}]
        return [_shrink_for_context(v) for v in value]
    if isinstance(value, dict):
        return {k: _shrink_for_context(v) for k, v in value.items()}
    return value


def bound_tool_content(output: object, *, budget_chars: int) -> str:
    """回喂 LLM 的 tool 结果字符串护栏. 绝不修改入参 output (tool_trace 安全).

    ≤预算 → 原样 _stringify 透传 (正常结果零回归);
    >预算 → dict-aware 收缩 (list 采样头部 + 省略计数, 超长 str 截断) + 结构化截断
    标记/引导语, 再加最终硬截兜底, 保证返回串恒 ≤ budget_chars.
    """
    s = _stringify(output)
    if len(s) <= budget_chars:
        return s

    if isinstance(output, dict):
        shrunk = {k: _shrink_for_context(v) for k, v in output.items()}
        shrunk["_context_truncated"] = True
        shrunk["_truncation_note"] = _BOUND_TRUNC_NOTE
        bounded = _stringify(shrunk)
    else:
        bounded = s

    if len(bounded) > budget_chars:
        bounded = bounded[:budget_chars] + '...<truncated>"}'
    return bounded
