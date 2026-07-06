"""extraction_writer — 代码解析产物写入 candidate 层 + KnowledgeEntry.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/03-extraction.md §3.7

两个入口:
- write_canonical_candidates_from_parse: schema 候选 (JPA/Mongo/enum/relationship)
- extract_and_write_knowledge: 知识条目 (example/rule/terminology)
"""
from __future__ import annotations

import json
import logging
import re as _re
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.metadata import async_session
from app.knowledge.canonical_candidate import write_canonical_candidate
from app.models import KnowledgeEntry
from app.schemas.knowledge_payload import RulePayload

if TYPE_CHECKING:
    from app.knowledge.explain_gate import ExplainGate

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
#  _build_field_payload_for_candidate
# ════════════════════════════════════════════════════════════════


def _build_field_payload_for_candidate(field: dict) -> dict:
    """把 enum_extractor 写入的 _enum_* 内部标记翻译为正式 candidate.payload 字段.

    翻译规则:
    - 所有 _ 前缀 key 被剥离
    - _enum_class_name → enum_class_hint
    - _enum_source → enum_source
    - _enum_match_status → enum_match_status
    """
    payload: dict = {k: v for k, v in field.items() if not k.startswith("_")}
    if field.get("_enum_class_name"):
        payload["enum_class_hint"] = field["_enum_class_name"]
    if field.get("_enum_source"):
        payload["enum_source"] = field["_enum_source"]
    if field.get("_enum_match_status"):
        payload["enum_match_status"] = field["_enum_match_status"]
    return payload


# ════════════════════════════════════════════════════════════════
#  write_canonical_candidates_from_parse
# ════════════════════════════════════════════════════════════════


async def write_canonical_candidates_from_parse(
    *,
    namespace_id: int,
    repo_id: int,
    jpa_entities: list[dict],
    mongo_documents: list[dict],
    enum_classes: list[dict],
    where_evidence: list[dict],
    coll_to_db: dict[str, tuple[str, str]] | None = None,
    repo_name: str = "",
) -> int:
    """Write schema canonical candidates from code parse results.

    每个 target (table/collection) 独立事务, 消除并发 worker 长事务竞态.

    Converts jpa_entities/mongo_documents/enum_classes into
    SchemaCanonicalCandidate rows via write_canonical_candidate().
    relationship candidate 由 entity writer 内联产生 — 与 field 共享
    同一 database gate, 防幽灵 SCO.

    Args:
        coll_to_db: collection→database 反查表 (训练时实时连接 DataSource 构建).
            用于补全 LLM 解析结果中缺失的 database 字段.

    Returns total candidates written.
    """
    total = 0

    # ── JPA entities — 每个 entity 一个事务 (含 field + relationship) ──
    for entity in jpa_entities:
        async with async_session() as db:
            total += await _write_jpa_entity_candidates(
                db, namespace_id, repo_id, entity, coll_to_db,
                repo_name=repo_name,
            )
            await db.commit()

    # ── Mongo documents — 每个 collection 一个事务 (含 field + relationship) ──
    for doc in mongo_documents:
        async with async_session() as db:
            total += await _write_mongo_document_candidates(
                db, namespace_id, repo_id, doc, coll_to_db,
                repo_name=repo_name,
            )
            await db.commit()

    # ── Enum classes — 每个 enum 一个事务 ──
    for enum_cls in enum_classes:
        async with async_session() as db:
            total += await _write_enum_class_candidates(
                db, namespace_id, repo_id, enum_cls,
            )
            await db.commit()

    log.info(
        "[%s] candidates written: %d (jpa=%d mongo=%d enum=%d)",
        repo_name, total, len(jpa_entities), len(mongo_documents),
        len(enum_classes),
    )
    return total


async def _write_jpa_entity_candidates(
    db: AsyncSession, namespace_id: int, repo_id: int, entity: dict,
    coll_to_db: dict[str, tuple[str, str]] | None = None,
    repo_name: str = "",
) -> int:
    """Write table_description + field_description candidates for a JPA entity."""
    count = 0
    table_name = entity.get("table_name") or entity.get("table") or ""
    if not table_name:
        return 0

    db_type = "mysql"
    database = entity.get("database") or ""
    # 反查: JPA entity 通常不带 database, 从 DataSource snapshot 补全
    if not database and coll_to_db and table_name in coll_to_db:
        database = coll_to_db[table_name][1]
    if not database:
        log.warning(
            "[%s] jpa table=%r 不在任何已注册数据源中, 跳过 (repo_id=%d)",
            repo_name, table_name, repo_id,
        )
        return 0
    source_file = entity.get("source_file") or ""
    evidence = [{"source": "code_jpa", "repo_id": repo_id, "file": source_file}]

    # table_description candidate
    description = entity.get("description") or entity.get("class_javadoc") or ""
    if description:
        await write_canonical_candidate(
            db,
            namespace_id=namespace_id,
            db_type=db_type,
            database=database,
            target=table_name,
            field_path="",
            candidate_kind="table_description",
            candidate_value={"description": description},
            evidence_sources=evidence,
            confidence_status="confirmed_by_code",
            repo_id=repo_id,
        )
        count += 1

    # field_description candidates
    for field in entity.get("fields", []):
        field_name = field.get("column") or field.get("name") or ""
        if not field_name:
            continue

        field_desc = field.get("description") or field.get("comment") or ""
        if field_desc:
            candidate_value = _build_field_payload_for_candidate(field)
            candidate_value["description"] = field_desc
            await write_canonical_candidate(
                db,
                namespace_id=namespace_id,
                db_type=db_type,
                database=database,
                target=table_name,
                field_path=field_name,
                candidate_kind="field_description",
                candidate_value=candidate_value,
                evidence_sources=evidence,
                confidence_status="confirmed_by_code",
                repo_id=repo_id,
            )
            count += 1

        # enum_values candidate for fields with enum_values
        enum_values = field.get("enum_values")
        if enum_values:
            await write_canonical_candidate(
                db,
                namespace_id=namespace_id,
                db_type=db_type,
                database=database,
                target=table_name,
                field_path=field_name,
                candidate_kind="enum_values",
                candidate_value={"enum_values": enum_values},
                evidence_sources=evidence,
                confidence_status="confirmed_by_code",
                repo_id=repo_id,
            )
            count += 1

    # relationship candidates — 与 field 共享同一 db_type/database/gate
    for rel in entity.get("relations", []):
        if not rel.get("from_field") or not rel.get("to_target"):
            continue
        count += await _write_relationship_candidate(
            db, namespace_id, repo_id,
            from_target=table_name,
            from_field=rel.get("from_field"),
            to_target=rel.get("to_target"),
            to_field=rel.get("to_field", ""),
            relation_type=rel.get("relation_type", "foreign_key"),
            db_type=db_type,
            database=database,
            source_file=source_file,
            evidence_source="code_jpa",
            coll_to_db=coll_to_db,
        )

    return count


async def _write_mongo_document_candidates(
    db: AsyncSession, namespace_id: int, repo_id: int, doc: dict,
    coll_to_db: dict[str, tuple[str, str]] | None = None,
    repo_name: str = "",
) -> int:
    """Write table_description + field_description candidates for a Mongo document."""
    count = 0
    collection = doc.get("collection") or doc.get("collection_name") or ""
    if not collection:
        return 0

    db_type = "mongodb"
    database = doc.get("database") or doc.get("database_name") or ""
    # 反查: LLM 解析结果通常不带 database, 从 DataSource snapshot 补全
    if not database and coll_to_db and collection in coll_to_db:
        database = coll_to_db[collection][1]
    if not database:
        log.warning(
            "[%s] mongo collection=%r 不在任何已注册数据源中, 跳过 (repo_id=%d)",
            repo_name, collection, repo_id,
        )
        return 0
    source_file = doc.get("source_file") or ""
    evidence = [{"source": "code_mongo", "repo_id": repo_id, "file": source_file}]

    # table_description candidate
    description = doc.get("description") or doc.get("purpose_detail") or ""
    if description:
        await write_canonical_candidate(
            db,
            namespace_id=namespace_id,
            db_type=db_type,
            database=database,
            target=collection,
            field_path="",
            candidate_kind="table_description",
            candidate_value={"description": description},
            evidence_sources=evidence,
            confidence_status="confirmed_by_code",
            repo_id=repo_id,
        )
        count += 1

    # field_description candidates
    for field in doc.get("fields", []):
        field_name = field.get("name") or ""
        if not field_name:
            continue

        field_desc = field.get("description") or ""
        if field_desc:
            candidate_value = _build_field_payload_for_candidate(field)
            candidate_value["description"] = field_desc
            await write_canonical_candidate(
                db,
                namespace_id=namespace_id,
                db_type=db_type,
                database=database,
                target=collection,
                field_path=field_name,
                candidate_kind="field_description",
                candidate_value=candidate_value,
                evidence_sources=evidence,
                confidence_status="confirmed_by_code",
                repo_id=repo_id,
            )
            count += 1

        # enum_values for fields with enum_values
        enum_values = field.get("enum_values")
        if enum_values:
            await write_canonical_candidate(
                db,
                namespace_id=namespace_id,
                db_type=db_type,
                database=database,
                target=collection,
                field_path=field_name,
                candidate_kind="enum_values",
                candidate_value={"enum_values": enum_values},
                evidence_sources=evidence,
                confidence_status="confirmed_by_code",
                repo_id=repo_id,
            )
            count += 1

    # relationship candidates — 与 field 共享同一 db_type/database/gate
    for rel in doc.get("relations", []):
        if not rel.get("from_field") or not rel.get("to_target"):
            continue
        count += await _write_relationship_candidate(
            db, namespace_id, repo_id,
            from_target=collection,
            from_field=rel.get("from_field"),
            to_target=rel.get("to_target"),
            to_field=rel.get("to_field", ""),
            relation_type=rel.get("relation_type", "foreign_key"),
            db_type=db_type,
            database=database,
            source_file=source_file,
            evidence_source="code_mongo",
            coll_to_db=coll_to_db,
        )

    return count


async def _write_enum_class_candidates(
    db: AsyncSession, namespace_id: int, repo_id: int, enum_cls: dict
) -> int:
    """Write enum_values candidate from a standalone enum class extraction."""
    values = enum_cls.get("values", [])
    if not values:
        return 0

    # enum_class candidates need a target entity/table to link to.
    # If linked_table is provided, use it; otherwise skip (orphan enum).
    target = enum_cls.get("linked_table") or enum_cls.get("table_name") or ""
    field_path = enum_cls.get("linked_field") or enum_cls.get("field_name") or ""
    db_type = enum_cls.get("db_type") or "mysql"
    database = enum_cls.get("database") or ""

    if not target:
        return 0

    source_file = enum_cls.get("source_file") or ""
    evidence = [{"source": "code_enum", "repo_id": repo_id, "file": source_file}]

    await write_canonical_candidate(
        db,
        namespace_id=namespace_id,
        db_type=db_type,
        database=database,
        target=target,
        field_path=field_path,
        candidate_kind="enum_values",
        candidate_value={"enum_values": values},
        evidence_sources=evidence,
        confidence_status="confirmed_by_code",
        repo_id=repo_id,
    )
    return 1


_RELATION_TYPE_NORMALIZE: dict[str, str] = {
    "foreign_key": "many_to_one", "fk": "many_to_one", "": "many_to_one",
}

async def _write_relationship_candidate(
    db: AsyncSession, namespace_id: int, repo_id: int, *,
    from_target: str, from_field: str,
    to_target: str, to_field: str,
    relation_type: str = "foreign_key",
    db_type: str = "mysql",
    database: str = "",
    source_file: str = "",
    evidence_source: str = "code_relation",
    to_db_type: str = "",
    to_database: str = "",
    coll_to_db: dict[str, tuple[str, str]] | None = None,
) -> int:
    """Write a relationship candidate — 归一化 7 键(无 source, 无 is_required).

    to_db_type/to_database 从 coll_to_db 反查填, 不再留空.
    source 走 evidence_sources (不进 candidate_value → 不进 hash).
    """
    if not from_target or not to_target or not database:
        return 0

    rt_lower = (relation_type or "").lower()
    normalized_rt = _RELATION_TYPE_NORMALIZE.get(rt_lower, rt_lower)
    _to_db_type = to_db_type or db_type
    _to_database = to_database or database
    if coll_to_db and to_target in coll_to_db:
        _to_db_type, _to_database = coll_to_db[to_target]

    evidence = [{"source": evidence_source, "repo_id": repo_id, "file": source_file}]

    candidate_value = {
        "from_target": from_target,
        "from_field": from_field,
        "to_db_type": _to_db_type,
        "to_database": _to_database,
        "to_target": to_target,
        "to_field": to_field,
        "relation_type": normalized_rt,
    }

    await write_canonical_candidate(
        db,
        namespace_id=namespace_id,
        db_type=db_type,
        database=database,
        target=from_target,
        field_path=from_field,
        candidate_kind="relationship",
        candidate_value=candidate_value,
        evidence_sources=evidence,
        confidence_status="confirmed_by_code",
        repo_id=repo_id,
    )
    return 1


# ════════════════════════════════════════════════════════════════
#  extract_and_write_knowledge
# ════════════════════════════════════════════════════════════════


async def extract_and_write_knowledge(
    db: AsyncSession,
    *,
    namespace_id: int,
    repo_id: int,
    mybatis_entries: list[dict],
    business_terms: list[dict],
    business_rules: list[dict],
    business_examples: list[dict] | None = None,
    explain_gate: "ExplainGate | None" = None,
    repo_name: str = "",
) -> int:
    """Write KnowledgeEntry(status=proposed) for knowledge-channel extractions.

    Creates KE entries for:
    - route_hint (aggregated per mapper namespace → table set)
    - rule (from business_rules)
    - terminology (from business_terms, via upsert_terminology_with_validation)
    - example (from business_examples — sql2nl 查询模式, D3 恢复; agentic 管线核心产出)

    Returns total KE entries created.
    """
    total = 0

    # ── mybatis_entries → route_hint KE (namespace aggregation) ──
    total += await _write_route_hints(db, namespace_id, repo_id, mybatis_entries)

    # ── business_rules → rule KE ──
    for rule in business_rules:
        created = await _write_rule_ke(db, namespace_id, repo_id, rule)
        if created:
            total += 1

    # ── business_terms → terminology KE (via existing pathway) ──
    for term in business_terms:
        created = await _write_terminology_ke(db, namespace_id, repo_id, term, repo_name=repo_name)
        if created:
            total += 1

    # ── business_examples → example KE (sql2nl, D3 恢复) ──
    total += await _write_business_examples(
        db, namespace_id, repo_id, business_examples or [],
    )

    log.info(
        "[%s] KE written: %d (rule=%d term=%d example=%d)",
        repo_name, total, len(business_rules), len(business_terms),
        len(business_examples or []),
    )
    return total


async def _write_business_examples(
    db: AsyncSession, namespace_id: int, repo_id: int, business_examples: list[dict],
) -> int:
    """sql2nl → example KE (D3 恢复). Stage A 下线, 本 spec 经 agentic 管线恢复为核心产出.

    每个 example dict (agent emit_knowledge entry_type=example 的 payload) →
    KnowledgeEntry(entry_type='example', status='proposed', source='code_extract').
    """
    total = 0
    for ex in business_examples:
        sql = ex.get("sql_pattern", "")
        if not sql:
            continue
        db.add(KnowledgeEntry(
            namespace_id=namespace_id,
            entry_type="example",
            status="proposed",
            tier="normal",
            source="code_extract",
            repo_id=repo_id,
            content=f"查询模式: {sql[:120]}",
            payload=json.dumps({
                "question_pattern": ex.get("question_pattern") or ex.get("question", ""),
                "collections": [f"{ex.get('database', '')}.{t}" if ex.get('database') else t
                                for t in ex.get("tables", [])],
                "join_keys": ex.get("join_keys", []),
                "final_query_plan": ex.get("final_query_plan"),
                "result_summary": ex.get("result_summary", ""),
                "sql_pattern": sql,                          # legacy compat
                "tables": ex.get("tables", []),              # legacy compat
                "source_mapper": ex.get("mapper_namespace", ""),
                "extraction_source": "mybatis_extract",
            }, ensure_ascii=False),
        ))
        total += 1
    if total:
        await db.flush()
    return total


async def _write_rule_ke(
    db: AsyncSession, namespace_id: int, repo_id: int, rule: dict
) -> bool:
    """Create a KnowledgeEntry for a business rule extraction."""
    rule_text = rule.get("rule_text") or ""
    if not rule_text:
        return False

    payload = RulePayload(
        rule_text=rule_text,
        applies_to_collections=rule.get("applies_to_collections") or [],
        rule_kind=rule.get("rule_kind") or "business_constraint",
        evidence=rule.get("evidence"),
    )

    ke = KnowledgeEntry(
        namespace_id=namespace_id,
        entry_type="rule",
        status="proposed",
        tier="normal",
        content=rule_text,
        payload=payload.model_dump_json(),
        source="code_extract",
        repo_id=repo_id,
        raw_input=rule_text,
    )
    db.add(ke)
    await db.flush()
    return True


async def _write_terminology_ke(
    db: AsyncSession, namespace_id: int, repo_id: int, term: dict,
    repo_name: str = "",
) -> bool:
    """Create a terminology KE via upsert_terminology_with_validation if available.

    repo_id 形参保留但有意忽略: 术语只归属 schema/namespace (ns 级), 不写 repo_id.
    """
    from app.knowledge.terminology_intake import upsert_terminology_with_validation, _resolve_db_type

    term_name = term.get("term") or ""
    if not term_name:
        return False

    primary_db = term.get("primary_database") or ""
    resolved_db_type = await _resolve_db_type(db, namespace_id, primary_db) if primary_db else None
    if not resolved_db_type:
        log.warning("[%s] terminology %r: cannot resolve db_type for database=%r from DataSource",
                    repo_name, term_name, primary_db)
        return False

    payload_dict = {
        "term": term_name,
        "primary_collection": term.get("primary_collection") or "",
        "primary_database": primary_db,
        "db_type": resolved_db_type,
        "primary_field": term.get("primary_field"),
        "synonyms": term.get("synonyms") or [],
        "source_collections": term.get("source_collections") or [],
    }

    try:
        ke = await upsert_terminology_with_validation(
            db, ns_id=namespace_id, payload_dict=payload_dict,
            source="schema",  # 术语零 repo_id
        )
        return ke is not None
    except Exception as e:
        log.warning("[%s] terminology upsert failed: %s", repo_name, e)
        return False


# ════════════════════════════════════════════════════════════════
#  route_hint aggregation (mapper namespace → table set)
# ════════════════════════════════════════════════════════════════


async def _write_route_hints(
    db: AsyncSession, namespace_id: int, repo_id: int,
    mybatis_entries: list[dict],
) -> int:
    """Aggregate mybatis entries by mapper_namespace → one route_hint KE per namespace.

    Each route_hint contains the sorted set of all tables referenced across
    the namespace's methods (extracted from SQL via FROM/JOIN patterns).
    """
    import re

    by_ns: dict[str, set[str]] = {}
    for entry in mybatis_entries:
        ns = entry.get("mapper_namespace")
        if entry.get("type", "").lower() != "select":
            continue
        sql = entry.get("canonical_sql") or entry.get("sql") or ""
        if not ns or not sql:
            continue
        tables = set(re.findall(r"\b(?:FROM|JOIN)\s+([\w_]+)", sql, re.I))
        by_ns.setdefault(ns, set()).update(tables)

    count = 0
    for mapper_ns, tables in by_ns.items():
        if not tables:
            continue
        ke = KnowledgeEntry(
            namespace_id=namespace_id,
            entry_type="route_hint",
            status="proposed",
            tier="normal",
            content=f"{mapper_ns} 涉及表: {', '.join(sorted(tables))}",
            payload=json.dumps({
                "topic_summary": f"{mapper_ns} 涉及表",
                "target_collections": sorted(tables),
                "source_mapper": mapper_ns,
                "extraction_source": "mybatis_extract",
                "source_repo_id": repo_id,
            }, ensure_ascii=False),
            source="code_extract",
            repo_id=repo_id,
        )
        db.add(ke)
        count += 1

    if count:
        await db.flush()
    return count





