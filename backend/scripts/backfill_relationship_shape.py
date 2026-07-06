"""一次性归一化全仓 relationships_json → 6 键 canonical 形态 (幂等).

迁移规则:
  - relation_type: "foreign_key" → "many_to_one"
  - 补 to_db_type (缺省 sco.db_type) / to_database (缺省 sco.database)
  - 删 is_required
  - source → sources 列表迁移 (旧单值 → [旧值])
  - 幂等: 可重复跑, 已升级条目 skip
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import sys

from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.metadata import async_session
from app.models import SchemaCanonicalObject

log = logging.getLogger(__name__)

_RELATION_TYPE_MAP = {"foreign_key": "many_to_one"}


def _upgrade_entry(r: dict, db_type: str, database: str) -> dict | None:
    """Normalize one relationship entry. Returns None if no change needed."""
    changed = False
    out = dict(r)

    # ── relation_type 收敛 ──
    rt = out.get("relation_type", "")
    if rt in _RELATION_TYPE_MAP:
        out["relation_type"] = _RELATION_TYPE_MAP[rt]
        changed = True

    # ── 补 to_db_type / to_database ──
    if not out.get("to_db_type"):
        out["to_db_type"] = db_type
        changed = True
    if not out.get("to_database"):
        out["to_database"] = database
        changed = True

    # ── 删 is_required ──
    if "is_required" in out:
        del out["is_required"]
        changed = True

    # ── source → sources 列表迁移 ──
    if "source" in out:
        src = out.pop("source")
        existing = set(out.get("sources", []))
        existing.add(src)
        out["sources"] = list(existing)
        changed = True

    if "sources" not in out:
        out["sources"] = []

    return out if changed else None


async def _backfill(db: AsyncSession, dry_run: bool = False) -> tuple[int, int]:
    """Return (scanned, updated)."""
    rows = list((await db.execute(sa_select(SchemaCanonicalObject))).scalars().all())
    scanned = len(rows)
    updated = 0

    for sco in rows:
        rels = _json.loads(sco.relationships_json or "[]")
        if not rels:
            continue

        upgraded = []
        any_changed = False
        for r in rels:
            result = _upgrade_entry(r, sco.db_type, sco.database)
            if result is not None:
                upgraded.append(result)
                any_changed = True
            else:
                upgraded.append(r)

        if any_changed:
            sco.relationships_json = _json.dumps(upgraded, ensure_ascii=False)
            updated += 1
            if not dry_run:
                db.add(sco)

    if not dry_run and updated:
        await db.flush()
        log.info("backfill_relationship_shape: %d/%d SCOs updated", updated, scanned)
    else:
        log.info("backfill_relationship_shape (dry_run=%s): %d/%d would update",
                 dry_run, updated, scanned)

    return scanned, updated


async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    dry_run = "--dry-run" in sys.argv

    async with async_session() as db:
        scanned, updated = await _backfill(db, dry_run=dry_run)
        if not dry_run and updated:
            await db.commit()
            print(f"Committed: {updated}/{scanned} SCOs updated")
        elif dry_run:
            print(f"Dry run: would update {updated}/{scanned} SCOs")
        else:
            print(f"No changes needed ({scanned} SCOs scanned)")


if __name__ == "__main__":
    asyncio.run(main())
