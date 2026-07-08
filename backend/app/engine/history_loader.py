"""多轮对话历史注入 — 读 QueryHistory 重建 user/assistant 消息对.

职责: 按 session_id 读最近 N 轮 QueryHistory, 每轮重建 user(=content) +
assistant(=generated_query + final_answer) 消息对, 返回正序 messages 供
agent_loop 拼在 system_prompt 之后。

设计:
  - 一行 QueryHistory = 一轮 (role=assistant, content=用户问题,
    result_snapshot.final_answer=回答, generated_query=最后成功 exec 的 input)。
  - 跳过 final_answer 为空的轮 (cancelled/error/未完成) — 避免孤儿 user
    消息破坏 user/assistant 严格交替 (Anthropic 硬约束)。
  - 排序 created_at DESC, id DESC — 秒粒度 created_at 撞值时 id 兜底保序。
  - 任何异常降级返回 [] (历史是增强不是必需, 不阻断主链路)。
  - assistant 模板经 personal-skills:prompt-engineering-2026 定稿:
    双段用"执行的查询/回答"标签划界; 无查询时裸放 final_answer (D8 去噪)。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.json_parser import parse_llm_json
from app.models import QueryHistory

log = logging.getLogger(__name__)


async def build_history_messages(
    db: AsyncSession,
    session_id: str,
    namespace_id: int,
    max_turns: int,
) -> list[dict]:
    """读同 session 最近 max_turns 轮, 重建 user/assistant 消息对(正序)。

    Returns: 按时间正序(最老→最新)的 messages; 异常或无历史返回 []。
    """
    if not session_id or max_turns <= 0:
        return []
    try:
        rows = (await db.execute(
            select(QueryHistory)
            .where(
                QueryHistory.session_id == session_id,
                QueryHistory.namespace_id == namespace_id,
            )
            .order_by(QueryHistory.created_at.desc(), QueryHistory.id.desc())
            .limit(max_turns)
        )).scalars().all()
    except Exception:
        log.warning(
            "[history_loader] query failed session_id=%s ns=%s — degrade to single-turn",
            session_id, namespace_id, exc_info=True,
        )
        return []

    messages: list[dict] = []
    for row in reversed(rows):  # 反转为正序(最老→最新)
        snap = parse_llm_json(row.result_snapshot or "{}", expect="dict")
        if snap is None:  # 畸形 JSON — 显式降级, 不用 `or {}` 折叠信号
            snap = {}
        final_answer = (snap.get("final_answer") or "").strip()
        if not final_answer:
            continue  # 跳过无回答的轮, 保 user/assistant 交替
        messages.append({"role": "user", "content": row.content or ""})
        if row.generated_query:
            asst = f"执行的查询：\n{row.generated_query}\n\n回答：\n{final_answer}"
        else:
            asst = final_answer
        messages.append({"role": "assistant", "content": asst})
    return messages
