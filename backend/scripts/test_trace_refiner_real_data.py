"""真实数据回归: 端到端验证 trace_refiner prompt + 机械字段注入闭环.

复现 trace 3ccac428215c7dfbb99e0b31d5d66ff8 (refine endpoint trace_id) 暴露的契约错位:
- terminology 字段名拼错 (rule_text → 应该是 term/primary_collection)
- example 缺 target_collection / query_json
- route_hint 漏 collection_path

闭环: 拉真实 PG agent_trace c815e1ac → refine_traces → allowlist + 机械字段注入 →
parse_payload 校验. 通过 = 三类 KE 全部通过 schema 校验, 不再 reject.

用法 (默认从 PG 拉最近 3 条 status=completed trace):
    cd backend && python -m scripts.test_trace_refiner_real_data
    cd backend && python -m scripts.test_trace_refiner_real_data --trace-id c815e1ac-208c-4b83-b404-c859d6f4f447
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import select

from app.db.metadata import async_session
from app.knowledge.trace_extractor import (
    derive_cost_strategy,
    extract_collections,
    extract_db_context,
    extract_final_pipeline,     # route_hint still uses (type:"execute_plan" wrapper)
    extract_join_fields,        # route_hint still uses ({a,b} shape)
    normalize_query_plan,       # example uses (new)
    extract_join_keys,          # example uses ({from,to} shape)
)
from app.knowledge.trace_refiner import refine_traces
from app.models.agent_trace import AgentTrace
from app.schemas.knowledge_payload import parse_payload


# ════════════════════════════════════════════
#  注入逻辑 — 与 api/agent_traces.py 内联实现保持等价
# ════════════════════════════════════════════

LLM_ALLOWED_FIELDS: dict[str, set[str]] = {
    "terminology": {"term", "primary_collection", "synonyms",
                    "primary_field", "source_collections"},
    "instance_alias": {"alias", "canonical_name", "target_id", "id_field"},
    "route_hint": {"question_pattern", "reason", "avoid_path"},
    "rule": {"rule_text", "rule_kind", "applies_to_collections",
             "priority", "evidence"},
    "example": {"question_pattern", "result_summary",
                "collections", "join_keys", "final_query_plan"},
}


def inject_mechanical_fields(
    entry_type: str, payload: dict[str, Any], tool_trace: list[dict]
) -> dict[str, Any]:
    """allowlist + 机械字段注入. 保持与 agent_traces.refine_traces_endpoint 一致."""
    allowed = LLM_ALLOWED_FIELDS.get(entry_type, set())
    if allowed:
        payload = {k: v for k, v in payload.items() if k in allowed}

    collections = extract_collections(tool_trace)
    db_type, database = extract_db_context(tool_trace)

    if entry_type == "terminology":
        if db_type:
            payload["db_type"] = db_type
        if database:
            payload["primary_database"] = database
        if not payload.get("primary_collection") and collections:
            payload["primary_collection"] = collections[0]
    elif entry_type == "instance_alias":
        if database:
            payload["target_database"] = database
        if not payload.get("target_collection") and collections:
            payload["target_collection"] = collections[0]
    elif entry_type == "example":
        qplan = normalize_query_plan(tool_trace)
        if qplan is not None:
            payload["final_query_plan"] = qplan
        if collections:
            payload["collections"] = collections
        joins = extract_join_keys(qplan)
        if joins:
            payload["join_keys"] = joins
    elif entry_type == "route_hint":
        if collections:
            payload["collection_path"] = collections
        payload["cost_strategy"] = derive_cost_strategy(tool_trace)
        joins = extract_join_fields(extract_final_pipeline(tool_trace))
        if joins:
            payload["join_fields"] = joins
    elif entry_type == "rule":
        if collections:
            payload.setdefault("applies_to_collections", collections)
    return payload


# ════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════

async def load_traces(trace_id: str | None, limit: int) -> list[AgentTrace]:
    async with async_session() as db:
        stmt = select(AgentTrace).where(
            AgentTrace.status.in_(["completed", "refined"])
        )
        if trace_id:
            stmt = stmt.where(AgentTrace.trace_id == trace_id)
        stmt = stmt.order_by(AgentTrace.created_at.desc()).limit(limit)
        return list((await db.execute(stmt)).scalars().all())


def evaluate_proposal(p, tool_trace: list[dict]) -> tuple[bool, str | None, dict]:
    """返回 (是否通过 schema, 错误描述, 注入后 payload)."""
    payload = inject_mechanical_fields(
        p.entry_type, dict(p.payload or {}), tool_trace
    )
    if p.entry_type == "instance_alias":
        # instance_alias 走 TypedDict 校验, 不在 parse_payload 注册
        from app.knowledge.instance_alias_intake import (
            InstanceAliasValidationError, validate_instance_alias_payload,
        )
        try:
            validate_instance_alias_payload(payload)
            return True, None, payload
        except InstanceAliasValidationError as e:
            return False, str(e), payload
    try:
        parse_payload(p.entry_type, payload)
        return True, None, payload
    except Exception as e:
        return False, str(e).splitlines()[0][:200], payload


async def main(trace_id: str | None, limit: int) -> int:
    rows = await load_traces(trace_id, limit)
    if not rows:
        print(f"[FAIL] 没找到 trace (trace_id={trace_id}, limit={limit})")
        return 2

    print(f"[INFO] 加载 {len(rows)} 条 trace")
    for r in rows:
        print(f"  - {r.trace_id} | {r.user_query[:60]}")

    payload = [
        {
            "trace_id": r.trace_id, "user_query": r.user_query,
            "trace_json": r.trace_json, "reflection_log_json": r.reflection_log_json,
        }
        for r in rows
    ]
    print("\n[INFO] 调真实 LLM refine_traces...")
    proposed = await asyncio.to_thread(refine_traces, payload)

    if not proposed:
        print("[FAIL] LLM 返 0 条 proposal — prompt 问题或 trace 内无可提炼知识")
        return 3

    print(f"[INFO] LLM 产出 {len(proposed)} 条 proposal\n")

    trace_by_id = {r.trace_id: r for r in rows}
    by_type: dict[str, list[tuple[bool, str | None]]] = {}
    pass_count = 0
    fail_count = 0

    for p in proposed:
        src = trace_by_id.get(p.source_trace_id) or rows[0]
        try:
            tt = (json.loads(src.trace_json or "{}") or {}).get("tool_trace") or []
        except (json.JSONDecodeError, TypeError):
            tt = []

        ok, err, final_payload = evaluate_proposal(p, tt)
        by_type.setdefault(p.entry_type, []).append((ok, err))
        if ok:
            pass_count += 1
            print(f"  [PASS] {p.entry_type:14s} content={p.content[:60]!r}")
        else:
            fail_count += 1
            print(f"  [FAIL] {p.entry_type:14s} content={p.content[:60]!r}")
            print(f"         err: {err}")
            print(f"         payload: {json.dumps(final_payload, ensure_ascii=False)[:300]}")

    print(f"\n[SUMMARY] 总 {len(proposed)} 条, PASS={pass_count}, FAIL={fail_count}")
    print("[BY TYPE]")
    for et, results in sorted(by_type.items()):
        ok_n = sum(1 for ok, _ in results if ok)
        print(f"  {et:14s}: {ok_n}/{len(results)}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-id", default=None,
                        help="指定 trace_id (默认 c815e1ac-...)")
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()
    tid = args.trace_id or "c815e1ac-208c-4b83-b404-c859d6f4f447"
    sys.exit(asyncio.run(main(tid, args.limit)))
