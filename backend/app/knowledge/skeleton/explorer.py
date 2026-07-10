"""Explorer V3 — tool-assisted bounded loop

Rev 2 V3 升级:
  V2 (old): single chat_completion (没有工具, 仅靠注入 skeleton index 推理)
  V3 (new): chat_completion_with_tools 有界循环 (<=8 轮), 4 只读工具

NEUTRAL 消息格式 (同 extraction_agent.py:292-306):
  assistant → {"role":"assistant","content":str,"tool_calls":[{"id","name","input"}]}
  tool      → {"role":"tool","tool_call_id":str,"content":str(JSON)}

_to_openai_messages / _claude_tool_use 适配器处理协议转换。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from langfuse import observe

from app.engine.json_parser import parse_llm_json
from app.engine.llm import ToolUseResponse, build_assistant_message, chat_completion_with_tools
from app.knowledge.extraction_tools import (
    EXTRACTION_TOOL_SPECS,
    find_files,
    grep,
    list_dir,
    read_file,
)
from app.knowledge.skeleton._base import ExplorerResult, Skeleton

logger = logging.getLogger(__name__)

# ── Explorer 专用工具子集 (4 只读, 无 emit) ──────────────────────────────────
_EXPLORER_TOOL_NAMES = frozenset({"list_dir", "read_file", "grep", "find_files"})
EXPLORER_TOOL_SPECS = [s for s in EXTRACTION_TOOL_SPECS
                       if s["name"] in _EXPLORER_TOOL_NAMES]

# ── 循环控制 ─────────────────────────────────────────────────────────────────
# 无硬迭代上限 — dead_loop 检测是唯一终止条件.
# Explorer 上下文增长 ~1.5K/轮 (list_dir/grep 结果截断), 不存在膨胀问题.
DEAD_LOOP_WINDOW = 4
_EXPLORER_TOOL_RESULT_MAX_CHARS = 8192  # noqa: hardcode

# ── P1 V3: Explorer System Prompt ───────────────────────────────────────────
# PA4 §5.3 审批版本 (2026-06-21) — 禁止改写任何字符
_P1_V3_TEMPLATE = """\
You are a Repository Explorer. Your job is to discover files that define
data persistence entities in a codebase — but NOT to extract their details.

## Tools

You have four read-only tools to explore the repository:

- list_dir(path): List directory contents. Start at "." (repo root) to understand
  the project layout. Returns dirs/ and files/ in the given path.

- read_file(path, offset?, limit?): Read a file's content with optional line range.
  Use to inspect candidate entity files for persistence markers (annotations,
  inheritance, ORM declarations).

- grep(pattern, path): Search file contents with a regex pattern (case-insensitive).
  Use to find annotation/declaration patterns across the codebase:
  @Entity, @Table, @Document, extends Model, struct, #[derive], @Model.

- find_files(glob): Find files by name or path pattern. Use to locate persistence
  files: *.java, *Mapper.xml, models.py, *.swift, *.scala, migrations/*.sql,
  or any extension the project uses.

These are your ONLY tools. You do NOT have tools to extract field/type/enum
information — your sole task is to discover which files to extract.

## Repository Index (optional navigation aid)

<repository_index>
{class_index_entries}
</repository_index>

If the index above is non-empty: it maps class names to file paths from a
tree-sitter AST pre-scan. Use it as a fast lookup shortcut when you know a
class name — it saves a grep or find_files call.

CRITICAL: The index is a navigation shortcut, NOT a guaranteed entity list.
- Do NOT assume every class in it is a data entity (many are services/controllers).
- Do NOT assume every data entity is in it (XML mappers, SQL scripts, interfaces,
  and classes in languages without an installed grammar are never indexed).

When the index is empty: the project uses languages without a pre-installed
tree-sitter grammar. This is normal — use find_files and grep to discover
entity files autonomously. The process is the same, just takes a few more rounds.

## Exploration Strategy

Actively explore the repository. Do not rely on the index alone.

1. **Orient:** list_dir("") to see the top-level project structure. Identify
   build files (pom.xml, build.gradle, package.json, Cargo.toml, etc.) to infer
   the primary languages and frameworks.

2. **Locate entity directories:** look for path segments that conventionally
   contain persistence definitions — entity/ model/ domain/ models/ entities/
   dao/ repository/ app/models/ Data/ Sources/.

3. **Find candidate files:** use find_files with language-appropriate globs:
   - JVM: *.java, *.kt, *Mapper.xml, *.hbm.xml
   - Python: models.py, **/models/*.py, **/model/*.py
   - Go: *model*.go, *types*.go, **/entity/*.go
   - C/C++: *.h, *.cpp (look for struct/class with ORM macros)
   - Swift: *.swift (particularly Models/ directories, @Model classes)
   - TypeScript/JS: *.entity.ts, *.model.ts, **/entities/*.ts
   - C#/.NET: *.cs (Entity Framework DbContext, entity classes)
   - Rust: *.rs (diesel/sea-orm model modules)
   - Ruby: *.rb (app/models/ directory)
   - PHP: *.php (Doctrine entities, Eloquent models)
   - Unknown: use list_dir to walk directories, read_file to sample files,
     grep for common persistence patterns across all extensions.

4. **Verify:** read_file on a sample of candidate files to confirm they are
   data persistence entities (not services/controllers). Look for persistence
   declarations — @Entity/@Table/@Document annotations, ORM base class
   inheritance, struct tags (db/sql/gorm), migration/DDL content.

5. **Expand:** once you find one entity directory, explore sibling and parent
   directories for related persistence files (XML mappers, SQL migrations,
   ORM configuration, schema files). Include ALL files needed for extraction.

## When to Stop

You have a limited exploration budget. Stop and output JSON when:
- You have identified the primary entity directories and verified a few samples.
- You have searched for additional persistence file types (mappers, migrations).
- You are confident the file list covers the repository's data model.

Do NOT exhaustively read every file. A well-sampled directory scan is sufficient.
If after several rounds you still cannot identify entities, output empty arrays.

## Exclude

Service/Controller/Handler/DTO/VO/Request/Response/Util/Helper/Config/
Configuration/Factory/Builder/Strategy/Filter/Interceptor/Listener/Watcher
classes or files. Test files (*Test*.java, *_test.py, *.spec.ts), generated
code, build artifacts, node_modules, vendor directories.

## Output

When finished exploring, return STRICT JSON (no markdown fence, no prose
outside JSON):

{
  "focus_files": ["relative/path/Entity.java", "relative/path/Mapper.xml", ...],
  "focus_classes": ["EntityName", ...],
  "reasoning": "Brief description of your exploration and identification logic"
}

Constraints:
- focus_files: relative paths from repo root. List EVERY file that needs
  extraction — entity classes, XML mappers, SQL migrations, ORM configs,
  schema files, and supporting persistence artifacts.
- focus_classes: class/struct/enum names that define data entities.
- reasoning: 2-4 sentences summarizing exploration path and logic.
- If uncertain about a file, include it with a note in reasoning.
- If after exploration you cannot identify any persistence entities, return
  empty arrays and explain why. Do not fabricate.\
"""

# ── P2 V3: Explorer User Message ─────────────────────────────────────────────
# PA4 §5.3 审批版本 (2026-06-21) — 禁止改写任何字符
_P2_V3_TEMPLATE = """\
Explore the repository at {repo_path}. Use your tools to understand the
project structure, identify all data persistence entity files and classes,
then output the JSON result.\
"""


# ═════════════════════════════════════════════════════════════════════════════
# 内部辅助
# ═════════════════════════════════════════════════════════════════════════════

def _build_explorer_system_prompt(skeleton: Skeleton) -> str:
    """构建 V3 system prompt: 注入 skeleton class_index + 语言无关探索策略."""
    if skeleton.class_index:
        entries = "\n".join(
            f"{cls} → {path}"
            for cls, path in sorted(skeleton.class_index.items())
        )
    else:
        entries = (
            "(no pre-scan navigation data available — use find_files / "
            "grep / list_dir to discover entity files)"
        )
    return _P1_V3_TEMPLATE.replace("{class_index_entries}", entries)


def _is_dead_loop(recent_tool_calls: list[tuple[str, str]]) -> bool:
    """4 轮内同 tool+input 重复 → 死循环."""
    if len(recent_tool_calls) < DEAD_LOOP_WINDOW:
        return False
    return all(item == recent_tool_calls[-1]
               for item in recent_tool_calls[-DEAD_LOOP_WINDOW:])


def _serialize_tool_result(result: dict) -> str:
    """序列化工具结果为 JSON 字符串, 截断超长输出."""
    raw = json.dumps(result, ensure_ascii=False, default=str)
    if len(raw) <= _EXPLORER_TOOL_RESULT_MAX_CHARS:
        return raw
    return (raw[:_EXPLORER_TOOL_RESULT_MAX_CHARS]
            + "\n\n[结果截断: 用更精确的路径/模式缩小范围]")


def _compute_status(focus_files: list[str],
                    focus_classes: list[str],
                    reasoning: str) -> str:
    """全三项非空 → ok; 任意为空 → partial."""
    if focus_files and focus_classes and reasoning:
        return "ok"
    return "partial"


def _parse_explorer_output(raw: str, repo_name: str) -> ExplorerResult:
    """解析 LLM 输出的 JSON → ExplorerResult."""
    parsed = parse_llm_json(raw, expect="dict")
    if parsed is None:
        logger.warning("[%s] Explorer JSON 解析失败 raw=%.200s", repo_name, raw)
        return ExplorerResult(
            focus_files=[], focus_classes=[], reasoning="parse_failed", status="partial",
        )

    focus_files: list[str] = parsed.get("focus_files") or []
    focus_classes: list[str] = parsed.get("focus_classes") or []
    reasoning: str = parsed.get("reasoning") or ""

    status = _compute_status(focus_files, focus_classes, reasoning)
    return ExplorerResult(
        focus_files=focus_files,
        focus_classes=focus_classes,
        reasoning=reasoning,
        status=status,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 公开入口
# ═════════════════════════════════════════════════════════════════════════════

@observe(name="explore_repo", as_type="chain", capture_input=False, capture_output=False)
async def explore_repo(
    *,
    repo_path: str,
    skeleton: Skeleton,
    repo_name: str = "",
    namespace_id: int | None = None,
) -> ExplorerResult:
    """Phase A V3: 无界工具循环探索仓库, 识别数据持久化文件/类.

    Pipeline:
        1. 构建 system prompt (P1 V3 + 可选 repository_index)
        2. 构建 user message (P2 V3)
        3. chat_completion_with_tools 无界循环:
           - LLM 调用工具探索目录/读文件/搜索/glob
           - dead_loop 检测 (4 轮同 tool+input → 终止)
           - LLM 自主输出 JSON 文件列表 → 循环结束
        4. 解析输出 → ExplorerResult

    降级策略:
        - 任何 LLM/parse 失败 → ExplorerResult(status="partial", focus_files=[])
        - dead_loop → 终止, 返回 partial
        - 无迭代上限 — 靠 LLM 自主判断探索完成 + dead_loop 兜底
    """
    system_prompt = _build_explorer_system_prompt(skeleton)
    user_msg = _P2_V3_TEMPLATE.replace("{repo_path}", repo_path)

    # ── NEUTRAL 格式消息 (同 extraction_agent.py:292-306) ─────
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    # ── 工具函数映射 (注入 repo_path 作为 root) ────────────────
    tool_fns: dict[str, Callable[..., Any]] = {
        "list_dir": lambda **kw: list_dir(kw.get("path", "."), repo_path),
        "read_file": lambda **kw: read_file(
            kw["path"], repo_path, kw.get("offset"), kw.get("limit")),
        "grep": lambda **kw: grep(
            kw["pattern"], kw.get("path", "."), repo_path,
            kw.get("recursive", True)),
        "find_files": lambda **kw: find_files(kw["glob"], repo_path),
    }

    iteration = 0
    loop_start = time.time()
    recent_tool_calls: list[tuple[str, str]] = []

    while True:
        iteration += 1

        # ── LLM 调用 ───────────────────────────────────────────
        try:
            response: ToolUseResponse = await chat_completion_with_tools(
                messages=messages,
                tools=EXPLORER_TOOL_SPECS,
                thinking=False,
                namespace_id=namespace_id,
            )
        except Exception:
            logger.warning(
                "[%s] Explorer LLM failed iteration=%d", repo_name, iteration,
                exc_info=True,
            )
            return ExplorerResult(
                focus_files=[], focus_classes=[],
                reasoning="llm_call_failed", status="partial",
            )

        # ── LLM 无工具调用 → 解析当前输出为 JSON ────────────────
        if not response.tool_calls:
            text = response.text or ""
            if not text.strip():
                logger.warning(
                    "[%s] Explorer empty response iteration=%d", repo_name, iteration,
                )
                return ExplorerResult(
                    focus_files=[], focus_classes=[],
                    reasoning="empty_response", status="partial",
                )
            return _parse_explorer_output(text, repo_name)

        # ── 执行工具 (同步调用) ──────────────────────────────────
        tool_results: list[tuple] = []
        for tc in response.tool_calls:
            fn = tool_fns.get(tc.name)
            if fn is None:
                result = {"status": "error", "error_type": "UNKNOWN_TOOL",
                          "message": f"未知工具: {tc.name}"}
            else:
                try:
                    result = fn(**tc.input)
                except Exception as e:
                    result = {"status": "error", "error_type": type(e).__name__,
                              "message": str(e)}

            # ── dead_loop 检测 ────────────────────────────────
            tc_sig = (tc.name, json.dumps(tc.input, sort_keys=True, default=str))
            recent_tool_calls.append(tc_sig)
            if len(recent_tool_calls) > DEAD_LOOP_WINDOW:
                recent_tool_calls.pop(0)
            tool_results.append((tc, result))

        if _is_dead_loop(recent_tool_calls):
            logger.warning(
                "[%s] Explorer dead_loop iteration=%d", repo_name, iteration,
            )
            return ExplorerResult(
                focus_files=[], focus_classes=[],
                reasoning="dead_loop", status="partial",
            )

        # ── NEUTRAL 格式追加消息 (对齐 extraction_agent.py lines 292-306) ──
        processed_tcs = [tc for tc, _ in tool_results]
        messages.append(build_assistant_message(response, tool_calls=processed_tcs))
        for tc, result in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _serialize_tool_result(result),
            })

        tool_names = [tc.name for tc in response.tool_calls]
        logger.info(
            "[%s] Explorer iteration=%-3d  tools=%-40s  elapsed=%.1fs",
            repo_name, iteration, str(tool_names), time.time() - loop_start,
        )
