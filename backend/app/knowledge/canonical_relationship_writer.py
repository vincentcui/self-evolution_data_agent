"""FK relationship → SchemaCanonicalCandidate writer (confirmed_by_introspect).

candidate_value 不含 source — source 走 evidence_sources, 由 promote 回填.
"""
from __future__ import annotations

import logging

from app.knowledge.canonical_candidate import write_canonical_candidate

log = logging.getLogger(__name__)


async def write_relationship_candidates_from_foreign_keys(
    db, *, namespace_id, datasource, foreign_keys, ds_id, referenced_targets=None,
) -> int:
    """FK 列表 → relationship candidate, 经 candidate 管道 → promote → SCO.

    source 不在 candidate_value (不进 hash), 写入 evidence_sources.
    referenced_targets 非 None 时仅写入目标表在集合内的 FK (收窄 refresh 范围).
    """
    count = 0
    for fk in foreign_keys:
        ft = fk.get("from_target", "")
        ff = fk.get("from_field", "")

        # Oracle 表名大写 vs referenced_targets 原始大小写: 复用 _is_referenced_target
        if referenced_targets is not None:
            from app.knowledge.schema_canonical import _is_referenced_target
            if not _is_referenced_target(datasource.db_type, ft, referenced_targets):
                continue

        if not ft or not ff:
            continue

        await write_canonical_candidate(
            db,
            namespace_id=namespace_id,
            db_type=datasource.db_type,
            database=datasource.database,
            target=ft,
            field_path=ff,
            candidate_kind="relationship",
            candidate_value={
                "from_target": ft,
                "from_field": ff,
                "to_db_type": fk.get("to_db_type", datasource.db_type),
                "to_database": fk.get("to_database", ""),
                "to_target": fk.get("to_target", ""),
                "to_field": fk.get("to_field", ""),
                "relation_type": fk.get("relation_type", "many_to_one"),
            },
            evidence_sources=[{
                "source": "introspect_fk",
                "datasource_id": ds_id,
                "db_type": datasource.db_type,
            }],
            confidence_status="confirmed_by_introspect",
            datasource_id=ds_id,
        )
        count += 1
    return count
