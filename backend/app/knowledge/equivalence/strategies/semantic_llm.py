"""Equivalence strategy: LLM 语义等价判定 (新增能力).

确定性 helper 全 miss 后的兜底: 调 LLM 判断两个 candidate 是否语义等价.
失败时静默返 None (不抛异常, 让 registry 链继续到 conflict).

Prompt 走 `backend/prompts/semantic_equivalence.md` (与 docs 设计稿
`docs/.../2026-05-19-mongo-canonical-retirement/prompts/` 保持一致,
由 T9 step 4 prompt-drift 门守护).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from string import Template
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

# 运行时 prompt 模板路径 (统一收口到 backend/prompts/, 与 design 稿同源)
# backend/app/knowledge/equivalence/strategies/semantic_llm.py → parents[4] = backend/
_PROMPT_FILE = Path(__file__).resolve().parents[4] / "prompts" / "semantic_equivalence.md"


def _load_prompt_body() -> str:
    """读 prompts/semantic_equivalence.md, 提取 '## 模板正文' 后的 ``` 块."""
    text = _PROMPT_FILE.read_text(encoding="utf-8")
    marker = "## 模板正文"
    idx = text.find(marker)
    if idx < 0:
        raise ValueError(f"prompt {_PROMPT_FILE} missing '## 模板正文' section")
    body_section = text[idx + len(marker):]
    fence_start = body_section.find("```")
    fence_end = body_section.rfind("```")
    if fence_start < 0 or fence_end <= fence_start:
        raise ValueError(f"prompt {_PROMPT_FILE} 模板正文 fence 缺失")
    body_start = body_section.find("\n", fence_start) + 1
    return body_section[body_start:fence_end].strip()


# 启动时一次性加载, 避免每次 checker 调用都读文件
_PROMPT_BODY = _load_prompt_body()


class _SemanticBudget:
    """单批 promote 的 LLM 调用预算计数器.

    由 promote 主流程在每批开始时 reset(), 每次调用 consume() 扣 1.
    用尽后 checker 直接返 None, 不发请求.
    """

    _remaining: int = 20  # default, overridden by settings at reset

    @classmethod
    def reset(cls) -> None:
        cls._remaining = getattr(settings, "equivalence_llm_budget_per_batch", 20)

    @classmethod
    def consume(cls) -> bool:
        """尝试消费 1 次配额. 返 True 表示可用, False 表示已耗尽."""
        if cls._remaining <= 0:
            return False
        cls._remaining -= 1
        return True

    @classmethod
    def exhaust(cls) -> None:
        """测试用: 强制耗尽预算."""
        cls._remaining = 0


async def _call_llm(prompt: str) -> str:
    """调用 LLM 获取语义等价判定结果 (raw JSON string).

    思考由全局默认关闭 (settings.llm_thinking_enabled=False), max_tokens=200 足够。
    """
    import asyncio

    from app.engine.llm import chat_completion

    # chat_completion 是 sync 函数, 用 to_thread 包装避免阻塞 event loop
    return await asyncio.to_thread(
        chat_completion,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=200, thinking=False,
    )


def _build_prompt(cands: list) -> str:
    """渲染 prompts/semantic_equivalence.md 模板, 填入 6 个运行时字段."""
    c1, c2 = cands[0], cands[1]
    kind = getattr(c1, "candidate_kind", "field_description")
    db_type = getattr(c1, "db_type", "mysql")
    target = getattr(c1, "target", "unknown")
    field_path = getattr(c1, "field_path", "unknown")

    val_a = json.loads(c1.candidate_value_json)
    val_b = json.loads(c2.candidate_value_json)

    # 取 description 或整个 value 作为比较内容
    a_text = val_a.get("description", json.dumps(val_a, ensure_ascii=False))
    b_text = val_b.get("description", json.dumps(val_b, ensure_ascii=False))

    return Template(_PROMPT_BODY).safe_substitute(
        kind=kind,
        db_type=db_type,
        target=target,
        field_path=field_path,
        candidate_a_value=a_text,
        candidate_b_value=b_text,
    )


async def semantic_llm_checker(cands: list) -> tuple[Any, str] | None:
    """LLM 语义等价判定.

    - 预算耗尽 → None
    - LLM 错误 → None (不抛)
    - 输出非法 JSON → None
    - confidence < 0.7 → None
    - equivalent=false → None
    - equivalent=true + confidence >= 0.7 → (winner, "matched")
    """
    if len(cands) < 2:
        return None

    # 预算检查
    if not _SemanticBudget.consume():
        log.debug("[semantic_llm] budget exhausted, skipping")
        return None

    prompt = _build_prompt(cands)

    try:
        raw = await _call_llm(prompt)
    except Exception as exc:
        log.warning("[semantic_llm] LLM call failed: %s", exc)
        return None

    # 解析 JSON
    from app.engine.json_parser import parse_llm_json
    result = parse_llm_json(raw, expect="dict")
    if result is None:
        log.warning("[semantic_llm] JSON parse failed")
        return None

    # 校验字段
    equivalent = result.get("equivalent")
    confidence = result.get("confidence", 0.0)

    if not isinstance(equivalent, bool):
        log.warning("[semantic_llm] 'equivalent' not bool: %r", equivalent)
        return None

    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        log.warning("[semantic_llm] invalid confidence: %r", confidence)
        return None

    if confidence < 0.7:
        log.info("[semantic_llm] low confidence %.2f, treating as uncertain", confidence)
        return None

    if not equivalent:
        return None

    # 等价: 选第一个 candidate 作为 winner
    return (cands[0], "matched")
