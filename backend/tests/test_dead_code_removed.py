"""死代码清零守卫 (spec 2026-07-08 C5/G3)."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # backend/

DEAD_SYMBOLS = [
    "extract_join_fields", "derive_cost_strategy", "extract_final_pipeline",
    "_batch_load_route_hints", "RouteHintCandidate", "route_hints_for_prompt",
    "route_hint_reason",
    "_extract_field_mappings", "PROBE_TOOLS", "FIELD_PROBE_TOOLS",
]


def test_dead_symbols_removed_from_app():
    """backend/app 内不得残留死符号 (测试目录除外).

    注: grep 每个 pattern 必须走 -e, 否则 `grep a b c PATH` 只有 a 是 pattern,
    b/c 被当文件路径 (不存在) → 零命中恒空过, 守卫形同虚设.
    """
    args = ["grep", "-rln", "--include=*.py"]
    for s in DEAD_SYMBOLS:
        args += ["-e", s]
    args.append(str(ROOT / "app"))
    result = subprocess.run(args, capture_output=True, text=True)
    # grep 命中返 0 (有残留 → 应失败); 未命中返 1 (干净 → 应通过)
    assert result.returncode != 0 or not result.stdout.strip(), (
        f"app 内仍残留死符号:\n{result.stdout}"
    )
