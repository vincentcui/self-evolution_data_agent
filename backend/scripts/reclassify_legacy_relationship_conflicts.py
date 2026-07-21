"""修复旧版错误聚合的 relationship conflict。

默认 dry-run，只读列出受影响记录。传 --apply 才会写入 .env.prod 指向的元数据库。
运行：cd backend && python -m scripts.reclassify_legacy_relationship_conflicts [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

ENV_FILE = Path(__file__).resolve().parents[1] / ".env.prod"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="提交远端数据库变更")
    parser.add_argument("--namespace-id", type=int, help="仅处理指定 namespace")
    return parser.parse_args()


def load_production_environment() -> None:
    if not ENV_FILE.is_file():
        raise RuntimeError(f"production env file not found: {ENV_FILE}")
    load_dotenv(ENV_FILE, override=True)
    if not os.environ.get("IS_METADATA_DB_URL"):
        raise RuntimeError("IS_METADATA_DB_URL is required in backend/.env.prod")


async def require_conflict_scope(db) -> None:
    exists = (await db.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "AND table_name = 'schema_canonical_conflicts' "
        "AND column_name = 'conflict_scope'"
    ))).scalar_one_or_none()
    if exists is None:
        raise RuntimeError(
            "conflict_scope is missing; deploy the application migration before running this script"
        )


async def main(args: argparse.Namespace) -> None:
    load_production_environment()
    from app.db.metadata import async_session
    from app.knowledge.canonical_promote import (
        find_misgrouped_relationship_conflicts,
        promote_single_field,
        reclassify_misgrouped_relationship_conflicts,
    )

    async with async_session() as db:
        await require_conflict_scope(db)
        planned = await find_misgrouped_relationship_conflicts(db, args.namespace_id)
        print(f"Found {len(planned)} legacy relationship conflicts")
        for item in planned:
            print(
                f"  conflict={item.conflict_id} ns={item.namespace_id} "
                f"{item.database}.{item.target}.{item.field_path} "
                f"candidates={item.candidate_count} identities={item.scope_count}"
            )
        if not args.apply:
            print("Dry run only. Re-run with --apply to commit these changes.")
            return

        reclassified = await reclassify_misgrouped_relationship_conflicts(
            db, args.namespace_id
        )
        promoted = 0
        for item in reclassified:
            report = await promote_single_field(
                db,
                ns_id=item.namespace_id,
                db_type=item.db_type,
                database=item.database,
                target=item.target,
                field_path=item.field_path,
                candidate_kind="relationship",
            )
            promoted += report.promoted_count
        await db.commit()
        print(f"Committed: reclassified={len(reclassified)}, promoted={promoted}")


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
