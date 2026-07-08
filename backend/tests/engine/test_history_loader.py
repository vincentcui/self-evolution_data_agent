import json

import pytest

from app.engine.history_loader import build_history_messages
from app.models import QueryHistory
from app.models.namespace import Namespace


async def _mk_ns(db, slug: str = "hist-ns") -> Namespace:
    ns = Namespace(name=slug, slug=slug)
    db.add(ns)
    await db.flush()
    return ns


async def _insert(db, ns_id: int, session_id: str, *, question: str,
                  answer: str, query: str = "", error: str = "") -> None:
    """插一条 QueryHistory(role=assistant, content=用户问题, snapshot.final_answer=回答)."""
    snap = json.dumps({"final_answer": answer}, ensure_ascii=False)
    db.add(QueryHistory(
        namespace_id=ns_id, session_id=session_id, role="assistant",
        content=question, generated_query=query, error=error,
        result_snapshot=snap,
    ))
    await db.flush()


@pytest.mark.asyncio
async def test_empty_session_id_returns_empty(db):
    ns = await _mk_ns(db)
    assert await build_history_messages(db, "", ns.id, 5) == []


@pytest.mark.asyncio
async def test_max_turns_zero_returns_empty(db):
    ns = await _mk_ns(db)
    assert await build_history_messages(db, "s1", ns.id, 0) == []


@pytest.mark.asyncio
async def test_one_turn_returns_user_assistant_pair(db):
    ns = await _mk_ns(db)
    await _insert(db, ns.id, "s1", question="总数?", answer="12345 条",
                  query=json.dumps({"collection": "orders"}))
    msgs = await build_history_messages(db, "s1", ns.id, 5)
    assert len(msgs) == 2
    assert msgs[0] == {"role": "user", "content": "总数?"}
    assert msgs[1]["role"] == "assistant"
    assert "执行的查询" in msgs[1]["content"]
    assert "12345 条" in msgs[1]["content"]


@pytest.mark.asyncio
async def test_three_turns_chronological_order(db):
    """3 轮 → 3 对, 最老→最新正序(id DESC 兜底保序)."""
    ns = await _mk_ns(db)
    await _insert(db, ns.id, "s1", question="Q1", answer="A1")
    await _insert(db, ns.id, "s1", question="Q2", answer="A2")
    await _insert(db, ns.id, "s1", question="Q3", answer="A3")
    msgs = await build_history_messages(db, "s1", ns.id, 5)
    assert len(msgs) == 6
    assert msgs[0]["content"] == "Q1" and msgs[5]["content"] == "A3"


@pytest.mark.asyncio
async def test_cancelled_turn_skipped_no_orphan(db):
    """final_answer 为空(cancelled)→ 跳过整对, 不产生孤儿 user, 交替不破."""
    ns = await _mk_ns(db)
    await _insert(db, ns.id, "s1", question="Q1", answer="A1")
    await _insert(db, ns.id, "s1", question="Q2", answer="", error="cancelled")
    await _insert(db, ns.id, "s1", question="Q3", answer="A3")
    msgs = await build_history_messages(db, "s1", ns.id, 5)
    assert len(msgs) == 4
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["Q1", "Q3"]
    # 严格交替: user/assistant/user/assistant
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]


@pytest.mark.asyncio
async def test_error_turn_with_answer_injected(db):
    """stop_reason 非 end_turn 但 final_answer 非空(如上限进展提示)→ 正常注入."""
    ns = await _mk_ns(db)
    await _insert(db, ns.id, "s1", question="Q1",
                  answer="(已达总轮次上限, 当前进展见结果)")
    msgs = await build_history_messages(db, "s1", ns.id, 5)
    assert len(msgs) == 2
    assert "已达总轮次上限" in msgs[1]["content"]


@pytest.mark.asyncio
async def test_max_turns_truncation(db):
    """max_turns=2 但有 5 轮 → 只取最近 2 轮."""
    ns = await _mk_ns(db)
    for i in range(1, 6):
        await _insert(db, ns.id, "s1", question=f"Q{i}", answer=f"A{i}")
    msgs = await build_history_messages(db, "s1", ns.id, 2)
    assert len(msgs) == 4
    assert msgs[0]["content"] == "Q4" and msgs[3]["content"] == "A5"


@pytest.mark.asyncio
async def test_malformed_snapshot_skipped(db):
    """result_snapshot 畸形 JSON → 跳过该轮, 其他轮正常."""
    ns = await _mk_ns(db)
    await _insert(db, ns.id, "s1", question="Q1", answer="A1")
    db.add(QueryHistory(
        namespace_id=ns.id, session_id="s1", role="assistant",
        content="Q2", generated_query="", error="",
        result_snapshot="{not valid json",
    ))
    await db.flush()
    msgs = await build_history_messages(db, "s1", ns.id, 5)
    assert len(msgs) == 2
    assert msgs[0]["content"] == "Q1"


@pytest.mark.asyncio
async def test_empty_generated_query_bare_answer(db):
    """generated_query 为空 → assistant 消息裸放 final_answer, 无'执行的查询'段."""
    ns = await _mk_ns(db)
    await _insert(db, ns.id, "s1", question="Q1", answer="裸回答", query="")
    msgs = await build_history_messages(db, "s1", ns.id, 5)
    assert "执行的查询" not in msgs[1]["content"]
    assert msgs[1]["content"] == "裸回答"


@pytest.mark.asyncio
async def test_ns_isolation(db):
    """不同 namespace 同 session_id 不串读."""
    ns1 = await _mk_ns(db, "ns-a")
    ns2 = await _mk_ns(db, "ns-b")
    await _insert(db, ns1.id, "s1", question="Q-a", answer="A-a")
    await _insert(db, ns2.id, "s1", question="Q-b", answer="A-b")
    msgs = await build_history_messages(db, "s1", ns1.id, 5)
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["Q-a"]


@pytest.mark.asyncio
async def test_db_exception_returns_empty(monkeypatch, db):
    """DB 异常 → 返回 [] 不抛(降级)."""
    ns = await _mk_ns(db)

    async def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "execute", boom)
    assert await build_history_messages(db, "s1", ns.id, 5) == []
