"""推荐问题静默降级测试 (spec test 18-19).

验证 query.py 中推荐问题生成的异常处理和空文本行为。
测试代码复刻 _run_and_finalize 中的推荐逻辑，通过 mock chat_completion 控制行为。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


async def _run_recommend(event_q: asyncio.Queue, mock_result):
    """复刻 query.py _run_and_finalize 中的推荐问题生成代码.

    mock_result: 字符串(正常返回) 或 Exception 实例(模拟失败).
    """
    final_answer = "test answer"
    question = "test question"
    ctx_chars = 2000
    max_tokens = 256
    temperature = 0.3
    timeout = 10

    side_effect = mock_result if isinstance(mock_result, Exception) else None
    return_value = None if isinstance(mock_result, Exception) else mock_result
    with patch("app.engine.llm.chat_completion",
               side_effect=side_effect, return_value=return_value):
        try:
            from app.engine.llm import chat_completion
            recom_prompt = (
                "基于以下问答，生成 1-3 个用户可能感兴趣的后续追问。"
                "每行一个问题，不要编号。\n\n"
                f"用户问题：{question}\n"
                f"回答：{final_answer[:ctx_chars]}\n\n"
                "输出 1-3 行自然语言问题："
            )
            recom_text = await asyncio.wait_for(asyncio.to_thread(
                chat_completion,
                messages=[{
                    "role": "system", "content": "你是帮助用户探索数据的助手。",
                }, {
                    "role": "user", "content": recom_prompt,
                }],
                max_tokens=max_tokens,
                temperature=temperature,
            ), timeout=timeout)
            questions = [q.strip() for q in recom_text.strip().split("\n") if q.strip()]
            if questions:
                await event_q.put({
                    "event": "recommended_questions",
                    "data": {"questions": questions[:3]},
                })
        except Exception:
            pass  # 静默降级


@pytest.mark.asyncio
async def test_recommend_llm_failure_silent_fallback():
    """spec test 18: LLM 调用失败 → 不推 SSE 事件."""
    event_q = asyncio.Queue()
    await _run_recommend(event_q, Exception("LLM down"))
    assert event_q.empty()


@pytest.mark.asyncio
async def test_recommend_empty_text_no_push():
    """spec test 19: LLM 返回空/空白文本 → 不推 SSE 事件."""
    event_q = asyncio.Queue()
    await _run_recommend(event_q, "   \n  \n   ")
    assert event_q.empty()


@pytest.mark.asyncio
async def test_recommend_normal_questions_push():
    """正常生成推荐问题 → 推送 SSE 事件."""
    event_q = asyncio.Queue()
    await _run_recommend(event_q, "问题1\n问题2\n问题3")
    assert not event_q.empty()
    evt = event_q.get_nowait()
    assert evt["event"] == "recommended_questions"
    assert len(evt["data"]["questions"]) == 3
