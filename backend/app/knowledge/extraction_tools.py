"""Agentic extraction: 4 只读工具 + 2 emit 工具 spec + 统一错误契约.

安全边界: 所有操作限定 clone 目录内, 禁绝对路径 / ../ 越界 / 写 / 网络.
"""
from __future__ import annotations

import fnmatch
import functools
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.engine.db_types import VALID_PARADIGMS

# 二进制/产物文件扩展名 — grep 跳过, 避免大仓遍历 .class/.jar/图片/压缩包性能退化
_BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".class", ".jar", ".war", ".ear", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".so", ".dll", ".dylib", ".o", ".a", ".bin", ".exe",
    ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4", ".avi", ".mov",
    ".pyc", ".pyo", ".lock",
})

# ── 错误契约 ──────────────────────────────────────────────
# ToolError 是异常: 工具内任意深度 raise, 由 @tool 装饰器统一序列化为
# {status: "error", ...} dict 返回给 LLM。如此调用方无需 `if err: return` 双 Optional
# 样板 — _sanitize_path 失败即 raise, 成功即返回真 Path (类型确定, 消灭 pyright 收窄难题).


class ToolError(Exception):
    def __init__(self, error_type: str = "", message: str = "", hint: str = ""):
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        return {"status": "error", "error_type": self.error_type,
                "message": self.message, "hint": self.hint}


def tool(fn):
    """工具统一出口: 把任意 ToolError 序列化为错误契约 dict。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except ToolError as e:
            return e.as_dict()
    return wrapper


def _sanitize_path(requested: str, root: str) -> Path:
    """安全解析路径: 只允许 root 内的相对路径, 越界/绝对路径/无法解析一律 raise ToolError。"""
    p = Path(requested)
    if p.is_absolute():
        raise ToolError(
            error_type="ACCESS_DENIED",
            message=f"拒绝绝对路径: '{requested}'",
            hint="使用相对路径 (如 'src/main/java'), 从 repo 根开始。",
        )
    # 防 ../ 越界 — 解析后须真落在 root 子树内 (用路径边界, 非字符串前缀:
    # 字符串前缀有兄弟目录碰撞漏洞, 如 root=/x/repo 时 /x/repo-evil 也 startswith 通过)
    try:
        root_resolved = Path(root).resolve()
        resolved = (root_resolved / p).resolve()
    except (ValueError, OSError):
        raise ToolError(
            error_type="PATH_NOT_FOUND",
            message=f"无法解析路径: '{requested}'",
            hint="用 list_dir 先确认目录结构。",
        )
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ToolError(
            error_type="ACCESS_DENIED",
            message=f"路径越界: '{requested}'",
            hint="只能访问 repo 目录内的文件。",
        )
    return resolved


@tool
def list_dir(path: str, root: str) -> dict[str, Any]:
    """列出目录内容。"""
    resolved = _sanitize_path(path or ".", root)
    if not resolved.exists():
        raise ToolError(error_type="PATH_NOT_FOUND",
                        message=f"目录不存在: '{path}'",
                        hint="先 list_dir 根目录了解结构。")
    if not resolved.is_dir():
        raise ToolError(error_type="NOT_A_DIRECTORY",
                        message=f"'{path}' 不是目录",
                        hint="对文件使用 read_file 而非 list_dir。")
    try:
        entries = list(resolved.iterdir())
    except PermissionError:
        raise ToolError(error_type="ACCESS_DENIED",
                        message=f"无权限读取: '{path}'",
                        hint="尝试其他目录。")

    dirs = sorted([e.name for e in entries if e.is_dir()])
    files = sorted([e.name for e in entries if e.is_file()])
    return {"status": "ok", "dirs": dirs, "files": files}


@tool
def read_file(path: str, root: str,
              offset: int | None = None, limit: int | None = None) -> dict[str, Any]:
    """读取文件内容, 支持 offset/limit 分段。"""
    resolved = _sanitize_path(path, root)
    if not resolved.exists():
        raise ToolError(error_type="PATH_NOT_FOUND",
                        message=f"文件不存在: '{path}'",
                        hint="用 find_files 或 list_dir 定位文件。")
    if not resolved.is_file():
        raise ToolError(error_type="NOT_A_FILE",
                        message=f"'{path}' 不是文件",
                        hint="对目录使用 list_dir。")
    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except (PermissionError, OSError) as e:
        raise ToolError(error_type="ACCESS_DENIED",
                        message=f"无法读取: '{path}': {e}",
                        hint="检查文件权限。")

    lines = content.splitlines()
    total = len(lines)
    start = max(0, offset or 0)
    end = min(total, start + (limit or total))
    selected = lines[start:end]
    return {
        "status": "ok",
        "content": "\n".join(selected),
        "total_lines": total,
        "start_line": start,
        "end_line": end,
    }


@tool
def grep(pattern: str, path: str, root: str,
         recursive: bool = True) -> dict[str, Any]:
    """在文件/目录中搜索文本模式 (Python re)。"""
    resolved = _sanitize_path(path or ".", root)
    if not resolved.exists():
        raise ToolError(error_type="PATH_NOT_FOUND",
                        message=f"路径不存在: '{path}'",
                        hint="用 list_dir 确认目录结构。")

    # ── ReDoS 防护: 正则长度上限 ──
    if len(pattern) > settings.agentic_extract_max_grep_pattern_len:
        raise ToolError(error_type="PATTERN_TOO_LONG",
                        message=(f"正则过长 ({len(pattern)} chars), "
                                 f"上限 {settings.agentic_extract_max_grep_pattern_len}"),
                        hint="简化正则或拆分多次 grep。")
    try:
        compiled = re.compile(pattern, re.MULTILINE | re.IGNORECASE)
    except re.error as e:
        raise ToolError(error_type="INVALID_PATTERN",
                        message=f"正则无效: '{pattern}': {e}",
                        hint="简化正则或使用普通字符串 (自动转为字面匹配)。")

    matches: list[dict] = []
    searched_files = 0
    root_resolved = Path(root).resolve()

    def _search_file(fp: Path) -> None:
        nonlocal searched_files
        if fp.suffix.lower() in _BINARY_EXTENSIONS:
            return  # 跳过二进制/产物文件 (不计入 searched_files)
        searched_files += 1
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, OSError):
            return
        for i, line in enumerate(content.splitlines(), 1):
            if compiled.search(line):
                matches.append({"file": str(fp.relative_to(root_resolved)),
                                "line": i, "match": line.strip()})

    if resolved.is_file():
        _search_file(resolved)
    elif resolved.is_dir():
        if recursive:
            for fp in resolved.rglob("*"):
                if fp.is_file():
                    _search_file(fp)
        else:
            for fp in resolved.iterdir():
                if fp.is_file():
                    _search_file(fp)

    return {"status": "ok", "matches": matches, "searched_files": searched_files}


@tool
def find_files(glob_pattern: str, root: str) -> dict[str, Any]:
    """按文件名 glob 定位文件。单次 rglob 遍历, 避免双重全树扫描。"""
    r = Path(root).resolve()
    if not r.exists():
        raise ToolError(error_type="PATH_NOT_FOUND",
                        message=f"root 不存在: '{root}'",
                        hint="检查 repo 根目录。")
    files = []
    is_path_glob = "/" in glob_pattern or "**" in glob_pattern
    for fp in r.rglob("*"):
        if not fp.is_file():
            continue
        rel = str(fp.relative_to(r))
        match = fnmatch.fnmatch(rel, glob_pattern) if is_path_glob \
            else fnmatch.fnmatch(fp.name, glob_pattern)
        if match:
            files.append(rel)
    return {"status": "ok", "files": sorted(files)}


# ── TOOL_SPECS (LLM 可见 schema) ──────────────────────────

EXTRACTION_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "list_dir",
        "description": "列出目录内容。Use when: 开始探索新仓库 / 需要了解目录结构 / 确认路径是否存在。Do not use when: 已知具体文件路径 (直接用 read_file)。Input: path=相对repo根的路径。Output: {dirs: [子目录名], files: [文件名]}",
        "input_schema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "相对 repo 根的目录路径。缺省为根目录。"}
            }
        }
    },
    {
        "name": "read_file",
        "description": "读取文件内容。Use when: 需要看源码 / 读了依赖清单后需深入 / grep 命中后需看上下文。Do not use when: 文件 > 500 行且只需看特定段落 (用 offset/limit)。Input: path=相对路径, offset=起始行(可省), limit=读取行数(可省)。Output: {content, total_lines, start_line, end_line}",
        "input_schema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "相对 repo 根的文件路径。"},
                "offset": {"type": "integer", "description": "起始行号 (0-based)。缺省从头开始。"},
                "limit": {"type": "integer", "description": "读取行数。缺省读取全部。"}
            }
        }
    },
    {
        "name": "grep",
        "description": "搜索文本模式 (大小写不敏感)。Use when: 找实体标志/@注解/类名/方法名/正则模式。Do not use when: 已知文件名找文件 (用 find_files) / 已知文件路径需读内容 (用 read_file)。Input: pattern=正则或普通字符串, path=文件或目录, recursive=是否递归(默认true)。Output: {matches: [{file, line, match}], searched_files: 实际搜索文件数}",
        "input_schema": {
            "type": "object",
            "required": ["pattern", "path"],
            "properties": {
                "pattern": {"type": "string", "description": "正则或普通字符串 (大小写不敏感)。"},
                "path": {"type": "string", "description": "文件/目录路径。目录默认递归搜索。"},
                "recursive": {"type": "boolean", "description": "目录搜索是否递归。默认 true。"}
            }
        }
    },
    {
        "name": "find_files",
        "description": "按文件名/路径 glob 定位文件。Use when: 已知文件名模式(如 *Entity.java) / 已知目录约定(如 **/model/**) / 找依赖清单。Do not use when: 按内容搜索 (用 grep) / 需看文件内容 (grep 命中后用 read_file)。Input: glob=文件名模式(**/*.xml, *Entity.java, models.py)。Output: {files: [匹配的相对路径]}",
        "input_schema": {
            "type": "object",
            "required": ["glob"],
            "properties": {
                "glob": {"type": "string", "description": "如 '*.java' / '**/*Mapper.xml' / '*Entity.java'。"}
            }
        }
    },
    {
        "name": "emit_schema_object",
        "description": "提交一个数据持久化对象(表/集合/文档)的完整 schema。Use when: 完成一个实体的字段+嵌套+枚举+关联的完整探索后。Do not use when: 仅发现实体但字段未展开 / 仅发现枚举未关联 / 不确定 paradigm。Input: 见 input_schema 完整定义。Output: {status: ok|rejected, message}",
        "input_schema": {
            "type": "object",
            "required": ["paradigm", "kind", "name", "fields", "source_ref"],
            "properties": {
                "paradigm": {"type": "string", "enum": sorted(VALID_PARADIGMS)},
                "kind": {"type": "string", "enum": ["table", "collection"]},
                "name": {"type": "string", "description": "表名/集合名"},
                "description": {"type": "string"},
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "type"],
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "description": {"type": "string"},
                            "nullable": {"type": "boolean"},
                            "indexed": {"type": "boolean"},
                            "source_ref": {"type": "string", "description": "该字段定义所在的源文件+行号 (如 Order.java:18). 用于链路追踪和幻觉排查."},
                            "sub_fields": {"type": "array", "items": {"type": "object"}, "description": "嵌套字段数组，元素格式同父级 fields"},
                            "enum_values": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["name", "db_value"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "db_value": {"type": "string"},
                                        "description": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                },
                "relations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["from_field", "to_object", "to_field"],
                        "properties": {
                            "from_field": {"type": "string", "description": "持有外键的列名"},
                            "to_object": {"type": "string", "description": "被引用的目标表/集合名"},
                            "to_field": {"type": "string", "description": "被引用的列名(如 id)。复合主键选最常用的那列"},
                            "relation_type": {"type": "string", "description": "基数类型: many_to_one / one_to_many / one_to_one"}
                        }
                    }
                },
                "source_ref": {"type": "string", "description": "来源文件路径"}
            }
        }
    },
    {
        "name": "emit_knowledge",
        "description": "提交一条知识发现 (非 schema 对象)。Use when: SQL SELECT语义化→emit example / 发现路由→emit route_hint / 发现术语→emit terminology / 发现规则→emit rule。Do not use when: 提交数据表/集合 schema (用 emit_schema_object)。Input: entry_type + payload 按类型填对应必填字段。Output: {status: ok|error, message}",
        "input_schema": {
            "type": "object",
            "required": ["entry_type", "payload"],
            "properties": {
                "entry_type": {
                    "type": "string",
                    "enum": ["route_hint", "terminology", "rule", "example"],
                    "description": "example=查询模式(SELECT语义化), terminology=术语, route_hint=路由提示, rule=业务规则"
                },
                "payload": {
                    "oneOf": [
                        {"title": "route_hint", "type": "object",
                         "required": ["mapper_namespace", "canonical_sql"],
                         "properties": {
                             "mapper_namespace": {"type": "string"},
                             "canonical_sql": {"type": "string"},
                         }},
                        {"title": "terminology", "type": "object",
                         "required": ["term", "primary_collection"],
                         "properties": {
                             "term": {"type": "string", "description": "业务术语 (实体类型/业务对象, ≤30字, 如 '订单' '商品')"},
                             "primary_collection": {"type": "string", "description": "术语对应的真实表名/集合名 (数据库中的实际名称)"},
                             "synonyms": {"type": "array", "items": {"type": "string"}, "description": "同义词列表 (可选)"},
                         }},
                        {"title": "rule", "type": "object",
                         "required": ["rule_text"],
                         "properties": {"rule_text": {"type": "string"}}},
                        {"title": "example", "type": "object",
                         "required": ["sql_pattern", "tables"],
                         "properties": {
                             "sql_pattern": {"type": "string"},
                             "tables": {"type": "array", "items": {"type": "string"}},
                             "question": {"type": "string"},
                             "mapper_namespace": {"type": "string"},
                         }},
                    ]
                }
            }
        }
    }
]

# ── Enum extraction agent tool spec ──────────────────────
# 独立于 EXTRACTION_TOOL_SPECS — schema 提取 agent 不应看到此工具.
EMIT_ENUM_DEFINITION_SPEC: dict[str, Any] = {
    "name": "emit_enum_definition",
    "description": (
        "提交一个枚举/常量类的完整定义。"
        "Use when: 已 read_file 完整提取了所有枚举值 (name + db_value + description)。"
        "Do not use when: 仅发现枚举名但未读源码 / 枚举值未完整提取。"
        "Input: enum_class=类名, fully_qualified_name=全限定名(可选), "
        "values=[{name, db_value, description}], source_file=定义文件路径。"
        "db_value 为 int 或 string 的数据库存储值。"
        "Output: {status: ok|error, message}"
    ),
    "input_schema": {
        "type": "object",
        "required": ["enum_class", "values"],
        "properties": {
            "enum_class": {
                "type": "string",
                "description": "枚举类名 (如 OrderStatus)",
            },
            "fully_qualified_name": {
                "type": "string",
                "description": "全限定名 (如 com.example.enums.OrderStatus)",
            },
            "values": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "db_value"],
                    "properties": {
                        "name": {"type": "string"},
                        "db_value": {"description": "int 或 string 数据库存储值"},
                        "description": {"type": "string"},
                    },
                },
            },
            "source_file": {"type": "string", "description": "定义所在的源文件路径"},
        },
    },
}
