"""Stage 2 抓手 E — agent_traces API 测试.

使用 admin_client fixture (conftest.py): ASGI client + fake admin + SAVEPOINT rollback.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import AgentTrace


@pytest.mark.asyncio
async def test_list_traces_paginated(db, admin_client):
    """GET /api/agent-traces 分页返回."""
    db.add_all([
        AgentTrace(
            trace_id=f"t-list-{i}",
            namespace_id=None,
            user_query=f"q{i}",
            trace_json="{}",
            status="completed",
        )
        for i in range(5)
    ])
    await db.commit()

    resp = await admin_client.get("/api/agent-traces", params={"size": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3


@pytest.mark.asyncio
async def test_refine_creates_proposed_ke(db, admin_client):
    """POST /api/agent-traces/refine — mock refine_traces 返回 1 条提案."""
    from app.knowledge.trace_refiner import ProposedKE

    tr = AgentTrace(
        trace_id="refine-test-1",
        namespace_id=None,
        user_query="本月活跃用户",
        status="completed",
        trace_json='{"tool_trace": [{"name": "lookup_knowledge"}]}',
    )
    db.add(tr)
    await db.commit()

    fake_results = [ProposedKE(
        entry_type="rule",
        content="活跃用户指 30 天内有过登录的用户",
        payload={"rule_text": "last_login >= now-30d"},
        evidence={"trace_ids": ["refine-test-1"], "reasoning": "trace 内..."},
    )]

    with patch("app.knowledge.trace_refiner.refine_traces", return_value=fake_results):
        resp = await admin_client.post(
            "/api/agent-traces/refine",
            json={"trace_ids": ["refine-test-1"]},
        )

    assert resp.status_code == 200
    out = resp.json()
    assert out["proposed_count"] == 1
    assert len(out["proposed_ke_ids"]) == 1

    # trace 应被标 refined
    await db.refresh(tr)
    assert tr.status == "refined"


@pytest.mark.asyncio
async def test_refine_rejects_already_refined(db, admin_client):
    """POST /api/agent-traces/refine — status=refined 的 trace 被过滤, 返回 0."""
    tr = AgentTrace(
        trace_id="refine-test-2",
        namespace_id=None,
        user_query="x",
        status="refined",
        trace_json="{}",
    )
    db.add(tr)
    await db.commit()

    resp = await admin_client.post(
        "/api/agent-traces/refine",
        json={"trace_ids": ["refine-test-2"]},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["proposed_count"] == 0


@pytest.mark.asyncio
async def test_refine_batch_size_limit(admin_client):
    """POST /api/agent-traces/refine — 超过 batch_max 返回 422."""
    # 默认 agent_trace_refine_batch_max = 50, 发 51 个 id
    ids = [f"t-{i}" for i in range(51)]
    resp = await admin_client.post(
        "/api/agent-traces/refine",
        json={"trace_ids": ids},
    )
    assert resp.status_code == 422


# ════════════════════════════════════════════
#  Regression: trace_refiner 必须遵守抓手 D + 唯一约束
#  事故 trace ac843ba4 (2026-05-26 16:59): LLM 提案 c_orders terminology
#  撞 uq_terminology_anchor (partial unique index, ns + collection + database
#  + db_type WHERE entry_type='terminology' AND is_superseded=false) → 500.
#  根因: agent_traces.refine_traces_endpoint 直接 KnowledgeEntry() + db.add,
#  绕过 engine.tools.knowledge_tools.save_knowledge — 既没走抓手 D 邻居检索 +
#  detect_relations 钩子, 也没接 PG ON CONFLICT 兜底.
#
#  fixture 借 test_amem_approve_evolution.py 同款 function-scoped engine,
#  避开 conftest session-scoped _engine 在 admin_client + httpx 组合下的
#  event_loop 漂移 (现象层修复, 哲学层应统一所有 API 测试 fixture).
# ════════════════════════════════════════════

import json as _json
import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.base import Base

_TEST_DB_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


@pytest_asyncio.fixture
async def fn_engine():
    eng = create_async_engine(_TEST_DB_URL, echo=False)

    @event.listens_for(eng.sync_engine, "connect")
    def _set_tz(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("SET timezone = 'Asia/Shanghai'")
        cur.close()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def fn_session(fn_engine):
    async with fn_engine.connect() as conn:
        trans = await conn.begin()
        await conn.begin_nested()
        sess = AsyncSession(bind=conn, expire_on_commit=False)

        @event.listens_for(sess.sync_session, "after_transaction_end")
        def _restart(sync_session, transaction):
            if transaction.nested and not transaction._parent.nested:
                sync_session.begin_nested()

        yield sess
        await sess.close()
        await trans.rollback()


@pytest_asyncio.fixture
async def fn_admin_client(fn_session):
    from app.auth import get_current_user
    from app.db.metadata import get_db
    from app.main import app
    from app.models.user import User

    async def _fake_admin():
        return User(id=1, username="admin", role="super_admin", password_hash="x")

    async def _fake_db():
        yield fn_session

    app.dependency_overrides[get_current_user] = _fake_admin
    app.dependency_overrides[get_current_user] = _fake_admin
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_refine_collides_with_terminology_unique_index(fn_session, fn_admin_client):
    """复现 500: ns 内已存在 active terminology, refine 提案撞 partial unique index."""
    from app.knowledge.trace_refiner import ProposedKE
    from app.models.knowledge_entry import KnowledgeEntry
    from app.models.namespace import Namespace

    ns = Namespace(name="t-collide", slug="t-collide", description="")
    fn_session.add(ns); await fn_session.flush()

    # ── 已存在一条 active terminology, 锚定 (ns, c_orders, db_shop, mongodb) ──
    existing = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="订单",
        tier="normal",
        status="canonical",
        is_superseded=False,
        source="code_extract",
        payload=_json.dumps({
            "term": "订单",
            "primary_collection": "c_orders",
            "primary_database": "db_shop",
            "db_type": "mongodb",
        }, ensure_ascii=False),
    )
    fn_session.add(existing)

    tr = AgentTrace(
        trace_id="refine-collide-1",
        namespace_id=ns.id,
        user_query="本月订单数",
        status="completed",
        trace_json='{"tool_trace": []}',
    )
    fn_session.add(tr)
    await fn_session.commit()

    # LLM 提了一条与 existing 锚定字段完全相同的 terminology
    fake_results = [ProposedKE(
        entry_type="terminology",
        content="订单",
        payload={
            "term": "订单",
            "primary_collection": "c_orders",
            "primary_database": "db_shop",
            "db_type": "mongodb",
        },
        evidence={"trace_ids": ["refine-collide-1"], "reasoning": "trace 内 ..."},
    )]

    with patch("app.knowledge.trace_refiner.refine_traces", return_value=fake_results):
        resp = await fn_admin_client.post(
            "/api/agent-traces/refine",
            json={"trace_ids": ["refine-collide-1"]},
        )

    # 期望: 不再 500. batch 应该完成, 撞约束的提案被静默跳过 (走抓手 D
    # 演化路径或前置查重路径), trace 仍标 refined.
    assert resp.status_code != 500, (
        f"refine 不应抛 500. 实际 status={resp.status_code} "
        f"body={resp.text[:300]}"
    )
    assert resp.status_code == 200
    out = resp.json()
    # 撞约束的 terminology 不进 active 池 — proposed_count 可以是 0 (前置查重跳过)
    # 或 1 但走演化路径让 LLM detect_relations=equivalent (待人工 approve 时 supersede 老条目).
    # 任一行为都比 500 强.
    assert out["proposed_count"] in (0, 1)


@pytest.mark.asyncio
async def test_refine_writes_related_entry_ids_per_amem(fn_session, fn_admin_client):
    """抓手 D 强约束: refine 路径产出的 example/rule/route_hint 必须经过
    detect_relations, 写 related_entry_ids_json (与 agent save_knowledge 等价).

    spec 02-stage2-pull-reinforcement.md 写入治理表第 4 行:
      | trace 提炼 | refine_traces_endpoint | 5 类 LLM 提案 | proposed |
    与第 3 行 agent save_knowledge 同列, 都应触发抓手 D 演化.
    """
    from app.knowledge.trace_refiner import ProposedKE
    from app.models.knowledge_entry import KnowledgeEntry
    from app.models.namespace import Namespace

    ns = Namespace(name="t-amem", slug="t-amem", description="")
    fn_session.add(ns); await fn_session.flush()

    tr = AgentTrace(
        trace_id="refine-amem-1",
        namespace_id=ns.id,
        user_query="过去 7 天活跃用户",
        status="completed",
        trace_json='{"tool_trace": []}',
    )
    fn_session.add(tr)
    await fn_session.commit()

    fake_results = [ProposedKE(
        entry_type="rule",
        content="活跃用户指过去 N 天内有过登录的用户",
        payload={"rule_text": "last_login >= now-{N}d"},
        evidence={"trace_ids": ["refine-amem-1"], "reasoning": "trace 内 ..."},
    )]

    # 同时 patch detect_relations 为 spy, 验证它被调用 (而不仅是产出 KE)
    with (
        patch("app.knowledge.trace_refiner.refine_traces", return_value=fake_results),
        patch(
            "app.knowledge.relations.detect_relations", return_value=[]
        ) as detect_spy,
    ):
        resp = await fn_admin_client.post(
            "/api/agent-traces/refine",
            json={"trace_ids": ["refine-amem-1"]},
        )

    assert resp.status_code == 200
    out = resp.json()
    assert out["proposed_count"] == 1

    # 期望: refine 路径触发了 detect_relations (抓手 D)
    assert detect_spy.called, (
        "trace 提炼路径应调 detect_relations (抓手 D), "
        "实际未调用 — 说明 refine_traces_endpoint 绕过了 save_knowledge"
    )

    # 期望: 产出的 KE 含 related_entry_ids_json 字段 (即使为空 list, 也证明走过 D 钩子)
    new_ke_id = out["proposed_ke_ids"][0]
    ke = (await fn_session.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.id == new_ke_id)
    )).scalar_one()
    assert ke.related_entry_ids_json is not None


@pytest.mark.asyncio
async def test_refine_route_hint_passes_schema_gate(fn_session, fn_admin_client):
    """Phase 1+2: trace_refiner 提案合法 route_hint payload → 通过闸门入库, 字段不变形.

    验证 allowlist 过滤 + trace_extractor 补机械字段 + schema gate 三层链路.
    """
    from app.knowledge.trace_refiner import ProposedKE
    from app.models.knowledge_entry import KnowledgeEntry
    from app.models.namespace import Namespace

    ns = Namespace(name="t-gate-pass", slug="t-gate-pass", description="")
    fn_session.add(ns)
    await fn_session.flush()

    # trace_json 含真实 tool_trace: fetch_schema + execute_query(mode=count)
    trace_json = {
        "tool_trace": [
            {"name": "fetch_schema", "input": {"target": "c_orders"}, "output": {}},
            {"name": "execute_query",
             "input": {"target": "c_orders", "mode": "count"},
             "output": {"count": 5}},
        ]
    }
    tr = AgentTrace(
        trace_id="refine-gate-pass-1",
        namespace_id=ns.id,
        user_query="订单数",
        status="completed",
        trace_json=_json.dumps(trace_json, ensure_ascii=False),
    )
    fn_session.add(tr)
    await fn_session.commit()

    # LLM 仅产语义字段 (Phase 2 prompt 禁止产机械字段)
    fake_results = [ProposedKE(
        entry_type="route_hint",
        content="订单统计路由",
        payload={
            "question_pattern": "订单数量统计",
            "reason": "单集合统计",
        },
        evidence={"trace_ids": ["refine-gate-pass-1"]},
        source_trace_id="refine-gate-pass-1",
    )]

    with patch("app.knowledge.trace_refiner.refine_traces", return_value=fake_results):
        resp = await fn_admin_client.post(
            "/api/agent-traces/refine",
            json={"trace_ids": ["refine-gate-pass-1"]},
        )

    assert resp.status_code == 200
    out = resp.json()
    assert out["proposed_count"] == 1
    new_id = out["proposed_ke_ids"][0]

    ke = (await fn_session.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.id == new_id)
    )).scalar_one()
    payload = _json.loads(ke.payload)

    # 机械字段被 trace_extractor 补全
    assert payload["collection_path"] == ["c_orders"]
    assert payload["cost_strategy"] == "count_only_first"
    # 语义字段 LLM 产
    assert payload["question_pattern"] == "订单数量统计"
    assert payload["reason"] == "单集合统计"
    # 无 LLM 自由发挥 extra 字段 (闸门拒)
    assert "cross_database_strategy" not in payload
    assert "route" not in payload


@pytest.mark.asyncio
async def test_detail_returns_tool_trace_compact(db, admin_client):
    """GET /api/agent-traces/{id} 响应含 tool_trace_compact, 复用 compact_tool_call 投影."""
    tr = AgentTrace(
        trace_id="compact-1",
        namespace_id=None,
        user_query="订单数",
        status="completed",
        trace_json=_json.dumps({
            "tool_trace": [
                {"name": "fetch_schema", "input": {"target": "c_orders"},
                 "output": {"fields": [{"name": "oid"}]}},
                {"name": "execute_query",
                 "input": {"target": "c_orders", "mode": "count", "query": {"filter": {}}},
                 "output": {"count": 7}},
            ]
        }, ensure_ascii=False),
    )
    db.add(tr)
    await db.commit()

    resp = await admin_client.get("/api/agent-traces/compact-1")
    assert resp.status_code == 200
    data = resp.json()
    compact = data["tool_trace_compact"]
    assert isinstance(compact, list) and len(compact) == 2
    assert compact[0]["step"] == 0 and compact[0]["tool"] == "fetch_schema"
    assert compact[0]["target"] == "c_orders"
    assert compact[0]["schema_field_count"] == 1
    assert compact[1]["mode"] == "count"
    assert compact[1]["count_returned"] == 7
    # trace_json / reflection_log_json 原样透传不变
    assert "trace_json" in data and "reflection_log_json" in data


@pytest.mark.asyncio
async def test_detail_compact_empty_when_trace_json_garbage(db, admin_client):
    """trace_json 为空/非法 JSON 时 tool_trace_compact=[], 不抛异常."""
    tr = AgentTrace(
        trace_id="compact-2", namespace_id=None, user_query="x",
        status="completed", trace_json="not-json{",
    )
    db.add(tr)
    await db.commit()
    resp = await admin_client.get("/api/agent-traces/compact-2")
    assert resp.status_code == 200
    assert resp.json()["tool_trace_compact"] == []


@pytest.mark.asyncio
async def test_detail_compact_tolerates_non_dict_elements(db, admin_client):
    """trace_json.tool_trace 含非字典元素 (null/"bad") 时 compact 不抛, 跳过为空 tool 行.

    根因: compact_tool_call 开头 call.get("name","") 对非字典抛 AttributeError,
    违背其 docstring "不会抛异常". 历史脏数据/并发截断可能产出非字典元素.
    """
    tr = AgentTrace(
        trace_id="compact-3", namespace_id=None, user_query="x",
        status="completed",
        trace_json=_json.dumps({
            "tool_trace": [
                None,
                "bad",
                {"name": "fetch_schema", "input": {"target": "c_orders"}, "output": {}},
            ]
        }, ensure_ascii=False),
    )
    db.add(tr)
    await db.commit()
    resp = await admin_client.get("/api/agent-traces/compact-3")
    assert resp.status_code == 200
    compact = resp.json()["tool_trace_compact"]
    assert len(compact) == 3
    assert compact[0]["tool"] == "" and compact[1]["tool"] == ""   # 非字典 → 空 tool 行
    assert compact[2]["tool"] == "fetch_schema"


@pytest.mark.asyncio
async def test_list_trace_damaged_signal(db, admin_client):
    """trace_json 损坏 (非法 JSON) → 列表返 trace_damaged=true, tool_call_count=null."""
    db.add(AgentTrace(
        trace_id="trace-damaged-list",
        namespace_id=None,
        user_query="damaged",
        trace_json='{"tool_trace": [{"name":"execute_query","output":{"rows":[{"id":1},',  # 腰斩
        reflection_log_json="[]",
        status="completed",
    ))
    await db.commit()
    resp = await admin_client.get("/api/agent-traces", params={"size": 200})
    assert resp.status_code == 200
    items = resp.json()
    item = next(i for i in items if i["trace_id"] == "trace-damaged-list")
    assert item["trace_damaged"] is True
    assert item["tool_call_count"] is None


@pytest.mark.asyncio
async def test_detail_trace_damaged_signal(db, admin_client):
    """trace_json 损坏 → 详情返 trace_damaged=true, tool_trace_compact=[]."""
    db.add(AgentTrace(
        trace_id="trace-damaged-detail",
        namespace_id=None,
        user_query="damaged",
        trace_json='{"tool_trace": [{"name":"list_databases",',  # 腰斩
        reflection_log_json="[]",
        status="completed",
    ))
    await db.commit()
    resp = await admin_client.get("/api/agent-traces/trace-damaged-detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trace_damaged"] is True
    assert body["tool_trace_compact"] == []
