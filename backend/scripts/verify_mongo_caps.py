"""Auto-verification report for mongo db_profile caps.

Outputs verification_report.md under backend/ with:
- Per-datasource: version + unsupported ops
- Trace 173dff87 failure-mode coverage check ($round in unsupported list)
- L0 + L1 test result summary
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Ensure backend/ is on sys.path so `app.*` imports resolve when invoked as
# `python scripts/verify_mongo_caps.py` (which puts scripts/, not backend/, on path).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPORT_PATH = Path(__file__).parent.parent / "verification_report.md"


def _run_pytest(target: str, marker: str | None = None) -> tuple[int, str]:
    cmd = ["pytest", target, "-v", "--tb=short"]
    if marker:
        cmd += ["-m", marker]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).parent.parent))
    return proc.returncode, proc.stdout + proc.stderr


async def _per_ds_report() -> list[dict]:
    import json as _json

    from app.engine.tools._db_profile_projector import _project_db_profile
    from app.models import DataSource

    url = os.environ.get("IS_METADATA_DB_URL")
    if not url:
        return [{"error": "IS_METADATA_DB_URL not set"}]
    engine = create_async_engine(url)
    SM = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    out: list[dict] = []
    try:
        async with SM() as s:
            rows = (await s.execute(
                select(DataSource).where(DataSource.db_type == "mongodb")
            )).scalars().all()
        for ds in rows:
            try:
                profile = _json.loads(ds.db_profile_json or "{}")
                caps = _project_db_profile(profile, "caps")
            except Exception as exc:  # noqa: BLE001
                out.append({"ds_id": ds.id, "error": repr(exc)})
                continue
            if not profile.get("version"):
                _err = "db_profile_json 无 version — 建源时 buildInfo 可能失败"
                out.append({"ds_id": ds.id, "error": _err})
                continue
            out.append({
                "ds_id": ds.id,
                "host": ds.host,
                "database": ds.database,
                "version": profile["version"],
                "unsupported_ops": caps.get("unsupported_ops", []),
                "round_blocked": "$round" in caps.get("unsupported_ops", []),
            })
    finally:
        await engine.dispose()
    return out


def main() -> int:
    lines: list[str] = []
    lines.append("# Mongo Server Capabilities Verification Report\n")
    lines.append(f"_Generated: {datetime.now().isoformat(timespec='seconds')}_\n")

    # L0 unit
    lines.append("## L0 Unit Tests\n")
    rc, out = _run_pytest("tests/engine/drivers/test_mongo_capabilities.py")
    lines.append(f"- Status: **{'PASS' if rc == 0 else 'FAIL'}**\n")
    lines.append("```\n" + out[-2000:] + "\n```\n")

    rc2, out2 = _run_pytest("tests/engine/tools/test_data_access_capabilities.py")
    lines.append(f"- data_access integration: **{'PASS' if rc2 == 0 else 'FAIL'}**\n")
    lines.append("```\n" + out2[-2000:] + "\n```\n")

    # L1 live
    lines.append("\n## L1 Live Test (real ds=3)\n")
    rcl, outl = _run_pytest(
        "tests/engine/drivers/test_mongo_capabilities_live.py", marker="live",
    )
    lines.append(f"- Status: **{'PASS' if rcl == 0 else 'FAIL'}**\n")
    lines.append("```\n" + outl[-2000:] + "\n```\n")

    # Per-DS report
    lines.append("\n## Per-Datasource Capabilities\n")
    try:
        per_ds = asyncio.run(_per_ds_report())
    except Exception as exc:  # noqa: BLE001
        lines.append(f"- Error: {exc}\n")
        per_ds = []
    for entry in per_ds:
        if "error" in entry:
            lines.append(f"- ds={entry.get('ds_id', '?')}: ERROR `{entry['error']}`\n")
            continue
        flag = "✅" if entry["round_blocked"] else "⚠️"
        lines.append(
            f"- ds={entry['ds_id']} {entry['host']}/{entry['database']} "
            f"version=**{entry['version']}** "
            f"$round_blocked={flag} "
            f"unsupported_ops={entry['unsupported_ops']}\n"
        )

    # trace 173dff87 conclusion
    lines.append("\n## Trace 173dff87 Failure-Mode Coverage\n")
    target_blocked = any(e.get("round_blocked") for e in per_ds if "error" not in e)
    if target_blocked:
        lines.append(
            "- ✅ At least one datasource blocks `$round` in agg_ops_unsupported. "
            "LLM can now see this signal in fetch_schema / estimate_cost output, "
            "preventing iter 3 blind retry on this server family.\n"
        )
    else:
        lines.append(
            "- ⚠️ No datasource currently blocks `$round`. Either all targets are >= 4.2, "
            "or buildInfo unavailable. Coverage cannot be empirically confirmed against "
            "trace 173dff87's failure mode.\n"
        )

    REPORT_PATH.write_text("".join(lines), encoding="utf-8")
    print(f"Report written: {REPORT_PATH}")

    overall = rc == 0 and rc2 == 0 and rcl == 0
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
