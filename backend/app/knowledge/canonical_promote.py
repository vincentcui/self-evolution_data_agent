"""promote_candidates_to_canonical — 9 分支汇聚 + ns 级锁.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/04-promotion.md
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.knowledge.canonical_audit import write_canonical_audit_log
from app.models import (
    SchemaCanonicalCandidate,
    SchemaCanonicalConflict,
    SchemaCanonicalObject,
)
from app.models.base import local_now
from app.models.git_repo import GitRepo

log = logging.getLogger(__name__)


@dataclass
class PromoteReport:
    promoted_count: int = 0
    conflicted_count: int = 0
    skipped_user_locked: int = 0
    skipped_in_conflict: int = 0
    candidates_processed: int = 0
    duration_seconds: float = 0.0


# 进程内 ns 级锁字典.
#
# 部署假设: 单 worker 模式 (uvicorn --workers 1 / 单 gunicorn worker).
# 多 worker 部署时进程内 asyncio.Lock 无法跨进程互斥, 两个进程同时
# promote 同一 ns 会撞 partial unique index (uq_one_open_conflict_per_field)
# 引发 IntegrityError, 但不会产生脏数据 — partial index 是兜底, 锁是优化.
# 若未来切多 worker, 需在 _process_group 入口加 SQLite advisory lock 行
# 或 PG advisory_lock; 当前不实现以保持简洁.
_ns_locks: dict[int, asyncio.Lock] = {}


def _get_ns_lock(ns_id: int) -> asyncio.Lock:
    """返回 ns 级 asyncio.Lock. 单 worker 假设, 同进程同 event loop.

    测试侧通过 fixture `_reset_ns_locks` autouse 清理本字典, 不再
    在生产代码里探测 lock 内部 _loop 属性.
    """
    if ns_id not in _ns_locks:
        _ns_locks[ns_id] = asyncio.Lock()
    return _ns_locks[ns_id]


async def promote_candidates_to_canonical(
    db: AsyncSession, ns_id: int
) -> PromoteReport:
    """ns-wide 汇聚.

    幂等: 第二次跑无 pending → promoted_count=0.
    锁: 进程内 asyncio.Lock + 超时 settings.promote_lock_timeout_secs.
    """
    start = asyncio.get_running_loop().time()
    report = PromoteReport()
    lock = _get_ns_lock(ns_id)

    try:
        await asyncio.wait_for(lock.acquire(), timeout=settings.promote_lock_timeout_secs)
    except asyncio.TimeoutError:
        log.warning("[promote] ns=%d lock timeout, abort", ns_id)
        return report

    try:
        q = select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.status == "pending",
        )
        rows = list((await db.execute(q)).scalars().all())
        report.candidates_processed = len(rows)

        # 按五元组分组
        groups: dict[tuple, list[SchemaCanonicalCandidate]] = defaultdict(list)
        for c in rows:
            key = (c.db_type, c.database, c.target, c.field_path, c.candidate_kind)
            groups[key].append(c)

        total_groups = len(groups)
        log.info("[promote] ns=%d 开始汇聚: %d candidates, %d groups",
                 ns_id, len(rows), total_groups)

        log_interval = max(1, total_groups // 10)  # 每 10% 输出一次
        for idx, (key, cands) in enumerate(groups.items(), 1):
            await _process_group(db, ns_id, key, cands, report)
            if idx % log_interval == 0 or idx == total_groups:
                log.info("[promote] ns=%d 进度 %d/%d groups (promoted=%d, conflict=%d)",
                         ns_id, idx, total_groups,
                         report.promoted_count, report.conflicted_count)

        report.duration_seconds = asyncio.get_running_loop().time() - start
        log.info("[promote] ns=%d 完成: promoted=%d, conflict=%d, "
                 "skipped_locked=%d, skipped_in_conflict=%d, %.1fs",
                 ns_id, report.promoted_count, report.conflicted_count,
                 report.skipped_user_locked, report.skipped_in_conflict,
                 report.duration_seconds)
        return report
    finally:
        lock.release()


async def promote_single_field(
    db: AsyncSession,
    *,
    ns_id: int,
    db_type: str,
    database: str,
    target: str,
    field_path: str,
    candidate_kind: str,
) -> PromoteReport:
    """单字段 promote (T5/T6 用). 复用 _process_group 核心. 无 ns 级锁."""
    report = PromoteReport()
    cands = list((await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.db_type == db_type,
            SchemaCanonicalCandidate.database == database,
            SchemaCanonicalCandidate.target == target,
            SchemaCanonicalCandidate.field_path == field_path,
            SchemaCanonicalCandidate.candidate_kind == candidate_kind,
            SchemaCanonicalCandidate.status == "pending",
        )
    )).scalars().all())
    report.candidates_processed = len(cands)
    if cands:
        key = (db_type, database, target, field_path, candidate_kind)
        await _process_group(db, ns_id, key, cands, report)
    return report


async def maybe_trigger_promote(db: AsyncSession, ns_id: int) -> PromoteReport | None:
    """检查 ns 下所有 repo 是否已解析完, 若是则触发 promote."""
    from app.models import GitRepo

    repos = (await db.execute(
        select(GitRepo).where(GitRepo.namespace_id == ns_id)
    )).scalars().all()
    if not repos:
        return None
    # 全部 repo 解析完成才触发
    if all(r.parse_status == "parsed" for r in repos):
        return await promote_candidates_to_canonical(db, ns_id)
    return None


# ─── 内部实现 ───────────────────────────────────────────────


async def _process_group(
    db: AsyncSession,
    ns_id: int,
    key: tuple,
    cands: list[SchemaCanonicalCandidate],
    report: PromoteReport,
) -> None:
    db_type, database, target, field_path, kind = key

    # 检查该字段已有 open conflict?
    open_conflict = (await db.execute(
        select(SchemaCanonicalConflict).where(
            SchemaCanonicalConflict.namespace_id == ns_id,
            SchemaCanonicalConflict.db_type == db_type,
            SchemaCanonicalConflict.database == database,
            SchemaCanonicalConflict.target == target,
            SchemaCanonicalConflict.field_path == field_path,
            SchemaCanonicalConflict.candidate_kind == kind,
            SchemaCanonicalConflict.status == "open",
        )
    )).scalar_one_or_none()
    if open_conflict is not None:
        report.skipped_in_conflict += len(cands)
        return

    # 检查该字段是否 user_locked (表级)
    sco = await _get_or_none_canonical(db, ns_id, db_type, database, target)
    if sco is not None and sco.user_locked:
        report.skipped_user_locked += 1
        for c in cands:
            await write_canonical_audit_log(
                db, namespace_id=ns_id, action="skipped_user_locked",
                candidate_id=c.id, canonical_id=sco.id,
                field_path=field_path, reason="field_locked",
            )
        return

    # 9 分支
    n = len(cands)
    if n == 1:
        await _handle_single_candidate(db, ns_id, key, cands[0], sco, report)
    else:
        await _handle_multi_candidates(db, ns_id, key, cands, sco, report)


async def _handle_single_candidate(
    db: AsyncSession, ns_id: int, key: tuple,
    cand: SchemaCanonicalCandidate,
    sco: SchemaCanonicalObject | None,
    report: PromoteReport,
) -> None:
    """N=1 单候选."""
    if cand.confidence_status == "evidence_only":
        return  # 跳过, 等用户 confirm/correct/ignore

    db_type, database, target, field_path, kind = key

    # B1/B9: 检查是否已有 active 候选 (不同 value_hash) → 应走 conflict
    active_cands = list((await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.db_type == db_type,
            SchemaCanonicalCandidate.database == database,
            SchemaCanonicalCandidate.target == target,
            SchemaCanonicalCandidate.field_path == field_path,
            SchemaCanonicalCandidate.candidate_kind == kind,
            SchemaCanonicalCandidate.status == "active",
            SchemaCanonicalCandidate.value_hash != cand.value_hash,
        )
    )).scalars().all())
    if active_cands:
        # 已有不同值的 active 候选 → 走 conflict (设计 §4.6 B1)
        all_cands = active_cands + [cand]
        await _create_conflict(db, ns_id, key, all_cands, "field_value")
        for c in all_cands:
            c.status = "in_conflict"
        report.conflicted_count += 1
        return

    # confidence ∈ {confirmed_by_introspect, confirmed_by_code} → AUTO PROMOTE
    await _apply_to_canonical(db, ns_id, key, cand, sco, "single_source_authoritative")
    report.promoted_count += 1


async def _handle_multi_candidates(
    db: AsyncSession, ns_id: int, key: tuple, cands: list[SchemaCanonicalCandidate],
    sco: SchemaCanonicalObject | None, report: PromoteReport,
) -> None:
    """N≥2 多候选 — 走 equivalence registry 链, 主流程零分支."""
    from app.knowledge.equivalence.registry import applicable_rules
    db_type, _, _, _, kind = key
    if len({c.value_hash for c in cands}) == 1:
        await _apply_to_canonical(db, ns_id, key, cands[0], sco, "multi_source_consistent")
        _finalize_losers(cands, cands[0], "active")
        report.promoted_count += 1
        return
    for rule in applicable_rules(db_type, kind):
        outcome = await _try_rule(rule, cands)
        if outcome is None:
            continue
        winner, reason = outcome
        await _apply_to_canonical(db, ns_id, key, winner, sco, f"{rule.name}:{reason}")
        loser_status = "active" if rule.name == "sample_values_union" else "rejected"
        _finalize_losers(cands, winner, loser_status)
        report.promoted_count += 1
        return
    await _record_conflict(db, ns_id, key, cands, report)


async def _try_rule(rule: Any, cands: list[SchemaCanonicalCandidate]):
    """跑单条 rule, 屏蔽异常, 兼容 sync/async checker. 返 (winner, reason) 或 None."""
    try:
        result = rule.checker(cands)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        log.warning("[promote] checker %s raised: %s", rule.name, exc)
        return None
    return result


async def _record_conflict(
    db: AsyncSession, ns_id: int, key: tuple,
    cands: list[SchemaCanonicalCandidate],
    report: PromoteReport,
) -> None:
    """全部 rule miss → 写 conflict + 标 candidate."""
    await _create_conflict(db, ns_id, key, cands, "field_value")
    for c in cands:
        c.status = "in_conflict"
    report.conflicted_count += 1


def _finalize_losers(
    cands: list[SchemaCanonicalCandidate],
    winner: SchemaCanonicalCandidate,
    loser_status: str,
) -> None:
    """主流程辅助: 把 winner 之外的 candidate 标 active 或 rejected, 同步时间戳."""
    now = datetime.now()
    for c in cands:
        if c.id == winner.id:
            continue
        c.status = loser_status
        if loser_status == "active":
            c.promoted_at = now
        else:
            c.rejected_at = now


async def _get_or_none_canonical(
    db: AsyncSession, ns_id: int, db_type: str, database: str, target: str,
) -> SchemaCanonicalObject | None:
    return (await db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == ns_id,
            SchemaCanonicalObject.db_type == db_type,
            SchemaCanonicalObject.database == database,
            SchemaCanonicalObject.target == target,
        )
    )).scalar_one_or_none()


async def _apply_to_canonical(
    db: AsyncSession, ns_id: int, key: tuple,
    cand: SchemaCanonicalCandidate,
    sco: SchemaCanonicalObject | None,
    reason: str,
) -> None:
    """把 candidate 值写到 SchemaCanonicalObject 对应字段."""
    db_type, database, target, field_path, kind = key
    value = json.loads(cand.candidate_value_json)

    if sco is None:
        sco = SchemaCanonicalObject(
            namespace_id=ns_id, db_type=db_type, database=database, target=target,
            fields_json="[]", indexes_json="[]", description="", purpose_detail="",
            sample_count=0, source="candidate_promote",
            relationships_json="[]", sample_values_json="[]", user_locked=False,
        )
        db.add(sco)
        await db.flush()

    before_snapshot = {
        "fields_json": sco.fields_json,
        "description": sco.description,
        "relationships_json": sco.relationships_json,
    }

    if kind == "table_description":
        sco.description = value.get("description", "")
    elif kind == "field_description":
        # 0. 写结构性字段 (type, sub_fields, nullable, indexed)
        for structural_key in ("type", "nullable", "indexed"):
            if structural_key in value:
                sco.fields_json = _upsert_field_attr(
                    sco.fields_json, field_path, structural_key, value[structural_key]
                )
        if "sub_fields" in value:
            # 递归给 sub_fields 打上 description_confidence
            sub_fields = _stamp_confidence_recursive(
                value["sub_fields"], cand.confidence_status
            )
            sco.fields_json = _upsert_field_attr(
                sco.fields_json, field_path, "sub_fields", sub_fields,
            )

        # 1. 写 description 和 enum_class_hint (若有)
        if "description" in value:
            sco.fields_json = _upsert_field_attr(
                sco.fields_json, field_path, "description", value["description"]
            )
        if "enum_class_hint" in value:
            sco.fields_json = _set_field_attr(
                sco.fields_json, field_path, "enum_class_hint", value["enum_class_hint"]
            )

        # 写 description_confidence (来源可信度)
        sco.fields_json = _upsert_field_attr(
            sco.fields_json, field_path, "description_confidence", cand.confidence_status
        )

        # 2. 尝试绑定 EnumDictionary
        hint = value.get("enum_class_hint")
        if hint:
            from sqlalchemy import select as sa_select

            from app.models.enum_dictionary import EnumDictionary
            enum_dict = (await db.execute(
                sa_select(EnumDictionary).where(
                    EnumDictionary.namespace_id == ns_id,
                    EnumDictionary.enum_class_name == hint,
                )
            )).scalar_one_or_none()
            if enum_dict:
                sco.fields_json = _bind_field_to_enum(
                    sco.fields_json, field_path,
                    enum_ref_id=enum_dict.id,
                    enum_values=json.loads(enum_dict.values_json),
                    enum_source=value.get("enum_source", "code_hint"),
                    enum_match_status="matched",
                )
            else:
                sco.fields_json = _set_field_attr(
                    sco.fields_json, field_path, "enum_match_status", "pending"
                )
        elif value.get("enum_match_status") == "pending":
            sco.fields_json = _set_field_attr(
                sco.fields_json, field_path, "enum_match_status", "pending"
            )
    elif kind == "enum_values":
        sco.fields_json = _upsert_field_attr(
            sco.fields_json, field_path, "enum_values", value.get("enum_values", [])
        )
    elif kind == "relationship":
        # sources from evidence — promote 回填
        ev = json.loads(cand.evidence_sources_json or "[]")
        srcs: list[str] = []
        for s in ev:
            st = s.get("source", "")
            if st in ("code_jpa", "code_mongo", "code_relation"):
                srcs.append("code")
            elif st == "introspect_fk":
                srcs.append("introspect_fk")
        value["sources"] = list(set(srcs))
        sco.relationships_json = _upsert_relationship(sco.relationships_json, value)
    elif kind == "sample_values":
        sco.sample_values_json = json.dumps(
            value.get("sample_values", []), ensure_ascii=False
        )

    sco.updated_at = local_now()
    cand.status = "active"
    cand.promoted_at = local_now()
    await db.flush()

    await write_canonical_audit_log(
        db, namespace_id=ns_id, action="auto_promote",
        candidate_id=cand.id, canonical_id=sco.id,
        field_path=field_path, before=before_snapshot,
        after={"value": value}, reason=reason,
    )


def _stamp_confidence_recursive(sub_fields: list[dict], confidence: str) -> list[dict]:
    """递归给 sub_fields 每层打上 description_confidence."""
    for sf in sub_fields:
        if sf.get("description"):
            sf["description_confidence"] = confidence
        if sf.get("sub_fields"):
            _stamp_confidence_recursive(sf["sub_fields"], confidence)
    return sub_fields


def _upsert_field_attr(fields_json: str, field_path: str, attr: str, val: Any) -> str:
    """在 fields[] 中找到 name==field_path 的条目并设置 attr=val."""
    fields = json.loads(fields_json) if fields_json else []
    found = False
    for f in fields:
        if f.get("name") == field_path:
            f[attr] = val
            found = True
            break
    if not found:
        fields.append({"name": field_path, attr: val})
    return json.dumps(fields, ensure_ascii=False)


def _merge_relationship_into(r: dict, value: dict) -> None:
    """把 value merge 进已存在条目 r — sources 积累 + to_field 非空胜出."""
    existing_to_field = r.get("to_field")
    existing_src = set(r.get("sources", []))
    new_src = set(value.get("sources", []))
    r.update(value)
    r["sources"] = list(existing_src | new_src)
    if not value.get("to_field") and existing_to_field:
        r["to_field"] = existing_to_field


def _upsert_relationship(relationships_json: str, value: dict) -> str:
    """按 5 身份键去重更新.

    dedup key: (from_field, to_db_type, to_database, to_target, relation_type).
    to_field 是可 merge 的 quality signal (非空胜出), 不参与 dedup.
    跨库同名表不误 merge; 同 key 时 merge (sources 积累 + to_field 非空胜出).
    Legacy 条目 (无 to_db_type/to_database) 按 3-key 匹配升级.
    """
    rels = json.loads(relationships_json) if relationships_json else []
    key = (
        value.get("from_field"), value.get("to_db_type"),
        value.get("to_database"), value.get("to_target"),
        value.get("relation_type"),
    )
    # ── 5-key exact match ──
    for r in rels:
        if (
            r.get("from_field") == key[0]
            and r.get("to_db_type") == key[1]
            and r.get("to_database") == key[2]
            and r.get("to_target") == key[3]
            and r.get("relation_type") == key[4]
        ):
            _merge_relationship_into(r, value)
            return json.dumps(rels, ensure_ascii=False)
    # ── legacy fallback: to_db_type/to_database 为空 → 3-key match ──
    for r in rels:
        if not r.get("to_db_type") or not r.get("to_database"):
            if (
                r.get("from_field") == key[0]
                and r.get("to_target") == key[3]
                and r.get("relation_type") == key[4]
            ):
                _merge_relationship_into(r, value)
                return json.dumps(rels, ensure_ascii=False)
    rels.append(value)
    return json.dumps(rels, ensure_ascii=False)


def _set_field_attr(fields_json: str, field_name: str, attr: str, val: Any) -> str:
    """在 fields[] 中找到 name==field_name 的条目并设置 attr=val. 不存在则创建."""
    fields = json.loads(fields_json) if fields_json else []
    for f in fields:
        if f.get("name") == field_name:
            f[attr] = val
            return json.dumps(fields, ensure_ascii=False)
    fields.append({"name": field_name, attr: val})
    return json.dumps(fields, ensure_ascii=False)


def _bind_field_to_enum(
    fields_json: str, field_name: str, *,
    enum_ref_id: int,
    enum_values: list[dict],
    enum_source: str,
    enum_match_status: str,
) -> str:
    """字段批量写入 enum 绑定 4 属性."""
    fields = json.loads(fields_json) if fields_json else []
    found = False
    for f in fields:
        if f.get("name") == field_name:
            f["enum_ref_id"] = enum_ref_id
            f["enum_values"] = enum_values
            f["enum_source"] = enum_source
            f["enum_match_status"] = enum_match_status
            found = True
            break
    if not found:
        fields.append({
            "name": field_name,
            "enum_ref_id": enum_ref_id,
            "enum_values": enum_values,
            "enum_source": enum_source,
            "enum_match_status": enum_match_status,
        })
    return json.dumps(fields, ensure_ascii=False)


def _extract_repo_name(url: str) -> str:
    """从 git URL 提取仓库名: git@x.com:/abc.git → abc, https://x.com/org/abc.git → abc"""
    name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or url


async def _create_conflict(
    db: AsyncSession, ns_id: int, key: tuple,
    cands: list[SchemaCanonicalCandidate], conflict_type: str,
) -> None:
    db_type, database, target, field_path, kind = key

    # 批量查 repo_id → repo name 映射
    repo_ids = [c.repo_id for c in cands if c.repo_id is not None]
    repo_name_map: dict[int, str] = {}
    if repo_ids:
        stmt = select(GitRepo.id, GitRepo.url).where(GitRepo.id.in_(set(repo_ids)))
        for row in (await db.execute(stmt)).all():
            repo_name_map[row.id] = _extract_repo_name(row.url)

    snapshot = [
        {
            "candidate_id": c.id,
            "value": json.loads(c.candidate_value_json),
            "evidence": json.loads(c.evidence_sources_json),
            "confidence_status": c.confidence_status,
            "source": repo_name_map.get(c.repo_id) if c.repo_id else None,
        }
        for c in cands
    ]
    conflict = SchemaCanonicalConflict(
        namespace_id=ns_id, db_type=db_type, database=database, target=target,
        field_path=field_path, candidate_kind=kind,
        conflict_type=conflict_type,
        candidate_ids_json=json.dumps([c.id for c in cands]),
        candidates_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        status="open",
    )
    db.add(conflict)
    await db.flush()
    await write_canonical_audit_log(
        db, namespace_id=ns_id,
        action="conflict_open_diff" if conflict_type == "field_value" else "conflict_open_semantic",
        conflict_id=conflict.id, field_path=field_path,
        after={"candidate_count": len(cands), "conflict_type": conflict_type},
    )
