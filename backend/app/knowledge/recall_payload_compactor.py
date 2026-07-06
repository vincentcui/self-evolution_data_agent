"""召回 payload 字面量压缩 — lookup_knowledge 返 LLM 前剥离数据快照.

目的:
- example.query_json 含具体 ObjectId 列表 / 长 regex / 整段子 pipeline 等"一次性快照",
  直接喂 LLM 会浪费 token 且诱导 LLM 当模板复用.
- 本模块按 mongo / SQL 语法层通用规则递归压缩, 保 pipeline/operator/业务字段名等
  "可复用结构", 摘掉具体字面量值.

调用方: app/engine/tools/knowledge_tools.py::lookup_knowledge
"""
from __future__ import annotations

from typing import Any

from app.config import settings


# Mongo 逻辑容器: list[dict] 是控制流结构, 不是数据数组, 递归保元素.
_LOGICAL_LIST_KEYS: frozenset[str] = frozenset({
    "$or", "$and", "$nor", "pipeline",
})

# BSON 类型 wrapper (Extended JSON): 一律保类型标识 + 摘值.
_BSON_TYPE_KEYS: frozenset[str] = frozenset({
    "$oid", "$date", "$timestamp", "$binary", "$numberDecimal",
    "$numberLong", "$numberInt", "$numberDouble", "$regularExpression",
    "$code", "$symbol", "$uuid",
})


def compact_payload_for_recall(entry_type: str, payload: dict) -> dict:
    """召回入参 payload → 压缩后 payload, 不改原 dict (返回新对象).

    **按 entry_type 分发**, 因为不同 entry_type 的 payload 性质完全不同:
    - example.query_json: 数据快照 (含 ObjectId 列表 / 时间戳等字面量), 必须压缩
    - 其他 entry_type (terminology/rule/route_hint/instance_alias): 全是
      业务语义文本 (reason/rule_text/synonyms 等), **原样返回**, 任何截断都会
      丢失知识本身.

    历史 bug (2026-05-29): 早期实现对所有 entry_type 都跑 _walk, 导致
    route_hint.reason (373 字完整避坑链路) 被 IS_RECALL_PAYLOAD_MAX_STR_LEN=120
    截断, 召回后 LLM 看到 "...<+273 chars>" 无法决策, 反而比无 KE 时跑得更差.
    """
    if not isinstance(payload, dict):
        return payload
    if entry_type != "example":
        # 语义文本类 payload 原样保留
        return payload
    # example.query_json + final_query_plan 含数据快照, 深度压缩
    out = dict(payload)
    if "final_query_plan" in out and isinstance(out["final_query_plan"], dict):
        out["final_query_plan"] = _compact_query_plan(out["final_query_plan"])
    if "query_json" in out:
        out["query_json"] = _walk(out["query_json"])
    return out


# ── query plan 联动压缩 ─────────────────────────────────────


def _compact_query_plan(plan: dict) -> dict:
    """Compress query plan by step.db_type: SQL literal stripping, Mongo _walk."""
    from app.engine.db_types import SQL_DB_TYPES, DOCUMENT_DB_TYPES

    out = dict(plan)
    steps: list[dict] = []
    for step in out.get("steps") or []:
        if not isinstance(step, dict):
            steps.append(step)
            continue
        s = dict(step)
        db_type = str(s.get("db_type", "mongodb"))
        query = s.get("query")
        if isinstance(query, dict):
            if db_type in SQL_DB_TYPES:
                s["query"] = _compact_sql_query(query)
            elif db_type in DOCUMENT_DB_TYPES:
                s["query"] = _walk(query)
            # else: unknown paradigm → leave query as-is (no known compaction)
        steps.append(s)
    out["steps"] = steps
    return out


def _compact_sql_query(query: dict) -> dict:
    """Strip SQL string literals and numeric constants, preserve structure."""
    import re
    out = dict(query)
    sql = out.get("sql")
    if isinstance(sql, str):
        sql = re.sub(r"'[^']*'", "'...'", sql)
        sql = re.sub(r'"[^"]*"', '"..."', sql)
        sql = re.sub(r'(?<=[=<>]\s)\d+', 'N', sql)
        sql = re.sub(r"\bIN\s*\([\d,\s]+\)", "IN(...)", sql)
        out["sql"] = sql
    return out


def _walk(node: Any, *, parent_key: str | None = None) -> Any:
    """递归遍历, 按节点类型分发."""
    if isinstance(node, dict):
        return _walk_dict(node)
    if isinstance(node, list):
        return _walk_list(node, parent_key=parent_key)
    if isinstance(node, str):
        return _walk_str(node)
    # int / float / bool / None — 保留 (短业务约束)
    return node


def _walk_dict(node: dict) -> dict:
    # BSON 类型 wrapper: 保类型标识, 替换值
    if len(node) == 1:
        only_key = next(iter(node))
        if only_key in _BSON_TYPE_KEYS:
            return {only_key: _placeholder_for_bson(node[only_key])}

    out: dict[str, Any] = {}
    for k, v in node.items():
        # key 保留 ($ operator + 业务字段都是知识本身)
        out[k] = _walk(v, parent_key=k)
    return out


def _walk_list(node: list, *, parent_key: str | None = None) -> Any:
    # 逻辑容器: pipeline / $or / $and / $nor → 递归, 不摘 list 长度
    if parent_key in _LOGICAL_LIST_KEYS:
        return [_walk(x) for x in node]

    # $switch.branches / $facet 等也是 list[dict] 控制流 → 走 dict 递归
    if node and all(isinstance(x, dict) for x in node):
        # list[dict] 长度大且元素结构简单 (无 nested operator) → 视为数据数组摘
        # 否则递归保元素
        if len(node) > settings.recall_payload_max_list_len:
            sample = _walk(node[0]) if node else None
            return [{
                "__placeholder__": (
                    f"<list_of_dict count={len(node)}, "
                    f"sample_keys={sorted(node[0].keys())[:5] if node else []}>"
                ),
                "sample": sample,
            }]
        return [_walk(x) for x in node]

    # 数据数组判定
    max_len = settings.recall_payload_max_list_len
    long_scalar = settings.recall_payload_long_scalar_len

    if len(node) > max_len:
        # 长 list → 摘
        return [_placeholder_for_list(node)]

    if node and all(isinstance(x, str) for x in node):
        avg_len = sum(len(x) for x in node) / len(node)
        if avg_len > long_scalar:
            # 短 list 但元素是长字面量 (ObjectId/UUID/timestamp) → 摘
            return [_placeholder_for_list(node)]

    # 短 list of 短 scalar / 短 str → 全保 (业务枚举有语义)
    return [_walk(x) for x in node]


def _walk_str(s: str) -> str:
    max_len = settings.recall_payload_max_str_len
    if len(s) <= max_len:
        return s
    keep = max_len - 20  # 留 20 字符给 marker
    return f"{s[:keep]}…<+{len(s) - keep} chars>"


def _placeholder_for_list(node: list) -> dict:
    """长 list / 长字面量 list → 单元素 placeholder dict, 保类型 + 数量."""
    if not node:
        return {"__placeholder__": "<empty_list>"}
    first = node[0]
    if isinstance(first, str):
        elem_type = "str"
        elem_meta = f"avg_len={int(sum(len(x) for x in node if isinstance(x, str)) / max(1, len(node)))}"
    elif isinstance(first, bool):
        elem_type = "bool"
        elem_meta = ""
    elif isinstance(first, int):
        elem_type = "int"
        elem_meta = f"range=[{min(node)},{max(node)}]" if all(isinstance(x, int) for x in node) else ""
    elif isinstance(first, float):
        elem_type = "float"
        elem_meta = ""
    elif isinstance(first, dict):
        elem_type = "dict"
        elem_meta = f"sample_keys={sorted(first.keys())[:5]}"
    else:
        elem_type = type(first).__name__
        elem_meta = ""
    meta_str = f", {elem_meta}" if elem_meta else ""
    return {"__placeholder__": f"<list_of_{elem_type} count={len(node)}{meta_str}>"}


def _placeholder_for_bson(value: Any) -> str:
    """BSON 类型 wrapper 的 value → 类型化 placeholder."""
    if isinstance(value, str):
        return f"<bson_value len={len(value)}>"
    if isinstance(value, dict):
        return f"<bson_value keys={sorted(value.keys())[:3]}>"
    return f"<bson_value type={type(value).__name__}>"
