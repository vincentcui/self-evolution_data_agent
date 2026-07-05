"""Enum 类专精解析 — 独立模块 (Phase 2 Gap #1).

从 code_parser.py 提取的 enum 扫描/解析/索引/注入逻辑.
设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/03-extraction.md §3.2
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings
from app.engine.json_parser import parse_llm_json
from app.engine.llm import chat_completion

logger = logging.getLogger(__name__)

# ── regex: 匹配 public enum 声明 ──
_RE_PUBLIC_ENUM = re.compile(r"\bpublic\s+enum\s+\w+")


# ════════════════════════════════════════════
#  Frozen dataclasses
# ════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class EnumValue:
    name: str
    db_value: int | str
    description: str | None


@dataclass(frozen=True, slots=True)
class EnumDef:
    enum_class: str
    fully_qualified_name: str
    values: list[EnumValue] = field(default_factory=list)


# ════════════════════════════════════════════
#  scan_enum_classes
# ════════════════════════════════════════════


def scan_enum_classes(java_files: list[str]) -> list[str]:
    """regex 扫含 'public enum ' 声明的 java 文件. 不调 LLM."""
    enum_files: list[str] = []
    for fp in java_files:
        try:
            text = Path(fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _RE_PUBLIC_ENUM.search(text):
            enum_files.append(fp)
    return enum_files


# ════════════════════════════════════════════
#  parse_enum_classes_batch
# ════════════════════════════════════════════

_ENUM_EXTRACTION_SYSTEM_PROMPT = """\
你是 Java 枚举类分析专家。分析以下 Java enum 源码, 提取枚举定义。

【db_value 推断规则】
- 枚举有构造参数 (如 `CREATED(1, "已创建")`) → 取首位 int 作 db_value
- 无构造参数 (纯名字枚举) → db_value = name (字符串)

【description 推断顺序】
1. 枚举常量上方 Javadoc 注释
2. 构造参数末位 String 参数
3. 无法推断 → null

返回严格 JSON (不要包含 markdown 代码块标记):
{
  "enum_class": "OrderStatus",
  "fully_qualified_name": "com.example.OrderStatus",
  "values": [
    {"name": "CREATED", "db_value": 1, "description": "已创建"},
    {"name": "PAID", "db_value": 2, "description": "已支付"}
  ]
}

【校验规则】
- values 非空数组
- 每个 value 的 name 非空字符串
- db_value 类型一致 (全 int 或全 string)
- fully_qualified_name 从 package 声明推断"""

_MAX_FILE_CHARS = 16000  # noqa: hardcode


async def parse_enum_classes_batch(
    enum_files: list[str],
    *,
    db=None,
    namespace_id: int | None = None,
    repo_id: int | None = None,
    repo_name: str = "",
    concurrency: int = 4,
) -> tuple[list[EnumDef], dict[str, EnumDef]]:
    """逐个 enum 文件调 LLM 提取枚举定义 (async, Semaphore 限流并发).

    返回 (enum_defs, enum_class_index)
    - enum_defs: 所有成功解析的 EnumDef
    - enum_class_index: 双索引 (simple_name + fqn → EnumDef)

    concurrency: 并发 LLM 调用数上限 (默认 4, 与 IS_AGENTIC_EXTRACT_SUBAGENT_CONCURRENCY 对齐).
    """
    total = len(enum_files)
    prefix = f"[{repo_name}] " if repo_name else ""
    logger.info("%senum LLM 解析开始, 共 %d 个候选文件 (并发度=%d)", prefix, total, concurrency)

    enum_defs: list[EnumDef] = []
    enum_class_index: dict[str, EnumDef] = {}
    sem = asyncio.Semaphore(concurrency)
    completed: int = 0
    lock = asyncio.Lock()

    async def _process_one(path: str) -> EnumDef | None:
        nonlocal completed
        async with sem:
            try:
                content = Path(path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                async with lock:
                    completed += 1
                logger.warning("%senum 文件读取失败: %s", prefix, path)
                return None

            if len(content) > _MAX_FILE_CHARS:
                content = content[:_MAX_FILE_CHARS] + "\n// ... 文件已截断"

            messages = [
                {"role": "system", "content": _ENUM_EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ]
            try:
                raw = await asyncio.to_thread(
                    chat_completion, messages=messages, temperature=0.1,
                    max_tokens=settings.enum_extract_max_tokens, thinking=False,
                )
            except Exception as e:
                async with lock:
                    completed += 1
                logger.warning("%senum LLM 调用异常, 跳过 %s: %s", prefix, path, e)
                return None

            data = _safe_parse_json(raw)
            if not data:
                async with lock:
                    completed += 1
                logger.warning("%senum LLM 输出 JSON 解析失败, 跳过: %s", prefix, path)
                return None

            values = data.get("values")
            if not values or not isinstance(values, list):
                async with lock:
                    completed += 1
                logger.warning("%senum values 为空或非数组, 跳过: %s", prefix, path)
                return None

            if not all(v.get("name") for v in values):
                async with lock:
                    completed += 1
                logger.warning("%senum value.name 为空, 跳过: %s", prefix, path)
                return None

            async with lock:
                completed += 1
                if completed % 10 == 0 or completed == total:
                    logger.info("%senum LLM 解析进度: %d/%d", prefix, completed, total)

            return _to_enum_def(data)

    tasks = [_process_one(f) for f in enum_files]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, EnumDef):
            enum_defs.append(r)
            if r.enum_class:
                enum_class_index[r.enum_class] = r
            if r.fully_qualified_name:
                enum_class_index[r.fully_qualified_name] = r
        elif isinstance(r, Exception):
            logger.warning("%senum LLM 解析异常 (gather): %s", prefix, r)

    logger.info("%senum LLM 解析完成: %d/%d 成功", prefix, len(enum_defs), total)
    return enum_defs, enum_class_index


# ════════════════════════════════════════════
#  build_enum_class_index
# ════════════════════════════════════════════


def build_enum_class_index(enum_defs: list[EnumDef]) -> dict[str, EnumDef]:
    """双索引: simple_name + fully_qualified_name."""
    idx: dict[str, EnumDef] = {}
    for e in enum_defs:
        if e.enum_class:
            idx[e.enum_class] = e
        if e.fully_qualified_name:
            idx[e.fully_qualified_name] = e
    return idx


# ════════════════════════════════════════════
#  _resolve_enum_class helpers (Phase 1 Plan 01)
# ════════════════════════════════════════════

ENUM_NAME_SUFFIXES = frozenset({
    "Status", "Type", "Level", "State",
    "Kind", "Mode", "Flag", "Code",
    "Category", "Stage",
})

BASE_TYPES = frozenset({
    "Integer", "Long", "String", "Short", "Byte",
    "Boolean", "Double", "Float", "BigDecimal",
    "int", "long", "short", "byte", "boolean", "double", "float",
})


def _split_camel(s: str) -> list[str]:
    """paymentStatus -> ['payment', 'Status']; my_status -> ['my', 'status']."""
    return [t for t in re.split(r"(?<=[a-z])(?=[A-Z])|_", s) if t]


def _is_base_type(ftype: str) -> bool:
    """剥外层泛型后判断."""
    inner = re.sub(r"<.*>", "", ftype).strip()
    return inner in BASE_TYPES


def _field_root_and_suffix(fname: str) -> tuple[tuple[str, ...], str | None]:
    """
    paymentStatus -> (('payment',), 'Status')
    type -> ((), None)  # 单 token 即后缀, 词根空, 不参与匹配
    """
    tokens = _split_camel(fname)
    if not tokens:
        return (), None
    last_cap = tokens[-1][:1].upper() + tokens[-1][1:]
    if last_cap in ENUM_NAME_SUFFIXES:
        return tuple(t.lower() for t in tokens[:-1]), last_cap
    return (), None


def _enum_root_and_suffix(enum_class: str) -> tuple[tuple[str, ...], str | None]:
    """
    OrderStatusEnum -> (('order',), 'Status')  # 'Enum' 后缀剥掉
    DeleteStatus -> (('delete',), 'Status')
    """
    n = enum_class
    if n.endswith("Enum") and len(n) > 4:
        n = n[:-4]
    tokens = _split_camel(n)
    if not tokens:
        return (), None
    if tokens[-1] in ENUM_NAME_SUFFIXES:
        return tuple(t.lower() for t in tokens[:-1]), tokens[-1]
    return (), None


def _match_by_root(
    fname: str,
    enum_class_index: dict[str, "EnumDef"],
) -> str | None:
    """词根 token 序列完全相等 + 后缀完全相等 = 命中. 多候选取最短."""
    f_root, f_suf = _field_root_and_suffix(fname)
    if not f_root or not f_suf:
        return None
    candidates: list[str] = []
    for ec in enum_class_index:
        e_root, e_suf = _enum_root_and_suffix(ec)
        if e_suf != f_suf or e_root != f_root:
            continue
        candidates.append(ec)
    if not candidates:
        return None
    candidates.sort(key=lambda x: (len(x), x))
    return candidates[0]


def _resolve_enum_class(
    field: dict[str, Any],
    enum_class_index: dict[str, "EnumDef"],
) -> tuple[str | None, str]:
    """
    返回 (enum_class_name, enum_source) 或 (None, "").
    enum_source ∈ code_hint | code_type | code_type_generic | name_heuristic
    """
    # Layer 1: hint
    hint = field.get("enum_class_hint")
    if hint:
        simple = hint.rsplit(".", 1)[-1]
        if simple in enum_class_index:
            return simple, "code_hint"

    # Layer 2: type 字面
    ftype = field.get("type", "")
    if ftype in enum_class_index:
        return ftype, "code_type"

    # Layer 3: 泛型内层
    m = re.search(r"<(\w+)>", ftype)
    if m and m.group(1) in enum_class_index:
        return m.group(1), "code_type_generic"

    # Layer 4: 仅基础类型字段才走启发式
    if not _is_base_type(ftype):
        return None, ""

    fname = field.get("name", "")
    match = _match_by_root(fname, enum_class_index)
    if match:
        return match, "name_heuristic"

    return None, ""


# ════════════════════════════════════════════
#  enrich_entity_fields_with_enum_index
# ════════════════════════════════════════════


def enrich_entity_fields_with_enum_index(
    entities: list[dict[str, Any]],
    mongo_docs: list[dict[str, Any]],
    enum_class_index: dict[str, EnumDef],
) -> None:
    """后处理 — 三级回退匹配字段 ↔ enum, 命中则反填 enum_values + _enum_* 元数据.

    元数据字段:
        _enum_source: code_hint | code_type | code_type_generic | name_heuristic
        _enum_class_name: 命中的 enum simple name
        _enum_match_status: pending (字段名含 enum 后缀但全部 layer miss)

    幂等: 已有 enum_values 的字段不覆盖.
    """
    if not enum_class_index:
        # 仍要写 pending 状态: 字段名含后缀但 index 空
        for entity in entities:
            _walk_for_pending(entity.get("columns", []))
        for doc in mongo_docs:
            _walk_for_pending(doc.get("fields", []))
        return

    def _walk(fields: list[dict[str, Any]]) -> None:
        for f in fields:
            if f.get("enum_values"):  # 幂等: 已填不动
                if f.get("sub_fields"):
                    _walk(f["sub_fields"])
                continue

            ec, source = _resolve_enum_class(f, enum_class_index)
            if ec:
                enum_def = enum_class_index[ec]
                f["enum_values"] = [
                    {"name": v.name, "db_value": v.db_value, "description": v.description}
                    for v in enum_def.values
                ]
                f["_enum_source"] = source
                f["_enum_class_name"] = ec
            else:
                # miss 但字段名含 enum 后缀 → pending
                fname = f.get("name", "")
                tokens = _split_camel(fname)
                if len(tokens) >= 2 and (tokens[-1][:1].upper() + tokens[-1][1:]) in ENUM_NAME_SUFFIXES:
                    f["_enum_match_status"] = "pending"

            if f.get("sub_fields"):
                _walk(f["sub_fields"])

    for entity in entities:
        _walk(entity.get("columns", []))
    for doc in mongo_docs:
        _walk(doc.get("fields", []))


def _walk_for_pending(fields: list[dict[str, Any]]) -> None:
    """enum_class_index 空时, 仅标 pending."""
    for f in fields:
        if f.get("enum_values"):
            continue
        fname = f.get("name", "")
        tokens = _split_camel(fname)
        if len(tokens) >= 2 and (tokens[-1][:1].upper() + tokens[-1][1:]) in ENUM_NAME_SUFFIXES:
            f["_enum_match_status"] = "pending"
        if f.get("sub_fields"):
            _walk_for_pending(f["sub_fields"])


# ════════════════════════════════════════════
#  Internal helpers
# ════════════════════════════════════════════


def _to_enum_def(parsed: dict[str, Any]) -> EnumDef:
    return EnumDef(
        enum_class=parsed.get("enum_class", ""),
        fully_qualified_name=parsed.get("fully_qualified_name", ""),
        values=[
            EnumValue(
                name=v["name"],
                db_value=v.get("db_value", v["name"]),
                description=v.get("description"),
            )
            for v in parsed.get("values", [])
        ],
    )


def _safe_parse_json(raw: str) -> dict | None:
    """Parse JSON from LLM response — delegates to unified parser."""
    return parse_llm_json(raw, expect="dict")
