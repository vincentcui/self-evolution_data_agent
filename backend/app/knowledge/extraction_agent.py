"""Agentic repo schema extraction — agent run loop.

Uses existing agent_loop.py message protocol + chat_completion_with_tools from engine/llm.py.
No new module reinvention — only prompt + tool specs + emit handler are extraction-specific.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from string import Template
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from app.knowledge.skeleton._base import WorkUnit

from app.config import settings
from app.engine.llm import build_assistant_message, chat_completion_with_tools
from app.knowledge.extraction_emit import validate_emit
from app.knowledge.extraction_prompts import load_prompt_or_fallback
from app.knowledge.extraction_tools import (
    EXTRACTION_TOOL_SPECS,
    find_files,
    grep,
    list_dir,
    read_file,
)

logger = logging.getLogger(__name__)

# ── Base Prompt ───────────────────────────────────────────
# Source: backend/prompts/extraction-agent-base.md → loaded via prompt_loader.
# DO NOT modify prompt content without spec review + pa audit + file update.


def build_extraction_system_prompt(hint_text: str | None = None) -> str:
    """组装 system prompt: base (从 prompts/ 经 prompt_loader 加载) + 可选 profile hint."""
    body = load_prompt_or_fallback("extraction-agent-base")
    prompt = Template(body).safe_substitute(max_depth=str(settings.agentic_extract_max_depth))
    if hint_text and hint_text.strip():
        prompt += f"\n\n[Hint]\n{hint_text.strip()}"
    return prompt.strip()


@dataclass
class ExtractionResult:
    objects: list[dict] = field(default_factory=list)
    knowledge_proposals: list[dict] = field(default_factory=list)
    status: str = "ok"       # ok | partial | failed
    reason: str = ""


# ── emit_knowledge payload schema ──
# 每类 entry_type 的最小 payload 字段定义.
_KNOWLEDGE_PAYLOAD_SCHEMA: dict[str, dict] = {
    "route_hint":  {"required": ["mapper_namespace", "canonical_sql"]},
    "terminology": {"required": ["term"]},
    "rule":        {"required": ["rule_text"]},
    "example":     {"required": ["sql_pattern", "tables"]},
}


def _serialize_tool_result(result: dict) -> str:
    """序列化工具结果给 LLM — 结构化 JSON 字符串，截断超长输出防 token 爆炸."""
    raw = json.dumps(result, ensure_ascii=False, default=str)
    max_chars = settings.agent_tool_result_max_chars
    if len(raw) <= max_chars:
        return raw
    return (raw[:max_chars]
            + f"\n\n[输出截断: {len(raw)} 字符 → {max_chars}, "
            + "请用 offset/limit 缩小 read_file / 更精确的 grep 模式 / 更窄的 find_files glob]")


def _format_skeleton_context(wu: "WorkUnit") -> str:
    # ── 字段优先级: Rev 2 字段任一非空 → Rev 2 布局; 全空 → 向后兼容 Rev 1 布局 ──
    use_rev2 = bool(wu.focus_files or wu.focus_classes or wu.skeleton_class_index)

    if use_rev2:
        return _format_skeleton_context_rev2(wu)
    return _format_skeleton_context_rev1(wu)


def _format_skeleton_context_rev2(wu: "WorkUnit") -> str:
    """P3 verbatim — Rev 2 free-exploration layout (PA4-approved, character-locked)."""
    lines = [""]

    # ── Focus Entities section ──
    lines.append("## Focus Entities (suggested by pre-scan)")
    lines.append("Start with these files — they likely define data persistence entities:")
    for file_path in sorted(wu.focus_files):
        file_name = os.path.basename(file_path)
        lines.append(f"- {file_name} → {file_path}")

    lines.append("")

    # ── Repository Index section ──
    lines.append("## Repository Index (navigation aid)")
    lines.append("Class→file index from tree-sitter pre-scan. Use for fast lookup:")
    for class_name, file_path in sorted(wu.skeleton_class_index.items()):
        lines.append(f"- {class_name} → {file_path}")

    lines.append("")

    # ── Guidance section ──
    lines.append("## Guidance")
    lines.append("The Focus Entities list is a suggested starting point. Explore freely:")
    lines.append(
        "- Read any file in the repository relevant to data persistence extraction"
        " — including XML mappers, SQL scripts, ORM configs, and properties files in any directory."
    )
    lines.append("- If you discover entities not in the Focus list, extract them too.")
    lines.append(
        "- Use the Repository Index for fast file lookup when you know the class name."
    )

    return "\n".join(lines)


def _format_skeleton_context_rev1(wu: "WorkUnit") -> str:
    """Rev 1 向后兼容布局 — 仅在 Rev 2 三字段全空时命中 (单 agent 降级路径)."""
    lines = ["", "## Your Assignment", f"Module: {wu.name}",
             f"Source directory: {wu.scope_dir}",
             "", "## Entities to investigate (discovered by pre-scan)"]
    for cls_name, file_path in sorted(wu.class_index_subset.items()):
        lines.append(f"- {cls_name} → {file_path}")
    cross = {k: v for k, v in wu.full_class_index.items() if k not in wu.class_index_subset}
    if cross:
        lines.append("")
        lines.append("## Cross-Module Reference Index")
        lines.append("If you encounter types from other modules, read them at:")
        for cls_name, file_path in sorted(cross.items()):
            lines.append(f"- {cls_name} → {file_path}")
    lines.extend(["", "## Guidance",
        "You are assigned only the entities above. Read their source files, "
        "expand fields, extract enums, mark relations. If your assigned entities "
        "reference types not listed above, read those referenced files too — "
        "but do not explore unrelated modules outside your assignment."])
    return "\n".join(lines)


async def run_extraction_agent(
    *,
    repo_path: str,
    skeleton: "WorkUnit | None" = None,
    hint_text: str | None = None,
    max_iterations: int | None = None,
    repo_name: str = "",
) -> ExtractionResult:
    """运行一次 agentic schema 提取。

    Args:
        repo_path: clone 后的仓库本地路径
        skeleton: 可选 WorkUnit 注入骨架上下文 (分治模式下指定子任务范围)
        hint_text: 可选 profile hint, 注入 system prompt
        max_iterations: loop 轮数上限, 默认取 IS_AGENTIC_EXTRACT_MAX_ITERATIONS
        repo_name: 仓库名, 日志分组用 (如 question-center-service)
    """
    if max_iterations is None:
        max_iterations = settings.agentic_extract_max_iterations

    system_prompt = build_extraction_system_prompt(hint_text)
    if skeleton is not None:
        system_prompt += _format_skeleton_context(skeleton)

    if skeleton is not None:
        user_msg = (
            f"分析仓库 {repo_path} 中的数据持久化定义。上面的 [Focus Entities]\n"
            "是建议起点 — 可以自由探索仓库内任何相关文件，包括各目录下的\n"
            "XML mapper、SQL 脚本、ORM 配置。读源码→展开字段→提取枚举→\n"
            "标记关联→emit。用 [Repository Index] 快速定位已知类名。"
        )
    else:
        user_msg = (
            f"分析仓库 {repo_path} 中的所有数据持久化定义。"
            "按探索原则自主发现实体、递归展开字段、提取枚举、标记关联关系。"
            "完成后调用 emit_schema_object 提交每个对象。"
        )
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    emitted: list[dict] = []
    knowledge_proposals: list[dict] = []  # sql2nl/example/route_hint/terminology/rule

    def _make_emit_handler(buf: list[dict]) -> Callable:
        def _handler(**data: Any) -> dict:
            result = validate_emit(data)
            if result.status == "ok":
                buf.append(data)
                return {"status": "ok", "message": f"已提交: {data.get('name', '?')}"}
            return result.__dict__
        return _handler

    def _emit_knowledge_handler(**data: Any) -> dict:
        entry_type = data.get("entry_type", "")
        schema = _KNOWLEDGE_PAYLOAD_SCHEMA.get(entry_type)
        if schema is None:
            return {"status": "error", "message": f"无效 entry_type: {entry_type}"}
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            return {"status": "error", "message": "payload 必须是 dict"}
        missing = [k for k in schema["required"] if k not in payload or not payload[k]]
        if missing:
            return {"status": "error", "message": f"payload 缺少必填字段: {missing}"}
        knowledge_proposals.append({"entry_type": entry_type, "payload": payload})
        return {"status": "ok", "message": f"已提交 knowledge: {entry_type}"}

    tool_fns: dict[str, Callable] = {
        "list_dir": lambda **kw: list_dir(kw.get("path", "."), repo_path),
        "read_file": lambda **kw: read_file(
            kw["path"], repo_path, kw.get("offset"), kw.get("limit")),
        "grep": lambda **kw: grep(
            kw["pattern"], kw.get("path", "."), repo_path, kw.get("recursive", True)),
        # root server-injected, LLM 只传 glob
        "find_files": lambda **kw: find_files(kw["glob"], repo_path),
        "emit_schema_object": _make_emit_handler(emitted),
        "emit_knowledge": _emit_knowledge_handler,
    }

    iteration = 0
    llm_failed = False
    loop_start = time.time()
    dead_loop_window = settings.agentic_extract_dead_loop_window
    # (tool_name, input_hash, error_type_or_ok) — 成功="ok", 错误=error_type
    recent_tool_calls: list[tuple[str, str, str]] = []

    while iteration < max_iterations:
        iteration += 1
        try:
            response = await chat_completion_with_tools(
                messages=messages,
                tools=EXTRACTION_TOOL_SPECS,
                thinking=False,
            )
        except Exception:
            logger.exception("chat_completion_with_tools 调用异常 (iteration=%d)", iteration)
            llm_failed = True  # LLM 调用失败 — 保留已 emit 对象, 下游识别 failed 状态
            break

        if not response.tool_calls:
            logger.info(
                "[%s] agent loop 自然终止  iteration=%-3d  objects=%-3d  "
                "knowledge=%-2d  elapsed=%.1fs",
                repo_name, iteration, len(emitted), len(knowledge_proposals),
                time.time() - loop_start,
            )
            break  # LLM 无更多工具调用 → 自然终止

        # 执行工具 + 收集结果 (先执行, 后拼消息)
        tool_results = []
        dead_loop_hit = False
        for tc in response.tool_calls:
            fn = tool_fns.get(tc.name)
            if tc.parse_error:
                # JSON 解析失败 → 透传原始诊断信号给 LLM, 让其自行判断并调整
                result = {"status": "error", "error_type": "JSON_PARSE_FAILED",
                          "message": tc.parse_error}
            elif fn is None:
                result = {"status": "error", "error_type": "UNKNOWN_TOOL",
                          "message": f"未知工具: {tc.name}"}
            else:
                try:
                    result = fn(**tc.input)
                except Exception as e:
                    result = {"status": "error", "error_type": type(e).__name__, "message": str(e)}

            # ── dead_loop 检测 (内联重实现, 3-tuple, 非 import — I1) ──
            tc_sig = (
                tc.name,
                json.dumps(tc.input, sort_keys=True, default=str),
                result.get("error_type", "") if result.get("status") == "error" else "ok",
            )
            recent_tool_calls.append(tc_sig)
            if len(recent_tool_calls) > dead_loop_window:
                recent_tool_calls.pop(0)
            if len(recent_tool_calls) >= dead_loop_window and \
               all(item == recent_tool_calls[-1] for item in recent_tool_calls[-dead_loop_window:]):
                dead_loop_hit = True
                tool_results.append((tc, result))
                break

            tool_results.append((tc, result))

        # 逐轮进度日志 — 工具执行后, objects/knowledge 反映本轮真效果
        tool_names = [tc.name for tc in response.tool_calls]
        logger.info(
            "[%s] agent loop iteration=%-3d  tools=%-40s  objects=%-3d  "
            "knowledge=%-2d  elapsed=%.1fs",
            repo_name, iteration, str(tool_names), len(emitted), len(knowledge_proposals),
            time.time() - loop_start,
        )

        # ── 单条 assistant message 包含全部已处理的 tool_calls (对齐 agent_loop.py:367-375) ──
        processed_tcs = [tc for tc, _ in tool_results]
        messages.append(build_assistant_message(response, tool_calls=processed_tcs))
        for tc, result in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _serialize_tool_result(result),
            })

        if dead_loop_hit:
            return ExtractionResult(
                objects=emitted,
                knowledge_proposals=knowledge_proposals,
                status="partial",
                reason="dead_loop",
            )

    # ── 最终状态判定 — 优先级: LLM 异常 > iteration cap > 自然终止 ──
    elapsed = time.time() - loop_start
    if llm_failed:
        logger.warning(
            "[%s] agent loop 异常退出  iterations=%d  objects=%d  "
            "knowledge=%d  elapsed=%.1fs  reason=llm_call_failed",
            repo_name, iteration, len(emitted), len(knowledge_proposals), elapsed,
        )
        return ExtractionResult(
            objects=emitted,
            knowledge_proposals=knowledge_proposals,
            status="failed",
            reason="llm_call_failed",
        )
    if iteration >= max_iterations:
        logger.warning(
            "[%s] agent loop 达迭代上限  iterations=%d/%d  objects=%d  knowledge=%d  elapsed=%.1fs",
            repo_name, iteration, max_iterations, len(emitted), len(knowledge_proposals), elapsed,
        )
    else:
        logger.info(
            "[%s] agent loop 完成  iterations=%d  objects=%d  knowledge=%d  elapsed=%.1fs",
            repo_name, iteration, len(emitted), len(knowledge_proposals), elapsed,
        )
    return ExtractionResult(
        objects=emitted,
        knowledge_proposals=knowledge_proposals,
        status="partial" if iteration >= max_iterations else "ok",
        reason="iteration_cap" if iteration >= max_iterations else "",
    )
