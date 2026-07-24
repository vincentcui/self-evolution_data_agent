"""Stage 2 抓手 B — 召回反馈环窗口 (Backend 抽象).

设计自决 (Spec D7 + B.E3): 当前 prod 单进程 (CLAUDE.md 禁 --reload), 用内存 backend
足够. 多进程切 Redis 时只换 backend 实现, 业务路径 (knowledge_tools.py / agent_loop.py)
用 Protocol 接口, 不动. drop-in 替换.

接口契约:
  window_record(trace_id, entry_ids):           agent_loop 主循环 — lookup_knowledge 后入栈
  window_consume_next_call(trace_id, tool_name): agent_loop 跑下一个 tool 后调,
                                                  根据 tool_name 决定 adopted / negative
  window_pop(trace_id) → dict | None:           trace 结束时 flush, 一次性 UPDATE knowledge_entries
  window_size() → int:                           运维诊断用 (活跃窗口数)

模块级函数全部转发给 _backend (RecallWindowBackend), 替换 backend 不影响调用方.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)

# 隐式信号分类 (Spec D5 决策 2b) — backend 无关, 仍是模块级常量
ADOPTING_TOOLS: frozenset[str] = frozenset({
    "execute_query", "execute_plan", "present_result", "save_knowledge",
})
NEGATIVE_TOOLS: frozenset[str] = frozenset({
    "fetch_schema", "clarify_with_user", "inspect_values",
})


# ════════════════════════════════════════════
#  Backend Protocol — 抽象接口, drop-in 替换
# ════════════════════════════════════════════

class RecallWindowBackend(Protocol):
    """召回窗口后端契约. 实现可以是内存 dict 或 Redis hash."""

    def record(self, trace_id: str, entry_ids: list[int]) -> None: ...
    def consume(self, trace_id: str, tool_name: str) -> None: ...
    def pop(self, trace_id: str) -> dict | None: ...
    def size(self) -> int: ...


# ════════════════════════════════════════════
#  内存实现 (单进程, prod 默认)
# ════════════════════════════════════════════

@dataclass
class _RecallEntry:
    entry_id: int
    recalled_at: float
    consumed: bool = False


@dataclass
class _TraceWindow:
    pending: list[_RecallEntry] = field(default_factory=list)
    adopted: dict[int, int] = field(default_factory=dict)
    negative: dict[int, int] = field(default_factory=dict)
    recall_inc: dict[int, int] = field(default_factory=dict)


class MemoryBackend:
    """RecallWindowBackend 内存实现 — 单进程 dict, prod 默认."""

    def __init__(self) -> None:
        self._windows: dict[str, _TraceWindow] = {}

    def record(self, trace_id: str, entry_ids: list[int]) -> None:
        if not trace_id or not entry_ids:
            return
        win = self._windows.setdefault(trace_id, _TraceWindow())
        now = time.time()
        for eid in entry_ids:
            win.pending.append(_RecallEntry(entry_id=eid, recalled_at=now))
            win.recall_inc[eid] = win.recall_inc.get(eid, 0) + 1

    def consume(self, trace_id: str, tool_name: str) -> None:
        if not trace_id or trace_id not in self._windows:
            return
        win = self._windows[trace_id]
        if tool_name == "lookup_knowledge":
            return
        target = (
            win.adopted if tool_name in ADOPTING_TOOLS
            else (win.negative if tool_name in NEGATIVE_TOOLS else None)
        )
        if target is None:
            return
        for entry in win.pending:
            if entry.consumed:
                continue
            target[entry.entry_id] = target.get(entry.entry_id, 0) + 1
            entry.consumed = True

    def pop(self, trace_id: str) -> dict | None:
        win = self._windows.pop(trace_id, None)
        if win is None:
            return None
        return {
            "recall_inc": dict(win.recall_inc),
            "adopted_inc": dict(win.adopted),
            "negative_inc": dict(win.negative),
        }

    def size(self) -> int:
        return len(self._windows)


# ════════════════════════════════════════════
#  模块级转发 (业务调用方接口稳定)
# ════════════════════════════════════════════

# 单例 backend. 多进程上线时由 main.py lifespan 切 RedisBackend (drop-in 替换).
_backend: RecallWindowBackend = MemoryBackend()


def set_backend(backend: RecallWindowBackend) -> None:
    """运维 / 启动钩子切换 backend (例: prod 多进程切 RedisBackend)."""
    global _backend
    _backend = backend


def window_record(trace_id: str, entry_ids: list[int]) -> None:
    _backend.record(trace_id, entry_ids)


def window_consume_next_call(trace_id: str, tool_name: str) -> None:
    _backend.consume(trace_id, tool_name)


def window_pop(trace_id: str) -> dict | None:
    return _backend.pop(trace_id)


def window_size() -> int:
    return _backend.size()
