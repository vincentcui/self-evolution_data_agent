"""一次性迁移: route_hint payload → 新 shape (spec 2026-07-08 C2/C8).

新 shape: {collection_path, navigation_note}. question_pattern 已在 content 列.
零兼容逻辑 — app 只认新 shape, 此脚本把所有旧数据改成新 shape.

用法:
  python scripts/migrate_route_hint_payload.py --dry-run   # 打印行数 + 3 条 before/after
  python scripts/migrate_route_hint_payload.py             # 实跑 + 自验

ChromaDB 不碰 (向量集合不存 payload, content 不变 → 主向量不动; HyQE 用 collection_path 保留).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 让脚本可从 backend/ 直接跑 (sys.path[0] = scripts/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # type: ignore
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models.knowledge_entry import KnowledgeEntry
from app.schemas.knowledge_payload import RouteHintPayload


def transform_route_hint_payload(old: dict) -> dict:
    """旧 payload → 新 shape. 纯函数, 幂等 (新 shape 输入返新 shape)."""
    return {
        "collection_path": list(old.get("collection_path") or []),
        "navigation_note": old.get("reason") or old.get("navigation_note") or "",
    }


def _is_new_shape(old: dict) -> bool:
    """幂等守卫: 已含 navigation_note 键视为新 shape, 跳过."""
    return "navigation_note" in old


async def _run(dry_run: bool) -> None:
    engine = create_async_engine(settings.metadata_db_url)
    try:
        # ── step 0: purge mybatis 自动噪声 source='code_extract' (spec D6/C10) ──
        # 第三 payload 形态 {topic_summary, target_collections,...} 经 transform 会清空成
        # 空壳 {collection_path:[], navigation_note:''} (数据破坏), 且属自动噪声.
        # status=proposed 无向量, transform 前物理删.
        # 与 C10 (删写入路径) 配套: C10 断新增, step 0 清存量.
        async with engine.begin() as conn:
            purge_rows = (await conn.execute(
                select(KnowledgeEntry.id).where(
                    KnowledgeEntry.entry_type == "route_hint",
                    KnowledgeEntry.source == "code_extract",
                )
            )).all()
            purge_count = len(purge_rows)
        print(f"[migrate] step 0: source='code_extract' 待 purge {purge_count} 行")
        if not dry_run and purge_count:
            async with engine.begin() as conn:
                await conn.execute(text(
                    "DELETE FROM knowledge_entries "
                    "WHERE entry_type='route_hint' AND source='code_extract'"
                ))
            print(f"[migrate] step 0: 已 purge {purge_count} 行")

        # ── step 1: transform 其余 route_hint (end_turn/refine/人工) payload ──
        async with engine.begin() as conn:
            rows = (await conn.execute(
                select(KnowledgeEntry.id, KnowledgeEntry.payload).where(
                    KnowledgeEntry.entry_type == "route_hint"
                )
            )).all()
        print(f"[migrate] route_hint 总行数: {len(rows)}")

        to_migrate: list[tuple[int, dict]] = []
        skipped = 0
        for rid, payload_str in rows:
            try:
                old = json.loads(payload_str) if payload_str else {}
            except json.JSONDecodeError:
                print(f"[migrate] WARN entry_id={rid} payload 非 JSON, 跳过")
                skipped += 1
                continue
            if _is_new_shape(old):
                skipped += 1
                continue
            to_migrate.append((rid, transform_route_hint_payload(old)))

        print(f"[migrate] 待迁移: {len(to_migrate)}, 跳过(已新shape/损坏): {skipped}")
        for rid, new in to_migrate[:3]:
            print(f"  entry_id={rid} → {new}")

        if dry_run:
            print("[migrate] dry-run, 不写入")
            return

        # 实跑
        async with engine.begin() as conn:
            for rid, new in to_migrate:
                await conn.execute(
                    update(KnowledgeEntry).where(KnowledgeEntry.id == rid).values(
                        payload=json.dumps(new, ensure_ascii=False)
                    )
                )
        print(f"[migrate] 已更新 {len(to_migrate)} 行")

        # 自验: re-read 全部 route_hint, 逐条过新 schema
        async with engine.begin() as conn:
            rows2 = (await conn.execute(
                select(KnowledgeEntry.id, KnowledgeEntry.payload).where(
                    KnowledgeEntry.entry_type == "route_hint"
                )
            )).all()
        valid = invalid = 0
        for rid, payload_str in rows2:
            try:
                old = json.loads(payload_str) if payload_str else {}
                RouteHintPayload(**old)
                valid += 1
            except Exception as e:
                invalid += 1
                print(f"[migrate] INVALID entry_id={rid}: {e}")
        print(f"[migrate] 自验: valid={valid}, invalid={invalid}")
        if invalid:
            sys.exit(1)
    finally:
        await engine.dispose()


def main() -> None:
    load_dotenv()  # 读 backend/.env
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(args.dry_run))


if __name__ == "__main__":
    main()
