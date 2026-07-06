"""真 LLM 冷启动行为验收 — list_tables 空时 LLM 的决策 (用户追加的两场景).

真 LLM + 真空库 (A/B 均 0 表), 验:
- 场景1: 单源 A 空 → LLM 建议刷新schema/解析git
- 场景2: A/B 双源无描述, A 空 → 续查 B → 都空 → 建议

@pytest.mark.live: 默认 skip, 跑需 IS_CLAUDE_API_KEY + E2E_MYSQL_A/B_* 凭据.

⚠️ 工具自开 session 绑 metadata_db_url, 故 seed 走真实建源 API (真实 commit 到同库),
   不用 rollback fixture、不 monkeypatch. 见模块顶部说明.
"""
from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.engine.agent_loop import run_agent_loop
from app.engine.agent_loop_dispatcher import build_bound_registry
from app.engine.tools.registry import TOOL_SPECS, build_system_prompt
from app.models import Namespace

pytestmark = pytest.mark.live


def _llm_available() -> bool:
    return bool(os.environ.get('IS_CLAUDE_API_KEY') or os.environ.get('IS_LLM_API_KEY'))


def _src(prefix: str) -> dict | None:
    host = os.environ.get(f"E2E_MYSQL_{prefix}_HOST")
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.environ.get(f"E2E_MYSQL_{prefix}_PORT", "3306")),
        "database": os.environ.get(f"E2E_MYSQL_{prefix}_DB", ""),
        "username": os.environ.get(f"E2E_MYSQL_{prefix}_USER", ""),
        "password": os.environ.get(f"E2E_MYSQL_{prefix}_PASS", ""),
    }


# 建议关键词: LLM 给出"去提取 schema"建议时的特征词 (任一命中即算建议)
_SUGGEST_KEYWORDS = ("刷新", "schema", "解析", "git", "提取", "Git")


def _has_suggestion(text: str) -> bool:
    return any(kw in text for kw in _SUGGEST_KEYWORDS)


def _list_tables_calls(trace: list[dict]) -> list[dict]:
    return [t for t in trace if t.get("name") == "list_tables"]


# 真 LLM 非确定性: 单次运行可能偶发偏离正确行为. 采用多采样多数表决 —
# 跑 _SAMPLES 次, 要求 >= _PASS_MIN 次满足行为契约. 契约本身不放宽 (仍是
# "正确行为"), 只是用统计稳健性吸收单次抖动. 失败时 dump 全部样本 trace 供诊断.
# 调高 _SAMPLES 提升稳定性 (代价是更慢/更贵); 测试专用旋钮, 非运行期 config.
_SAMPLES = int(os.environ.get("E2E_COLDSTART_LLM_SAMPLES", "3"))
_PASS_MIN = _SAMPLES // 2 + 1  # 多数 (3→2, 5→3)


@pytest_asyncio.fixture
async def coldstart_seed():
    """走真实建源 API 填入数据源 (真实 commit 到 metadata_db_url) + teardown 删 ns.

    根因见模块顶部。只 override get_current_user (super_admin), 不 override get_db
    → 端点走真实 async_session, 与 run_agent_loop 工具读同库, 无需 monkeypatch。
    """
    from app.auth import get_current_user
    from app.main import app
    from app.models.user import User

    async def _fake_super_admin():
        return User(id=1, username="admin", role="super_admin", password_hash="x")

    app.dependency_overrides[get_current_user] = _fake_super_admin  # 不 override get_db
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    created_ns_ids: list[int] = []

    async def _seed(sources: list[tuple[dict, str]]) -> Namespace:
        """建临时 ns + 经真实 add_datasource API 建源 (连通才存). sources: [(cfg, description)]."""
        tag = os.urandom(4).hex()
        r = await client.post(
            "/api/namespaces",
            json={"name": f"cold-{tag}", "slug": f"cold-{tag}", "description": "coldstart live"},
        )
        assert r.status_code == 201, r.text
        ns_body = r.json()
        created_ns_ids.append(ns_body["id"])
        for cfg, desc in sources:
            rr = await client.post(
                f"/api/namespaces/{ns_body['id']}/datasources",
                json={
                    "db_type": "mysql", "host": cfg["host"], "port": cfg["port"],
                    "database": cfg["database"], "username": cfg["username"],
                    "password": cfg["password"], "description": desc,
                },
            )
            assert rr.status_code == 201, f"建源失败 (A/B 须真实可连空库): {rr.text}"
        # 轻量 detached Namespace 供 prompt/registry 读 id/slug/name (不连 ORM session)
        return Namespace(id=ns_body["id"], slug=ns_body["slug"], name=ns_body["name"])

    yield _seed

    # teardown: 两段式删 ns (dry_run 取 confirm_token), CASCADE 清 datasources
    for ns_id in created_ns_ids:
        prev = await client.delete(f"/api/namespaces/{ns_id}?dry_run=true")
        url = f"/api/namespaces/{ns_id}?dry_run=false"
        if prev.status_code == 200 and prev.json().get("confirm_required"):
            url += f"&confirm_token={prev.json()['confirm_token']}"
        await client.delete(url)
    await client.aclose()
    app.dependency_overrides.clear()


async def _run_once(ns: Namespace, question: str) -> tuple[set, str]:
    """跑一次 agent loop, 返回 (probed_databases 集合, final_answer).

    工具各自开 async_session (绑 metadata_db_url) → 见 seed 的真实数据.
    build_bound_registry 需一个 db 形参 (db-needing 工具会丢弃它自开), 给个真实 session 即可."""
    from app.db.metadata import async_session
    events: list[dict] = []
    async def emit(e): events.append(e)
    async with async_session() as db:
        bound = build_bound_registry(
            db=db, namespace_id=ns.id, ns_slug=ns.slug,
            trace_id=f"cold-{ns.slug}", sse_emit=emit,
        )
        result = await run_agent_loop(
            trace_id=f"cold-{ns.slug}",
            question=question,
            tools_registry=bound,
            tool_specs=TOOL_SPECS,
            sse_emit=emit,
            user_correction_queue=asyncio.Queue(),
            system_prompt=build_system_prompt(settings=settings, namespace=ns),
        )
    probed = {t["input"].get("database") for t in _list_tables_calls(result.tool_trace)}
    return probed, result.final_answer or ""


@pytest.mark.skipif(not _llm_available(), reason="LLM key 未配置")
@pytest.mark.asyncio
async def test_scenario1_single_source_empty_llm_suggests_refresh(coldstart_seed):
    """场景1: 单源 A 空 → LLM 调 list_tables(A) 后建议刷新schema/git.

    多采样多数表决 (_SAMPLES 次取 >=_PASS_MIN 次), 吸收单次 LLM 抖动."""
    a = _src("A")
    if a is None:
        pytest.skip("E2E_MYSQL_A_* 未配置")
    ns = await coldstart_seed([(a, "风场发电运维库")])  # 单源可有描述

    passed = 0
    failures: list[str] = []
    for i in range(_SAMPLES):
        probed, answer = await _run_once(ns, f"{a['database']} 这个库里有哪些表?")
        # 契约: 调过 list_tables(A) 且最终答案含提取建议
        ok = a["database"] in probed and _has_suggestion(answer)
        if ok:
            passed += 1
        else:
            failures.append(f"#{i}: probed={probed} answer={answer[:120]!r}")
    assert passed >= _PASS_MIN, (
        f"场景1 仅 {passed}/{_SAMPLES} 次满足契约 (需 >={_PASS_MIN}). "
        f"失败样本:\n" + "\n".join(failures)
    )


@pytest.mark.skipif(not _llm_available(), reason="LLM key 未配置")
@pytest.mark.asyncio
async def test_scenario2_two_sources_no_desc_llm_probes_both_then_suggests(coldstart_seed):
    """场景2: A/B 双源无描述, A 空 → 续查 B → 都空 → 建议刷新/git.

    多采样多数表决 (_SAMPLES 次取 >=_PASS_MIN 次), 吸收单次 LLM 抖动."""
    a, b = _src("A"), _src("B")
    if a is None or b is None:
        pytest.skip("E2E_MYSQL_A_* 或 _B_* 未配置")
    # 关键: 两源都不传 description (无语义线索 → 逼 LLM fallback 遍历, 而非语义路由跳过)
    ns = await coldstart_seed([(a, ""), (b, "")])

    q = "帮我看看现在能查到哪些业务表, 有数据的话统计一下"
    passed = 0
    failures: list[str] = []
    for i in range(_SAMPLES):
        probed, answer = await _run_once(ns, q)
        # 契约: 两库都探查过 (A 空续查 B) 且都空后给提取建议
        ok = (a["database"] in probed and b["database"] in probed
              and _has_suggestion(answer))
        if ok:
            passed += 1
        else:
            failures.append(f"#{i}: probed={probed} answer={answer[:120]!r}")
    assert passed >= _PASS_MIN, (
        f"场景2 仅 {passed}/{_SAMPLES} 次满足契约 (需 >={_PASS_MIN}). "
        f"多数样本未续查 B 或未给建议 → system_prompt 'fallback 遍历' 引导不够强, "
        f"回 Task 9 强化措辞 (走 prompt-engineering-2026 skill), 而非放宽契约. "
        f"失败样本:\n" + "\n".join(failures)
    )
