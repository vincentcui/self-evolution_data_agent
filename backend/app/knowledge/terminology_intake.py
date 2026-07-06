"""Phase 1b Task 1.3 — terminology 通道统一写入 helper.

# ════════════════════════════════════════════════════════════════
#  设计契约
# ════════════════════════════════════════════════════════════════
# 3 通道 (schema / manual / agent_learn) 共用同一闸门:
#
#   1. Schema 校验 (TerminologyPayload Pydantic, 失败 → None, 不写库)
#   2. db_type 一致性 (ns 下 primary_database 实际类型 ≠ payload.db_type → None)
#   3. 唯一键查重 (active 行 = is_superseded=False, 命中 active 三元组)
#   4a. 不存在 → 新建 KE(status=proposed)
#         + audit_log: source=schema → "auto_generate", 其余 → "propose"
#   4b. 存在 + 双向同义命中 (existing_lex ∩ candidate_lex ≠ ∅) → 合并 synonyms
#         + audit_log action="merge", diff_json 含 shared_terms
#   4c. 存在 + 双向同义未命中 → 写 TerminologyConflict, 不写 audit_log, return None
#
# 调用方管 commit. helper 内部仅 db.flush() 让 PK/FK 落定.
"""

import json
import logging

from pydantic import ValidationError
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry, KnowledgeSource
from app.models.namespace import DataSource
from app.models.terminology_conflict import TerminologyConflict
from app.schemas.knowledge_payload import TerminologyPayload

log = logging.getLogger(__name__)



# ════════════════════════════════════════════════════════════════
#  内部工具
# ════════════════════════════════════════════════════════════════
def _diagnose_merge_reason(existing_lex: set[str], candidate_lex: set[str]) -> str:
    return f"shared_terms={sorted(existing_lex & candidate_lex)}"


async def _resolve_db_type(db: AsyncSession, ns_id: int, database: str) -> str | None:
    ds = (await db.execute(
        select(DataSource).where(
            DataSource.namespace_id == ns_id,
            DataSource.database == database,
        )
    )).scalar_one_or_none()
    return ds.db_type if ds else None


# ════════════════════════════════════════════════════════════════
#  Step 1 — schema 校验
# ════════════════════════════════════════════════════════════════
async def _validate_payload(
    payload_dict: dict, source: KnowledgeSource,
) -> TerminologyPayload | None:
    try:
        return TerminologyPayload(**payload_dict)
    except ValidationError as e:
        log.warning(
            "[terminology_intake] schema validation failed source=%s: %s",
            source, str(e)[:300],
        )
        return None


# ════════════════════════════════════════════════════════════════
#  Step 2 — db_type 一致性
# ════════════════════════════════════════════════════════════════
async def _check_db_type(
    db: AsyncSession, ns_id: int, parsed: TerminologyPayload,
) -> bool:
    actual_db_type = await _resolve_db_type(db, ns_id, parsed.primary_database)
    if actual_db_type is None:
        log.warning(
            "[terminology_intake] no datasource for db=%s in ns=%d",
            parsed.primary_database, ns_id,
        )
        return False
    if actual_db_type != parsed.db_type:
        log.warning(
            "[terminology_intake] db_type mismatch ns=%d db=%s payload=%s actual=%s",
            ns_id, parsed.primary_database, parsed.db_type, actual_db_type,
        )
        return False
    return True


# ════════════════════════════════════════════════════════════════
#  Step 3 — 唯一键查重 (active 三元组)
# ════════════════════════════════════════════════════════════════
async def _find_active_duplicate(
    db: AsyncSession, ns_id: int, parsed: TerminologyPayload,
) -> KnowledgeEntry | None:
    return (await db.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "terminology",
            KnowledgeEntry.is_superseded.is_(False),
            cast(KnowledgeEntry.payload, JSONB)["primary_collection"].astext
                == parsed.primary_collection,
            cast(KnowledgeEntry.payload, JSONB)["primary_database"].astext
                == parsed.primary_database,
            cast(KnowledgeEntry.payload, JSONB)["db_type"].astext == parsed.db_type,
        )
    )).scalar_one_or_none()


# ════════════════════════════════════════════════════════════════
#  Step 4a — 新建 (status=proposed) + audit_log
# ════════════════════════════════════════════════════════════════
async def _create_proposed(
    db: AsyncSession,
    ns_id: int,
    parsed: TerminologyPayload,
    source: KnowledgeSource,
    repo_id: int | None,
    raw_input: str,
    evidence: dict | None,
) -> KnowledgeEntry:
    ke = KnowledgeEntry(
        namespace_id=ns_id,
        entry_type="terminology",
        source=source,
        status="proposed",
        is_superseded=False,
        payload=parsed.model_dump_json(),
        content=parsed.term,
        raw_input=raw_input,
        evidence_json=json.dumps(evidence or {}),
        repo_id=repo_id,
    )
    db.add(ke)
    await db.flush()
    action = "auto_generate" if source == "schema" else "propose"
    db.add(KnowledgeAuditLog(
        entry_id=ke.id,
        actor_id=None,
        action=action,
        from_status=None,
        to_status="proposed",
        reason=f"source={source}",
    ))
    await db.flush()
    return ke


# ════════════════════════════════════════════════════════════════
#  Step 4b — 合并 synonyms (canonical 保护 + audit_log merge)
# ════════════════════════════════════════════════════════════════
async def _merge_or_skip(
    db: AsyncSession,
    existing: KnowledgeEntry,
    existing_payload: TerminologyPayload,
    parsed: TerminologyPayload,
    source: KnowledgeSource,
) -> KnowledgeEntry | None:
    """canonical 一律不合并 (含所有来源) — 返 None 让 caller 落 conflict.

    非 canonical (proposed/superseded/rejected) 走原合并路径.

    设计契约 (spec 2026-05-21-git-source-full-purge 决策 2):
        自动合并 = 系统替人做决策, 一旦合错回滚成本高.
        走 conflict 工单 = 暴露分歧, 人来定; resolved 工单成本低于错误自动合并.
    """
    if existing.status == "canonical":
        log.debug(
            "[terminology_intake] skip merge: canonical id=%d source=%s"
            " — route to conflict",
            existing.id, source,
        )
        return None

    existing_lex = {existing_payload.term, *existing_payload.synonyms}
    candidate_lex = {parsed.term, *parsed.synonyms}
    # 合并 synonyms — 去重并剔除 existing.term 自身, 不动 existing.term
    merged = list(dict.fromkeys(
        existing_payload.synonyms + [parsed.term] + parsed.synonyms
    ))
    merged = [s for s in merged if s != existing_payload.term]
    new_payload = existing_payload.model_copy(update={"synonyms": merged})
    existing.payload = new_payload.model_dump_json()
    db.add(KnowledgeAuditLog(
        entry_id=existing.id,
        actor_id=None,
        action="merge",
        from_status=existing.status,
        to_status=existing.status,
        reason=f"source={source}",
        diff_json=json.dumps({
            "before": {"synonyms": existing_payload.synonyms},
            "after": {"synonyms": merged},
            "candidate_term": parsed.term,
            "merge_reason": _diagnose_merge_reason(existing_lex, candidate_lex),
        }),
    ))
    await db.flush()
    return existing


# ════════════════════════════════════════════════════════════════
#  Step 4c — 不同实体落 conflict 表 (不写 audit_log)
# ════════════════════════════════════════════════════════════════
async def _record_conflict(
    db: AsyncSession,
    ns_id: int,
    existing: KnowledgeEntry,
    existing_payload: TerminologyPayload,
    parsed: TerminologyPayload,
    source: KnowledgeSource,
    repo_id: int | None,
) -> None:
    conflict = TerminologyConflict(
        namespace_id=ns_id,
        existing_entry_id=existing.id,
        candidate_payload=parsed.model_dump_json(),
        candidate_source=source,
        candidate_repo_id=repo_id,
        status="open",
    )
    db.add(conflict)
    await db.flush()
    log.info(
        "[terminology_intake] conflict id=%d existing_term=%s candidate_term=%s",
        conflict.id, existing_payload.term, parsed.term,
    )


# ════════════════════════════════════════════════════════════════
#  主入口 — 5 通道统一写入闸门
# ════════════════════════════════════════════════════════════════
async def upsert_terminology_with_validation(
    db: AsyncSession,
    *,
    ns_id: int,
    payload_dict: dict,
    source: KnowledgeSource,
    repo_id: int | None = None,
    raw_input: str = "",
    evidence: dict | None = None,
) -> KnowledgeEntry | None:
    """5 通道统一写入闸门. 调用方管事务边界 (helper 仅 flush 不 commit).

    返回:
      KnowledgeEntry — 新建 / 命中合并的活跃行
      None           — schema 失败 / db_type 不匹配 / database 不存在 / 冲突落表
    """
    parsed = await _validate_payload(payload_dict, source)
    if parsed is None:
        return None
    if not await _check_db_type(db, ns_id, parsed):
        return None

    existing = await _find_active_duplicate(db, ns_id, parsed)
    if existing is None:
        return await _create_proposed(
            db, ns_id, parsed, source, repo_id, raw_input, evidence,
        )

    existing_payload = TerminologyPayload(**json.loads(existing.payload))
    existing_lex = {existing_payload.term, *existing_payload.synonyms}
    candidate_lex = {parsed.term, *parsed.synonyms}
    if existing_lex & candidate_lex:
        ke = await _merge_or_skip(db, existing, existing_payload, parsed, source)
        if ke is not None:
            return ke
        # canonical 拒绝合并 → 落 conflict
    log.warning(
        "[terminology_intake] term=%r 冲突落 conflict 表，未写 KE (source=%s)",
        parsed.term, source,
    )
    await _record_conflict(db, ns_id, existing, existing_payload, parsed, source, repo_id)
    return None
