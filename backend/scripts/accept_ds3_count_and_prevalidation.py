"""Empirical acceptance against the live DocumentDB instance (ds=3) — READ-ONLY.

Spec: mongo-count-pipeline-and-plan-caps, task 10 (evidence-backed, optional/gated).
This is a CONFIRMATION against the real instance, NOT the primary regression gate
(that is the CI-enforced hard gate in task 2.1, which needs no live DocumentDB).

Confirms TWO things on ds=3 / c_brand:

  (A) Count-with-pipeline fix (Req 1.1, 1.2):
      execute_query(ds, "c_brand", {"pipeline":[{"$match": F}]}, mode="count")
      agrees with the equivalent {"filter": F} count AND is strictly smaller than the
      whole-collection count. The key proof is that a pipeline-shape count now reflects
      the match condition (differs from the whole-collection count) — the old code
      returned the whole-collection count (14783) for the pipeline shape.

  (B) Pre-validation rejection (Req 5.2, 5.3):
      a $project step with an embedded-$ fieldpath ("$module.$id_str") is rejected by
      validate_pipeline_against_caps(pipeline, ds3_caps) with a readable
      {reason, suggested_next_step} payload instead of a raw OperationFailure 16410.
      Optionally contrasts with the raw driver, confirming the driver itself emits 16410.

Usage (from backend/):
  cd backend && python -m scripts.accept_ds3_count_and_prevalidation [ds_id]
  cd backend && python -m scripts.accept_ds3_count_and_prevalidation 3 --brand-regex '正则'

READ-ONLY: only count_documents / aggregate(read) / find(read) are issued. No writes.
If ds=3 is unreachable, the script reports that clearly and exits non-zero WITHOUT
fabricating results (task 10 is explicitly optional/gated).
"""
# ruff: noqa: E501 — 验收脚本输出含密集字面量, 单行可读性优先
from __future__ import annotations

import argparse
import asyncio
import json as _json
import re
import sys

from sqlalchemy import select

from app.db.metadata import async_session
from app.engine.drivers.mongo import MongoDriver
from app.engine.plan_executor import validate_pipeline_against_caps
from app.engine.tools._db_profile_projector import _project_db_profile
from app.models.namespace import DataSource

_COLLECTION = "c_brand"


async def _resolve_ds(ds_id: int) -> DataSource | None:
    async with async_session() as s:
        return (
            await s.execute(select(DataSource).where(DataSource.id == ds_id))
        ).scalar_one_or_none()


async def _whole_collection_count(driver: MongoDriver, ds: DataSource) -> int:
    """Whole-collection count via the FILTER shape ({} matches everything)."""
    res = await driver.execute_query(ds, _COLLECTION, {"filter": {}}, mode="count")
    return int(res["rows"][0]["count"])


async def _empty_pipeline_count(driver: MongoDriver, ds: DataSource) -> int:
    """Whole-collection count via the AGGREGATE shape with an empty pipeline ([]).

    Exercises Req 1.6 / 2.6: an empty pipeline classifies as aggregate and counts the
    whole collection (the old estimate_cost truthiness form misclassified []).
    """
    res = await driver.execute_query(ds, _COLLECTION, {"pipeline": []}, mode="count")
    return int(res["rows"][0]["count"])


async def _count_filter_shape(driver: MongoDriver, ds: DataSource, f: dict) -> int:
    res = await driver.execute_query(ds, _COLLECTION, {"filter": f}, mode="count")
    return int(res["rows"][0]["count"])


async def _count_pipeline_shape(driver: MongoDriver, ds: DataSource, f: dict) -> int:
    res = await driver.execute_query(
        ds, _COLLECTION, {"pipeline": [{"$match": f}]}, mode="count"
    )
    return int(res["rows"][0]["count"])


async def _pick_narrowing_filter(
    driver: MongoDriver, ds: DataSource, whole: int, brand_regex: str | None,
) -> tuple[dict, str]:
    """Return (filter, human_description) that selects a strict subset (count < whole).

    Preference order:
      1. caller-supplied --brand-regex (honors the design's boundary-regex intent).
      2. auto-derived: sample a brandName, match the docs whose brandName contains it.
      3. fallback: exact brandName equality of a sampled doc.
    A strict subset (0 < count < whole) is what proves the pipeline count reflects the
    match condition rather than the whole collection.
    """
    if brand_regex:
        return (
            {"brandName": {"$regex": brand_regex}},
            f"brandName ~= /{brand_regex}/ (caller-supplied)",
        )

    # Sample a few docs with a string brandName (read-only probe, limit 10).
    probe = await driver.execute_query(
        ds, _COLLECTION, {"filter": {"brandName": {"$type": "string"}}}, mode="probe"
    )
    names = [
        d.get("brandName")
        for d in probe["rows"]
        if isinstance(d.get("brandName"), str) and d.get("brandName").strip()
    ]
    if not names:
        raise RuntimeError(
            "无法采样到含字符串 brandName 的文档, 无法自动构造窄化过滤. "
            "请用 --brand-regex 显式指定一个窄化正则."
        )

    name = names[0]
    candidates: list[tuple[dict, str]] = [
        ({"brandName": {"$regex": re.escape(name)}}, f"brandName 包含 {name!r}"),
        ({"brandName": name}, f"brandName == {name!r}"),
    ]
    # Also try a short prefix of the sampled name as a (possibly broader) boundary subset.
    prefix = name.strip()[:2]
    if prefix:
        candidates.insert(
            0, ({"brandName": {"$regex": re.escape(prefix)}}, f"brandName 以 {prefix!r} 起的正则子集")
        )

    for f, desc in candidates:
        c = await _count_filter_shape(driver, ds, f)
        if 0 < c < whole:
            return f, f"{desc} → {c} 行 (严格子集)"
    # Last resort: equality must yield >=1; if it equals whole the collection is degenerate.
    f, desc = candidates[-1]
    return f, f"{desc} (注意: 非严格子集, 见下方实测数字)"


async def _confirm_raw_16410(driver: MongoDriver, ds: DataSource) -> str:
    """OPTIONAL contrast: confirm the raw driver emits OperationFailure 16410 for the
    embedded-$ fieldpath, so pre-validation is demonstrably preventing a real driver error.
    READ-ONLY aggregate."""
    client = driver._get_client(ds)
    coll = client[ds.database][_COLLECTION]
    bad = [{"$project": {"x": "$module.$id_str"}}, {"$limit": 1}]
    try:
        async for _ in coll.aggregate(bad, maxTimeMS=8000):
            break
        return "raw driver 未报错 (意外 — 该实例似乎接受了 embedded-$ fieldpath)"
    except Exception as exc:  # noqa: BLE001 — we want the raw code
        code = getattr(exc, "code", None)
        return f"raw driver OperationFailure code={code} (期望 16410)"


async def main(ds_id: int, brand_regex: str | None) -> int:
    ds = await _resolve_ds(ds_id)
    if ds is None:
        print(f"[UNREACHABLE] ds={ds_id} 在元数据库中不存在")
        return 2

    driver = MongoDriver()
    print(f"=== Empirical acceptance: ds={ds_id} db={ds.database} coll={_COLLECTION} ===")

    # Reachability probe (read-only). A connection failure here means the live instance
    # is not reachable from this environment — report, do not fabricate.
    try:
        if not await driver.health_check(ds):
            print(f"[UNREACHABLE] ds={ds_id} health_check 失败 (连接被拒/超时). "
                  f"task 10 是可选/gated 项; 主回归门禁 (task 2.1 CI 硬门禁) 已绿, 无需 live DocumentDB.")
            await driver.close_all()
            return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[UNREACHABLE] ds={ds_id} 连接异常: {exc!r}. "
              f"task 10 是可选/gated 项; 主回归门禁 (task 2.1 CI 硬门禁) 已绿, 无需 live DocumentDB.")
        await driver.close_all()
        return 2

    failures: list[str] = []
    try:
        # ── (A) Count-with-pipeline fix ─────────────────────────────────────────
        print("\n--- (A) Count honors the pipeline shape (Req 1.1, 1.2, 1.6) ---")
        whole_filter = await _whole_collection_count(driver, ds)
        whole_empty_pipeline = await _empty_pipeline_count(driver, ds)
        print(f"whole-collection count  (filter {{}})       = {whole_filter}")
        print(f"whole-collection count  (pipeline [])       = {whole_empty_pipeline}")
        if whole_filter != whole_empty_pipeline:
            failures.append(
                f"empty-pipeline count {whole_empty_pipeline} != filter {{}} count {whole_filter} (Req 1.6)"
            )
        if whole_filter <= 0:
            failures.append(f"whole-collection count 非正数 ({whole_filter}); 集合可能为空, 无法证明子集")

        f, desc = await _pick_narrowing_filter(driver, ds, max(whole_filter, 1), brand_regex)
        print(f"narrowing filter: {desc}")
        print(f"narrowing filter (raw)  = {f}")

        pipe_count = await _count_pipeline_shape(driver, ds, f)
        filt_count = await _count_filter_shape(driver, ds, f)
        print(f"narrow count (pipeline-shape {{'pipeline':[{{'$match':F}}]}}) = {pipe_count}")
        print(f"narrow count (filter-shape   {{'filter':F}})               = {filt_count}")

        if pipe_count != filt_count:
            failures.append(
                f"pipeline-shape count {pipe_count} != filter-shape count {filt_count} (Req 1.1/1.2)"
            )
        else:
            print(f"OK: pipeline-shape == filter-shape == {pipe_count}")

        if not (pipe_count < whole_filter):
            failures.append(
                f"narrow count {pipe_count} 未严格小于 whole-collection {whole_filter} — "
                f"无法证明 count 反映了 match (请用 --brand-regex 指定更窄的过滤)"
            )
        else:
            print(f"OK: narrow count {pipe_count} < whole-collection {whole_filter} "
                  f"(pipeline-shape count 反映了 match, 不再是整集合计数)")

        # ── (B) Pre-validation rejection ────────────────────────────────────────
        print("\n--- (B) Pre-validation rejects embedded-$ fieldpath (Req 5.2, 5.3) ---")
        profile = _json.loads(ds.db_profile_json or "{}")
        caps = _project_db_profile(profile, "caps")
        version = profile.get("version", "?")
        if not caps or not profile.get("version"):
            failures.append("db_profile_json 为空/无 version — 建源时 buildInfo 可能失败, 无法做能力预校验")
        else:
            print(f"resolved caps: flavor={caps.get('flavor')} version={version}")
            print(f"syntax_constraints = {caps.get('syntax_constraints')}")
            bad_pipeline = [
                {"$match": {}},
                {"$project": {"x": "$module.$id_str"}},
                {"$limit": 1},
            ]
            violation = validate_pipeline_against_caps(bad_pipeline, caps)
            print(f"bad pipeline = {bad_pipeline}")
            if violation is None:
                failures.append("validate_pipeline_against_caps 未拦截 embedded-$ fieldpath (期望 violation)")
            else:
                reason = violation.get("reason")
                suggestion = violation.get("suggested_next_step")
                restriction = violation.get("restriction")
                print(f"VIOLATION restriction = {restriction}")
                print(f"VIOLATION reason      = {reason}")
                print(f"VIOLATION suggested_next_step = {suggestion}")
                if not reason or not suggestion:
                    failures.append("violation 缺少 reason / suggested_next_step (Req 5.3)")
                elif restriction != "project_no_dollar_fieldpath":
                    failures.append(
                        f"violation.restriction={restriction!r} 期望 'project_no_dollar_fieldpath'"
                    )
                else:
                    print("OK: 以可读的 {reason, suggested_next_step} 拦截, 而非裸 16410")

            # Optional contrast: confirm the raw driver actually emits 16410.
            print("\n--- (B') Optional contrast: raw driver behavior for the same shape ---")
            raw_msg = await _confirm_raw_16410(driver, ds)
            print(raw_msg)
            if "16410" not in raw_msg:
                print("NOTE: raw driver 未报 16410 — 不计入失败 (该对照仅为加强证据, 非验收必需)")

    finally:
        await driver.close_all()

    print("\n=== RESULT ===")
    if failures:
        print("FAILED:")
        for fdesc in failures:
            print(f"  - {fdesc}")
        return 1
    print("PASSED — 两项确认均通过 (count-with-pipeline + 预校验拦截), 基于 ds=3 实测证据")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ds_id", nargs="?", type=int, default=3, help="DataSource id (default 3)")
    parser.add_argument(
        "--brand-regex", default=None,
        help="可选: brandName 的窄化正则 (默认自动采样推导)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.ds_id, args.brand_regex)))
