"""
核心查询 API
POST /api/query        — 自然语言 → 数据结果
POST /api/query/stream — SSE 流式 agent loop (Stage 5)
POST /api/query/stream/{trace_id}/cancel — Stage 4 agent loop 中止
"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._clarify_extract_prompt import (
    CLARIFY_EXTRACT_PROMPT,
    VALID_CATEGORIES,
)
from app.api._session_cleanup import (
    write_cancelled_history,
)
from app.auth import assert_ns_access, get_current_user, require_admin_or_above
from app.config import settings
from app.db.metadata import async_session as _new_db_session
from app.db.metadata import get_db
from app.engine import history_loader
from app.engine.agent_loop import (
    AgentResult,
    cancel_agent,
    is_agent_running,
    run_agent_loop,
)
from app.engine.agent_loop_dispatcher import build_bound_registry
from app.engine.model_registry import registry
from app.engine.sse_manager import (
    deregister_sse_session,
    format_sse_event,
    get_correction_queue,
    get_event_queue,
    list_active_trace_ids,
    register_sse_session,
)
from app.engine.tools.registry import (
    CHART_TOOLS,
    EXEC_TOOLS,
    TOOL_SPECS,
    build_system_prompt,
)
from app.engine.visualizer import render_chart
from app.knowledge.trace_extractor import (
    extract_collections as _extract_collections_impl,
)
from app.knowledge.trace_extractor import (
    extract_join_keys as _extract_join_keys_impl,
)
from app.knowledge.trace_extractor import (
    normalize_query_plan as _normalize_query_plan_impl,
)
from app.models import (
    Namespace,
    QueryHistory,
)
from app.models.model_config import DEFAULT_MAX_HISTORY_TURNS
from app.models.user import User
from app.schemas import (
    ClarifyResponseRequest,
    CorrectionRequest,
    QueryStreamRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["query"])


# ── cancel 来源标志位 (P0-4 Task 7) ──
_cancel_reason: dict[str, str] = {}
"""trace_id → cancel 来源: 'user_abort' 由 cancel 端点写, 'external' 默认.
agent_loop except 块 pop 后写入日志, SSE event 不发 reason 字段。"""


async def _async_extract_after_end_turn(
    *, ns_id: int | None, ns_slug: str, question: str,
    result: AgentResult, trace_id: str,
) -> None:
    """agent end_turn 成功后台 LLM-as-extractor 抽取知识入待审池.

    职责拆分 (Stage extractor-protocol Task 2):
      - 代码侧抽: collections / rows_count / tool_count / trace_summary
        (机械字段, 不变性由代码保证)
      - LLM 侧吐: question_pattern + result_summary (语义改写, 仅此两字段)

    防御式: 任何内部异常 log.exception 完整字段 (不省略), 永不传播.
    namespace_id=None (全局) 不沉淀.
    """
    from app.api._async_extract_prompt import ASYNC_EXTRACT_PROMPT
    from app.engine.llm import chat_completion

    del ns_slug  # 保留参数兼容调用方, 新协议下不再使用

    raw_llm_output: str = ""
    llm_output: dict | None = {}
    try:
        if ns_id is None:
            return
        should, reason = _should_extract(result, settings)
        if not should:
            log.info("[async_extract] skip trace=%s reason=%s", trace_id, reason)
            return

        # ── 代码侧抽取 (机械字段, 一定对) ──
        collections    = _extract_collections(result.tool_trace or [])
        rows_count, _  = _extract_rows_chart(result)
        tool_count     = len(result.tool_trace or [])
        trace_summary  = _summarize_tool_trace(result.tool_trace, settings)

        if not collections:
            log.warning(
                "[async_extract] missing collections trace=%s", trace_id,
            )
            return

        # ── LLM 侧 (仅语义改写) ──
        prompt = ASYNC_EXTRACT_PROMPT.format(
            question=question,
            collections=json.dumps(collections, ensure_ascii=False),
            tool_trace_summary=trace_summary,
        )
        raw_llm_output = await asyncio.to_thread(
            chat_completion,
            messages=[{"role": "user", "content": prompt}],
            namespace_id=ns_id,
        )
        from app.engine.json_parser import parse_llm_json
        llm_output = parse_llm_json(raw_llm_output, expect="dict")
        if llm_output is None:
            log.warning(
                "[async_extract] JSON parse failed trace=%s raw=%s...",
                trace_id, (raw_llm_output or "")[:200],
            )
            return
        _validate_llm_output_minimal(llm_output)

        # ── 服务端拼装 (保真) ──
        question_pattern = llm_output["question_pattern"].strip()
        final_query_plan = _normalize_query_plan_impl(result.tool_trace or [])
        evidence = {"trace_id": trace_id, "tool_count": tool_count, "rows": rows_count}

        example = {
            "question_pattern": question_pattern,
            "collections":      collections,
            "join_keys":        _extract_join_keys(final_query_plan),
            "final_query_plan": final_query_plan,
            "result_summary":   llm_output.get("result_summary") or "",
        }
        async with _new_db_session() as db:
            await _write_extract_results(
                db=db, ns_id=ns_id,
                trace_id=trace_id,
                question_pattern=question_pattern,
                example=example, evidence=evidence,
            )
            await db.commit()

    except json.JSONDecodeError:
        log.exception(
            "[async_extract] JSON parse failed trace=%s\n"
            "  raw_llm_output=%r\n  question=%r",
            trace_id, raw_llm_output, question,
        )
    except Exception:
        log.exception(
            "[async_extract] failed trace=%s\n"
            "  raw_llm_output=%r\n  llm_output=%r\n  question=%r",
            trace_id, raw_llm_output, llm_output, question,
        )


@router.post("/query/stream/{trace_id}/cancel")
async def cancel_agent_loop(
    trace_id: str,
    user: User = Depends(get_current_user),
):
    """Stage 4 Task 10 — cancel running agent loop by trace_id.

    P0-4 Task 7: 写 _cancel_reason='user_abort' 标志位让 agent_loop except 区分来源,
    SSE event 仍发空 data, 区分仅服务后端可观测 (log).

    P1-19 A2: 通过公共函数 cancel_agent 发 cancel, 不直读 _active_agent_workers.

    task 仍在 _active_agent_workers 中: task.cancel() + 等待 grace_secs → 200.
    task 已结束 (run_agent_loop finally 已 pop): _cancel_reason 上文已设置作为闸门
    标志, _run_and_finalize() 检查后中止成功写入 → 仍返回 200.
    """
    # ── P0-4: 标记来源 user_abort, agent_loop except 块 pop 后日志区分 ──
    _cancel_reason[trace_id] = "user_abort"
    log.info("agent cancel requested (user_abort) trace=%s", trace_id)

    cancelled, task = cancel_agent(trace_id)
    if not cancelled:
        # 区分两种情况:
        # 1. SSE 会话仍存活 → _run_and_finalize() 还在运行 (盲区),
        #    _cancel_reason 作为闸门标志让检查点中止成功写入 → 200
        # 2. SSE 会话已注销 → trace_id 从未存在过 → 清理标志, 404
        if get_event_queue(trace_id) is not None:
            log.info(
                "agent cancel flag-only (task already finished, SSE alive) trace=%s",
                trace_id,
            )
            return {"cancelled": True, "trace_id": trace_id, "note": "flag_set"}
        _cancel_reason.pop(trace_id, None)
        raise HTTPException(404, f"trace_id {trace_id} 不存在或已结束")

    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=settings.agent_cancel_grace_secs)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    return {"cancelled": True, "trace_id": trace_id}


# ══════════════════════════════════════════════════════════════════════════════
#  Stage 5 — SSE 流式端点 + 用户反向通道
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/query/stream")
async def query_stream(
    body: QueryStreamRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE 流式 agent loop — text/event-stream, keepalive 每 IS_AGENT_KEEPALIVE_INTERVAL_SECS 秒."""
    from app.knowledge.knowledge_loader import batch_load_terminology, load_all_knowledge
    from app.knowledge.terminology_automaton import match_terminology

    ns = await db.get(Namespace, body.namespace_id)
    if not ns:
        raise HTTPException(404, "命名空间不存在")
    await assert_ns_access(db, user, ns.id)

    # trace_id: 每次执行新生成 (落 agent_traces, 唯一约束); 取消重发/重试都是新 trace。
    # session_id: 会话级稳定标识 (前端传则用), 用于多轮历史 / 按会话聚合。
    # 两者解耦 — 同一会话多次提交 → 不同 trace_id, 不再撞 agent_traces 唯一约束。
    trace_id = str(uuid.uuid4())
    session_id = body.session_id or ""  # P0: 不用 trace_id 回退，保证前端传来的 session UUID 可追溯
    event_q, correction_q = register_sse_session(trace_id)

    async def sse_emit(evt: dict) -> None:
        await event_q.put(evt)

    bound = build_bound_registry(
        db=db, namespace_id=ns.id, ns_slug=ns.slug,
        trace_id=trace_id,
        sse_emit=sse_emit,
    )
    # Phase 4 Task 4.5: 单一知识入口 → bundle 拆段注入 system_prompt
    bundle = await load_all_knowledge(db, ns.id, ns.slug, body.question)
    # AC 自动机精确匹配术语锚点
    term_ids = match_terminology(ns.slug, body.question)
    anchors = await batch_load_terminology(db, term_ids)
    system_prompt = build_system_prompt(
        settings=settings, namespace=ns,
        anchors=anchors,
        critical=bundle.critical,
    )

    # 多轮上下文: 读同 session 最近 N 轮历史注入 agent_loop messages。
    # max_turns 单一真相源 = 激活 CHAT 配置 model_configs.max_history_turns;
    # 缺失时回落 DEFAULT_MAX_HISTORY_TURNS。
    chat_cfg = registry.chat_config
    max_turns = DEFAULT_MAX_HISTORY_TURNS
    if chat_cfg and isinstance(chat_cfg.get("max_history_turns"), int):
        max_turns = chat_cfg["max_history_turns"]
    history_messages = await history_loader.build_history_messages(
        db, session_id, ns.id, max_turns,
    )

    async def _run_and_finalize() -> None:
        try:
            result = await run_agent_loop(
                trace_id=trace_id,
                question=body.question,
                tools_registry=bound,
                tool_specs=TOOL_SPECS,
                sse_emit=sse_emit,
                user_correction_queue=correction_q,
                system_prompt=system_prompt,
                db=db,
                namespace_id=ns.id,
                session_id=session_id,
                history_messages=history_messages,
            )
            # ── Cancel 盲区守卫 ──
            # _cancel_reason 可能在 run_agent_loop() 返回后 (已从 _active_agent_workers
            # 移除自身) 被 cancel 端点设置。pop-and-check 将迟到的 cancel 转为
            # CancelledError，让已有 except 块走 _write_cancelled_history。
            if _cancel_reason.pop(trace_id, None) == "user_abort":
                raise asyncio.CancelledError(
                    "user cancelled (flag detected after agent loop completed)"
                )
            # Phase 7: end_turn 异步抽取 (fire-and-forget, 不阻断主链路)
            asyncio.create_task(_async_extract_after_end_turn(
                ns_id=ns.id, ns_slug=ns.slug, question=body.question,
                result=result, trace_id=trace_id,
            ))
            history_id = await _write_query_history(
                namespace_id=ns.id,
                session_id=session_id,
                question=body.question,
                result=result,
                trace_id=trace_id,
            )
            final_data: dict = {
                "content": result.final_answer,
                "history_id": history_id,
                "stop_reason": result.stop_reason,
            }
            # Stage 3: present_result 显式 ref → 反查全量 rows + 确定性渲染.
            presented = _resolve_present_result(result.tool_trace)
            if presented:
                final_data["rows"] = presented["rows"]
                final_data["columns"] = presented["columns"]
                final_data["chart_type"] = presented["chart_type"]
                final_data["chart_option"] = presented["chart_option"]
                final_data["category_column"] = presented["category_column"]
                final_data["value_column"] = presented["value_column"]
                final_data["series_by"] = presented["series_by"]
                final_data["truncated"] = presented["truncated"]  # §4.6 显式透传
                final_data["rendered_row_count"] = presented["rendered_row_count"]
                final_data["total_row_count"] = presented["total_row_count"]
            else:
                # 无 present_result (LLM 未收尾): 退化取最后一次 execute_query 原始 rows, 表格展示.
                fallback = _extract_from_tool_trace(result.tool_trace, EXEC_TOOLS, ("rows",))
                if fallback.get("rows"):
                    final_data["rows"] = fallback["rows"]
                    first = fallback["rows"][0]
                    if isinstance(first, dict):
                        final_data["columns"] = list(first.keys())
                    final_data["chart_type"] = "table"
                final_data.setdefault("chart_option", {})
                final_data.setdefault("category_column", "")
                final_data.setdefault("value_column", "")
                final_data.setdefault("series_by", "")
                final_data.setdefault("truncated", False)
                final_data.setdefault("rendered_row_count", len(final_data.get("rows", [])))
                final_data.setdefault("total_row_count", len(final_data.get("rows", [])))
            await event_q.put({
                "event": "final_answer",
                "data": final_data,
            })
        except asyncio.CancelledError:
            # 被取消的对话也要保存，让刷新后仍可查看部分过程
            try:
                await write_cancelled_history(
                    trace_id=trace_id,
                    namespace_id=ns.id,
                    session_id=session_id,
                    question=body.question,
                )
            except Exception:
                log.exception("write_cancelled_history failed trace=%s", trace_id)
        except Exception as exc:
            log.exception("query_stream unexpected error trace_id=%s", trace_id)
            await event_q.put({
                "event": "error",
                "data": {"code": "internal_error", "message": str(exc), "recoverable": False},
            })
        finally:
            _cancel_reason.pop(trace_id, None)  # 兜底清理，防止泄漏
            await event_q.put(None)  # sentinel → generator 退出

    agent_task = asyncio.create_task(_run_and_finalize())

    async def event_generator():
        try:
            while True:
                try:
                    evt = await asyncio.wait_for(
                        event_q.get(),
                        timeout=float(settings.agent_keepalive_interval_secs),
                    )
                except asyncio.TimeoutError:
                    yield format_sse_event("keepalive")
                    continue
                if evt is None:
                    break
                yield format_sse_event(evt["event"], evt.get("data"))
        except GeneratorExit:
            agent_task.cancel()
        finally:
            deregister_sse_session(trace_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ════════════════════════════════════════════
#  P0-5: tool_trace 反扫 — final_answer + history 复用
# ════════════════════════════════════════════
def _extract_from_tool_trace(
    trace: list[dict],
    tool_names: tuple[str, ...],
    fields: tuple[str, ...],
) -> dict:
    """反扫 tool_trace 取最后一个 ok 的指定 tool 的指定字段.

    多次调用同名 tool 时取最后一个成功的 (与 LLM 最终展示给用户的应是
    最后一次成功查询匹配)。无命中返空 dict。
    """
    last = next(
        (tr for tr in reversed(trace)
         if tr["name"] in tool_names and tr["status"] == "ok"),
        None,
    )
    if not last:
        return {}
    return {f: last["output"][f] for f in fields if f in last["output"]}


def _resolve_present_result(trace: list[dict]) -> dict | None:
    """找最后一个成功 present_result → 按 ref 反查目标执行的完整 rows → render_chart.

    返回 {rows, columns, chart_type, chart_option, category_column, value_column,
          truncated, rendered_row_count, total_row_count} 或 None (无 present_result).
    ref 失效时渲染端 fail-safe (render_chart 对空 rows 返回 table).
    truncated/total_row_count 取自目标执行 output (render mode 疑似截断时由 executor
    补 count 填精确总数), 全程透传不静默 (§4.6).
    """
    pr = next(
        (tr for tr in reversed(trace)
         if tr.get("name") == "present_result" and tr.get("status") == "ok"),
        None,
    )
    if not pr:
        return None
    out = pr.get("output") or {}
    ref = out.get("ref")
    chart_spec = out.get("chart_spec") or {}
    # 按 ref 反查目标执行的完整 rows + truncated
    target = next(
        (tr for tr in trace if tr.get("id") == ref and tr.get("status") == "ok"), None,
    )
    rows: list[dict] = []
    truncated = False
    total_row_count = 0
    if target:
        target_out = target.get("output") or {}
        if not isinstance(target_out, dict):
            target_out = {}
        raw_rows = target_out.get("rows")
        # 畸形 output (rows 非 list) 退化为空, 不抛
        rows = list(raw_rows) if isinstance(raw_rows, list) else []
        truncated = bool(target_out.get("truncated"))
        total_row_count = int(target_out.get("total_row_count") or len(rows))
    chart_type, chart_option = render_chart(rows, chart_spec)
    columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
    return {
        "rows": rows,
        "columns": columns,
        "chart_type": chart_type,
        "chart_option": chart_option,
        "category_column": chart_spec.get("x", ""),  # 兼容旧前端字段
        "value_column": chart_spec.get("value", ""),  # 切换图表类型时前端复用 LLM 选定的度量列
        "series_by": chart_spec.get("series_by", ""),  # 切换图表类型时前端复用 LLM 选定的分组列
        "truncated": truncated,                       # §4.6 截断显式透传
        "rendered_row_count": len(rows),
        "total_row_count": total_row_count,           # 截断时为补 count 的精确总数
    }


async def _write_query_history(
    *,
    namespace_id: int,
    session_id: str,
    question: str,
    result: "AgentResult",
    trace_id: str = "",
) -> int:
    """写 QueryHistory。使用独立 session 避免与 request scoped db 生命周期冲突。

    trace_id 用于 commit 前最后一次取消检查 — 若用户在此期间点了取消,
    则 raise CancelledError 让上层 except 块写取消记录。
    """
    generated_query = ""
    row_count = 0
    for tr in reversed(result.tool_trace):
        if tr["name"] in EXEC_TOOLS and tr.get("status") == "ok":
            generated_query = json.dumps(tr["input"], ensure_ascii=False, default=str)
            out = tr.get("output") or {}
            row_count = int(out.get("row_count", 0) or out.get("count", 0) or 0)
            break

    # Stage 3: history 快照存渲染器产出的【完整 rows】+ chart_option (非 LLM 手抄).
    presented = _resolve_present_result(result.tool_trace)
    if presented:
        rows_data = presented["rows"]
        columns_data = presented["columns"]
        chart_type_data = presented["chart_type"]
        chart_option_data = presented["chart_option"]
        category_column_data = presented["category_column"]
        value_column_data = presented["value_column"]
        series_by_data = presented["series_by"]
        truncated_data = presented["truncated"]
        rendered_row_count_data = presented["rendered_row_count"]
        total_row_count_data = presented["total_row_count"]
    else:
        exec_data = _extract_from_tool_trace(result.tool_trace, EXEC_TOOLS, ("rows", "columns"))
        rows_data = exec_data.get("rows", [])
        columns_data = exec_data.get("columns", []) or (
            list(rows_data[0].keys()) if rows_data and isinstance(rows_data[0], dict) else []
        )
        chart_type_data = "table"
        chart_option_data = {}
        category_column_data = ""
        value_column_data = ""
        series_by_data = ""
        truncated_data = False
        rendered_row_count_data = len(rows_data)
        total_row_count_data = len(rows_data)

    async with _new_db_session() as db:
        entry = QueryHistory(
            namespace_id=namespace_id,
            session_id=session_id,
            role="assistant",
            content=question,
            generated_query=generated_query,
            row_count=row_count,
            error="" if result.stop_reason == "end_turn" else result.stop_reason,
            result_snapshot=json.dumps({
                "session_id": session_id,
                "history_id": 0,
                "needs_clarification": False,
                "clarification_message": "",
                "generated_query": generated_query,
                "columns": columns_data,
                "rows": rows_data,
                "row_count": row_count,
                "chart_type": chart_type_data,
                "category_column": category_column_data,
                "value_column": value_column_data,
                "series_by": series_by_data,
                "chart_option": chart_option_data,
                "truncated": truncated_data,
                "rendered_row_count": rendered_row_count_data,
                "total_row_count": total_row_count_data,
                "performance_warning": "",
                "error": "" if result.stop_reason == "end_turn" else result.stop_reason,
                "clarification_questions": [],
                "pending_id": 0,
                "final_answer": result.final_answer,
                "iterations": result.iterations,
                "stop_reason": result.stop_reason,
                # P0: 保存工具调用链, 前端重建历史时展示完整过程
                "tool_trace": result.tool_trace,
            }, ensure_ascii=False, default=str),
        )
        db.add(entry)
        # ── 提交前最后一次取消检查 (commit 之后无法回滚) ──
        if trace_id and _cancel_reason.pop(trace_id, None) == "user_abort":
            raise asyncio.CancelledError(
                "user cancelled (flag detected before history commit)"
            )
        await db.commit()
        await db.refresh(entry)
        return entry.id



@router.post("/query/stream/{trace_id}/correct")
async def submit_correction(
    trace_id: str,
    body: CorrectionRequest,
    user: User = Depends(get_current_user),
):
    """用户纠偏 — abort / redirect / param_override 推入 correction_queue."""
    q = get_correction_queue(trace_id)
    if q is None:
        raise HTTPException(404, f"trace_id {trace_id!r} 无活跃 SSE 会话")
    await q.put(body.model_dump())
    return {"ok": True, "trace_id": trace_id}


@router.post("/query/stream/{trace_id}/clarify_response")
async def submit_clarify_response(
    trace_id: str,
    body: ClarifyResponseRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """解除 clarify_with_user tool 的 asyncio.Event 阻塞 + emit clarify_resolved。"""
    from app.engine.tools.interaction_tools import resolve_pending_clarification
    from app.models import PendingClarification

    # 先拿 PendingClarification 对象 (resolve 前, 确保能读到 original_question)
    pc = await db.get(PendingClarification, body.pending_id)
    if not pc:
        raise HTTPException(404, f"pending {body.pending_id} 不存在")

    try:
        await resolve_pending_clarification(
            db=db, pending_id=body.pending_id, answer=str(body.answer),
        )
    except ValueError as e:
        raise HTTPException(404, str(e))

    # ── P0-3 emit clarify_resolved — 让前端关卡片 ──
    if (event_q := get_event_queue(trace_id)) is not None:
        await event_q.put({"event": "clarify_resolved", "data": {
            "pending_id": body.pending_id,
            "answer": str(body.answer),
        }})

    # ── Phase 7: fire-and-forget 启动后台抽取 (不阻断响应) ──
    clarify_ctx = json.loads(pc.clarification_questions_json or "[]")
    clarify_q = clarify_ctx[0]["question"] if clarify_ctx else ""
    clarify_options = clarify_ctx[0].get("options", []) if clarify_ctx else []
    clarify_reason = clarify_ctx[0].get("reason", "") if clarify_ctx else ""
    asyncio.create_task(_clarify_extract_hook(
        trace_id=trace_id,
        question=pc.original_question,
        clarify_q=clarify_q,
        clarify_options=clarify_options,
        clarify_reason=clarify_reason,
        user_answer=str(body.answer),
        ns_id=pc.namespace_id,
    ))

    return {"ok": True}


@router.get("/query/active_workers")
async def list_active_workers(user: User = Depends(require_admin_or_above)):
    """Admin 查看活跃 SSE trace (单进程 dict, 不跨 worker 共享)."""
    ids = list_active_trace_ids()
    return {"trace_ids": ids, "count": len(ids)}


@router.get("/query/stream/{trace_id}/status")
async def get_stream_status(trace_id: str, user: User = Depends(get_current_user)):
    return {
        "trace_id": trace_id,
        "agent_running": is_agent_running(trace_id),
        "sse_connected": get_event_queue(trace_id) is not None,
    }


# ════════════════════════════════════════════
#  Phase 7: clarify_response 后台抽取 hook
# ════════════════════════════════════════════


async def _clarify_extract_hook(
    *,
    trace_id: str,
    question: str,
    clarify_q: str,
    clarify_options: list[str],
    clarify_reason: str,
    user_answer: str,
    ns_id: int,
) -> None:
    """clarify_response 后台抽取知识 (fire-and-forget).

    自建 DB session (主请求 session 已关闭).
    LLM 抽取产 4 类输出, 各自分流写库.
    失败完整 log, 不省略 / 不写脏数据.
    """
    from app.engine.llm import chat_completion

    raw_llm_output: str = ""
    output: dict | None = {}
    try:
        prompt = CLARIFY_EXTRACT_PROMPT.format(
            question=question,
            clarify_question=clarify_q,
            clarify_options=clarify_options,
            clarify_reason=clarify_reason,
            user_answer=user_answer,
            trace_id=trace_id,
        )
        raw_llm_output = await asyncio.to_thread(
            chat_completion,
            messages=[{"role": "user", "content": prompt}],
            namespace_id=ns_id,
        )
        from app.engine.json_parser import parse_llm_json
        output = parse_llm_json(raw_llm_output, expect="dict")
        if output is None:
            log.error(
                "[clarify_extract] JSON parse failed trace=%s\n"
                "  raw_llm_output=%r\n"
                "  question=%r\n"
                "  clarify_q=%r\n"
                "  clarify_options=%r\n"
                "  clarify_reason=%r\n"
                "  user_answer=%r",
                trace_id, raw_llm_output, question, clarify_q,
                clarify_options, clarify_reason, user_answer,
            )
            return
        category = output.get("category")
        if category not in VALID_CATEGORIES:
            raise ValueError(f"category not in whitelist: {category!r}")

        async with _new_db_session() as db:
            await _dispatch_clarify_extract(
                db=db, ns_id=ns_id,
                trace_id=trace_id, output=output,
            )
            await db.commit()

    except Exception:
        log.exception(
            "[clarify_extract] failed trace=%s\n"
            "  raw_llm_output=%r\n"
            "  output=%r\n"
            "  question=%r\n"
            "  clarify_q=%r\n"
            "  clarify_options=%r\n"
            "  clarify_reason=%r\n"
            "  user_answer=%r",
            trace_id, raw_llm_output, output, question, clarify_q,
            clarify_options, clarify_reason, user_answer,
        )


async def _dispatch_clarify_extract(
    *, db: AsyncSession, ns_id: int,
    trace_id: str, output: dict,
) -> None:
    """4 类分流."""
    category = output["category"]
    if category == "skip":
        log.info(
            "[clarify_extract] skip trace=%s reasoning=%r",
            trace_id, output.get("reasoning"),
        )
        return

    if category == "instance_alias":
        await _write_instance_alias_proposed(
            db=db, ns_id=ns_id,
            trace_id=trace_id, output=output,
        )
        return

    if category == "terminology_synonym":
        await _propose_terminology_synonym_extension(
            db=db, ns_id=ns_id,
            trace_id=trace_id, output=output,
        )
        return

    if category == "rule":
        await _write_rule_proposed(
            db=db, ns_id=ns_id,
            trace_id=trace_id, output=output,
        )
        return


async def _write_instance_alias_proposed(
    *, db: AsyncSession, ns_id: int,
    trace_id: str, output: dict,
) -> None:
    """写 KE(type=instance_alias, status=proposed)."""
    from app.knowledge.instance_alias_intake import (
        InstanceAliasValidationError,
        validate_instance_alias_payload,
    )
    from app.models import KnowledgeEntry

    try:
        validated = validate_instance_alias_payload(output["payload"])
    except InstanceAliasValidationError as e:
        log.warning(
            "[clarify_extract] instance_alias rejected trace=%s reason=%r payload=%r",
            trace_id, str(e), output.get("payload"),
        )
        return

    ke = KnowledgeEntry(
        namespace_id=ns_id, entry_type="instance_alias",
        content=output["content"], tier="normal", status="proposed",
        source=settings.agent_learn_source,
        payload=json.dumps(validated, ensure_ascii=False),
        evidence_json=json.dumps(output.get("evidence", {}), ensure_ascii=False),
    )
    db.add(ke)
    await db.flush()
    log.info(
        "[clarify_extract] instance_alias proposed trace=%s id=%s alias=%r",
        trace_id, ke.id, validated["alias"],
    )


async def _propose_terminology_synonym_extension(
    *, db: AsyncSession, ns_id: int,
    trace_id: str, output: dict,
) -> None:
    """同义词扩展 — 找 canonical terminology, 写 proposed 版本."""
    from app.models import KnowledgeEntry

    target_term = (output.get("payload") or {}).get("target_term", "").strip()
    new_synonyms = (output.get("payload") or {}).get("new_synonyms") or []
    if not target_term or not new_synonyms:
        log.warning(
            "[clarify_extract] terminology_synonym malformed payload trace=%s payload=%r",
            trace_id, output.get("payload"),
        )
        return

    stmt = select(KnowledgeEntry).where(
        KnowledgeEntry.namespace_id == ns_id,
        KnowledgeEntry.entry_type == "terminology",
        KnowledgeEntry.status == "canonical",
        KnowledgeEntry.is_superseded.is_(False),
    )
    candidates = (await db.execute(stmt)).scalars().all()
    target_ke = next(
        (
            k for k in candidates
            if json.loads(k.payload or "{}").get("term") == target_term
        ),
        None,
    )
    if target_ke is None:
        log.warning(
            "[clarify_extract] terminology_synonym target_term not found trace=%s "
            "ns_id=%s target_term=%r",
            trace_id, ns_id, target_term,
        )
        return

    proposal_payload = json.loads(target_ke.payload or "{}")
    existing_syns = list(proposal_payload.get("synonyms") or [])
    proposal_payload["synonyms"] = list(dict.fromkeys(existing_syns + new_synonyms))

    proposed_ke = KnowledgeEntry(
        namespace_id=ns_id, entry_type="terminology",
        content=target_ke.content, tier=target_ke.tier,
        status="proposed", source=settings.agent_learn_source,
        payload=json.dumps(proposal_payload, ensure_ascii=False),
        evidence_json=json.dumps({
            **(output.get("evidence") or {}),
            "synonym_extension_of": target_ke.id,
            "added_synonyms": new_synonyms,
        }, ensure_ascii=False),
    )
    db.add(proposed_ke)
    await db.flush()
    log.info(
        "[clarify_extract] terminology_synonym proposed trace=%s "
        "extension_of=%s added=%r",
        trace_id, target_ke.id, new_synonyms,
    )


async def _write_rule_proposed(
    *, db: AsyncSession, ns_id: int,
    trace_id: str, output: dict,
) -> None:
    """写 KE(type=rule, status=proposed)."""
    from app.models import KnowledgeEntry

    content = (output.get("content") or "").strip()
    if not content:
        log.warning(
            "[clarify_extract] rule empty content trace=%s output=%r",
            trace_id, output,
        )
        return

    ke = KnowledgeEntry(
        namespace_id=ns_id, entry_type="rule",
        content=content, tier="normal", status="proposed",
        source=settings.agent_learn_source,
        payload=json.dumps({}, ensure_ascii=False),
        evidence_json=json.dumps(output.get("evidence", {}), ensure_ascii=False),
    )
    db.add(ke)
    await db.flush()
    log.info(
        "[clarify_extract] rule proposed trace=%s id=%s",
        trace_id, ke.id,
    )


# ════════════════════════════════════════════
#  Phase 7: end_turn 后台 LLM-as-extractor 辅助函数
# ════════════════════════════════════════════


def _should_extract(result: AgentResult, settings_obj) -> tuple[bool, str]:
    """触发条件: end_turn AND tool_count ≥ min AND 真出 rows."""
    stop_reason = getattr(result, "stop_reason", None)
    if stop_reason != "end_turn":
        return False, f"stop_reason={stop_reason!r}"

    tool_count = len(result.tool_trace or [])
    if tool_count < settings_obj.knowledge_extract_min_tool_calls:
        return False, (
            f"tool_count={tool_count} < min="
            f"{settings_obj.knowledge_extract_min_tool_calls}"
        )

    has_rows = False
    for call in result.tool_trace or []:
        if call.get("name") not in EXEC_TOOLS:
            continue
        out = call.get("output") or {}
        rows = out.get("rows") or []
        count = out.get("count") or 0
        if (isinstance(rows, list) and len(rows) > 0) or count > 0:
            has_rows = True
            break
    if not has_rows:
        return False, "no execution rows produced"

    return True, "ok"


def _summarize_tool_trace(tool_trace: list[dict], settings_obj) -> str:
    """tool_trace → 简短文本摘要 (防 prompt token 爆炸)."""
    lines: list[str] = []
    max_chars = settings_obj.knowledge_extract_per_call_max_chars
    for i, call in enumerate(tool_trace or [], 1):
        name = call.get("name", "?")
        inp = call.get("input") or {}
        out = call.get("output") or {}
        key_in = {
            k: v for k, v in inp.items() if k in (
                "collection", "field", "filter", "pattern", "fields",
                "batch_field", "pipeline_stages",
            )
        }
        if "batch_ids" in inp:
            key_in["batch_ids"] = f"<{len(inp['batch_ids'])} ids>"
        in_str = json.dumps(key_in, ensure_ascii=False, default=str)[:max_chars]
        out_str = json.dumps(out, ensure_ascii=False, default=str)[:max_chars]
        lines.append(f"  [{i}] {name}: in={in_str} out={out_str}")
    return "\n".join(lines)


def _extract_rows_chart(result: AgentResult) -> tuple[int, str]:
    """从 tool_trace 拿 rows 数量 + chart_type."""
    rows_count = 0
    chart_type = "table"
    for call in result.tool_trace or []:
        name = call.get("name")
        out = call.get("output") or {}
        if name in EXEC_TOOLS:
            rows = out.get("rows") or []
            if isinstance(rows, list):
                rows_count = max(rows_count, len(rows))
            count = out.get("count") or 0
            if count and rows_count == 0:
                rows_count = count
        if name in CHART_TOOLS:
            # present_result: chart_type 埋在 chart_spec; 兜底读顶层 chart_type
            spec = out.get("chart_spec")
            ct = spec.get("chart_type") if isinstance(spec, dict) else None
            ct = ct or out.get("chart_type")
            if ct:
                chart_type = ct
    return rows_count, chart_type


def _extract_collections(tool_trace: list[dict]) -> list[str]:
    """从 tool_trace 抽涉及集合 (保序去重).

    覆盖 stage 3 4 件套数据访问工具的 input.target.
    通过 TOOL_TARGET_FIELD 集中查表, 工具改名只改 registry.py.
    """
    return _extract_collections_impl(tool_trace)


def _extract_join_keys(final_query_plan: dict | None) -> list[dict]:
    """从归一化 query_plan 中抽取 join 键对.

    返回 [{from: "上游集合.字段", to: "下游集合.字段"}, ...].
    """
    return _extract_join_keys_impl(final_query_plan)


def _validate_llm_output_minimal(out: dict) -> None:
    """LLM 只返两字段, 校验同步缩减."""
    if not isinstance(out, dict):
        raise ValueError(f"output 不是 dict: {type(out).__name__}")
    qp = out.get("question_pattern")
    if not isinstance(qp, str) or not qp.strip():
        raise ValueError(f"question_pattern 缺失或非字符串: {qp!r}")


async def _write_extract_results(
    *, db: AsyncSession, ns_id: int,
    trace_id: str,
    question_pattern: str,
    example: dict,
    evidence: dict,
) -> None:
    """example 必产."""
    from app.models import KnowledgeEntry

    example_ke = KnowledgeEntry(
        namespace_id=ns_id, entry_type="example",
        content=question_pattern, tier="normal", status="proposed",
        source=settings.agent_learn_source,
        payload=json.dumps(example, ensure_ascii=False),
        evidence_json=json.dumps(evidence, ensure_ascii=False),
    )
    db.add(example_ke)
    await db.flush()
    log.info(
        "[async_extract] example proposed trace=%s id=%s pattern=%r",
        trace_id, example_ke.id, question_pattern[:80],
    )
