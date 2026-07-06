"""Flavor 感知能力探测 (设计 A2/A3/A4/A5).

- ProfileRegistry: 从本地 flavor_profiles/ 目录加载可插拔能力档案 (零网络)
- detect_flavor:   按 buildInfo 结构签名判定 flavor (纯函数, native 默认回退)
- compute_capabilities: 按 flavor 计算三类能力限制 (native 走 Op_Version_Table)
- build_capabilities:    失败安全包装 (探测/计算/加载异常 → 日志 + native 回退)

档案为静态只读 JSON, 走 json.loads + schema 校验, 不经过 parse_llm_json
(那条硬规则只约束 LLM 响应解析)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.engine.drivers.mongo_capabilities import compute_unsupported_ops
from app.engine.drivers.mongo_flavor_predicate import (
    KNOWN_PREDICATE_KEYS,
    collect_predicate_keys,
    evaluate,
)
from app.logging_config import get_logger

log = get_logger("mongo_flavor")

NATIVE_FLAVOR = "mongodb"
_PROFILES_DIR = Path(__file__).parent / "flavor_profiles"

_REQUIRED_TOP_KEYS = (
    "flavor",
    "display_name",
    "priority",
    "detection",
    "capability_restrictions",
    "equivalent_hints",
)
_REQUIRED_RESTRICTION_KEYS = (
    "unsupported_ops",
    "unsupported_stage_variants",
    "syntax_constraints",
)


class ProfileLoadError(Exception):
    """任一档案非法 → 全量加载失败 (fail-safe-loud, R4.6)。"""


@dataclass(frozen=True)
class FlavorProfile:
    flavor: str
    display_name: str
    priority: int
    detection: dict
    capability_restrictions: dict
    equivalent_hints: list = field(default_factory=list)


def _validate_and_build(raw: object, source: str) -> FlavorProfile:
    """校验单个档案 dict 并构造 FlavorProfile; 非法抛 ProfileLoadError。"""
    if not isinstance(raw, dict):
        raise ProfileLoadError(f"{source}: 档案根必须是 object")
    for k in _REQUIRED_TOP_KEYS:
        if k not in raw:
            raise ProfileLoadError(f"{source}: 缺必需顶层键 {k!r}")
    if not isinstance(raw["flavor"], str) or not raw["flavor"]:
        raise ProfileLoadError(f"{source}: flavor 必须是非空字符串")
    if not isinstance(raw["priority"], int):
        raise ProfileLoadError(f"{source}: priority 必须是 int")
    detection = raw["detection"]
    if not isinstance(detection, dict) or not detection:
        raise ProfileLoadError(f"{source}: detection 必须是非空 object")
    allowed = KNOWN_PREDICATE_KEYS | {"field", "value", "values"}
    unknown = collect_predicate_keys(detection) - allowed
    if unknown:
        raise ProfileLoadError(f"{source}: detection 含未知谓词键 {sorted(unknown)}")
    restrictions = raw["capability_restrictions"]
    if not isinstance(restrictions, dict):
        raise ProfileLoadError(f"{source}: capability_restrictions 必须是 object")
    for rk in _REQUIRED_RESTRICTION_KEYS:
        if rk not in restrictions:
            raise ProfileLoadError(f"{source}: capability_restrictions 缺 {rk!r}")
        if not isinstance(restrictions[rk], list):
            raise ProfileLoadError(f"{source}: capability_restrictions.{rk} 必须是 list")
    if not isinstance(raw["equivalent_hints"], list):
        raise ProfileLoadError(f"{source}: equivalent_hints 必须是 list")
    return FlavorProfile(
        flavor=raw["flavor"],
        display_name=raw["display_name"],
        priority=raw["priority"],
        detection=detection,
        capability_restrictions=restrictions,
        equivalent_hints=raw["equivalent_hints"],
    )


class ProfileRegistry:
    """加载并按 priority 降序排列所有 Flavor_Profile 的本地组件 (R4)。"""

    def __init__(self, profiles_dir: Path | None = None) -> None:
        self._dir = profiles_dir or _PROFILES_DIR
        self._profiles: list[FlavorProfile] | None = None

    def load(self) -> list[FlavorProfile]:
        """从 profiles_dir 加载全部 *.json 档案, 按 priority 降序返回。

        R4.1: 纯本地文件读取, 零网络。
        R4.6: 任一档案非法 → 抛 ProfileLoadError (全量失败, 不部分加载)。
        R4.8: 全部合法 → 加载全部直至目录遍历完毕。
        """
        if self._profiles is not None:
            return self._profiles
        profiles: list[FlavorProfile] = []
        if self._dir.is_dir():
            for path in sorted(self._dir.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    raise ProfileLoadError(f"{path.name}: 读取/解析失败: {e}") from e
                profiles.append(_validate_and_build(raw, path.name))
        profiles.sort(key=lambda p: p.priority, reverse=True)
        self._profiles = profiles
        return profiles

    def non_native_profiles(self) -> list[FlavorProfile]:
        """priority 降序的非原生 profile 列表, 供 detect_flavor 遍历 (R1.6)。"""
        return [p for p in self.load() if p.flavor != NATIVE_FLAVOR]

    def get(self, flavor: str) -> FlavorProfile | None:
        """按 flavor 标识符取 profile; native 返回 None (走 Op_Version_Table)。"""
        if flavor == NATIVE_FLAVOR:
            return None
        for p in self.load():
            if p.flavor == flavor:
                return p
        return None


@lru_cache(maxsize=1)
def get_profile_registry() -> ProfileRegistry:
    """进程级单例; 首次调用加载档案目录, 结果缓存 (档案是离线静态文件)。"""
    reg = ProfileRegistry()
    reg.load()  # 触发一次加载 (坏档案在此即抛 ProfileLoadError)
    return reg


# ── A2: detect_flavor ────────────────────────────────────

def detect_flavor(build_info: dict, registry: ProfileRegistry | None = None) -> str:
    """依据 buildInfo 结构签名判定 flavor。纯函数 / total (R1)。

    仅消费 build_info 结构特征, 不接受 host/port/URI。无非原生 profile 命中 → native。
    """
    reg = registry or get_profile_registry()
    for profile in reg.non_native_profiles():
        if evaluate(profile.detection, build_info):
            return profile.flavor
    return NATIVE_FLAVOR


# ── A4: compute_capabilities ─────────────────────────────

def compute_capabilities(
    flavor: str,
    version: str,
    registry: ProfileRegistry | None = None,
) -> dict:
    """按已判定 flavor 计算三类能力限制 (R2)。始终令 agg_ops_unsupported == unsupported_ops。"""
    reg = registry or get_profile_registry()
    profile = reg.get(flavor)
    if profile is None:
        # 原生路径: 沿用版本表 (R2.2)
        unsupported_ops = compute_unsupported_ops(version)
        stage_variants: list[str] = []
        syntax_constraints: list[str] = []
        hints: list = []
        resolved_flavor = NATIVE_FLAVOR
    else:
        restrictions = profile.capability_restrictions
        unsupported_ops = list(restrictions.get("unsupported_ops", []))
        stage_variants = list(restrictions.get("unsupported_stage_variants", []))
        syntax_constraints = list(restrictions.get("syntax_constraints", []))
        hints = list(profile.equivalent_hints)
        resolved_flavor = profile.flavor
    return {
        "version": version,
        "flavor": resolved_flavor,
        "unsupported_ops": unsupported_ops,
        "unsupported_stage_variants": stage_variants,
        "syntax_constraints": syntax_constraints,
        "equivalent_hints": hints,
        "agg_ops_unsupported": unsupported_ops,  # deprecated alias, 同值
    }


# ── A5: build_capabilities (失败安全包装) ────────────────

def build_capabilities(build_info: dict, version: str) -> dict:
    """flavor 探测 + 能力计算的失败安全包装 (R3.5 / R4.6 / R4.7)。

    探测/计算/档案加载异常 → 先成功写错误日志 → 回退原生 mongodb 能力计算。
    日志写入本身失败 → 抛出 (整个能力操作失败, 不静默回退, R4.7)。
    """
    try:
        flavor = detect_flavor(build_info)
        return compute_capabilities(flavor, version)
    except Exception as exc:  # noqa: BLE001 — 探测/计算/加载异常统一回退
        # R4.7: 日志写入失败则整个操作失败, 不静默回退
        log.warning(
            "[mongo_flavor] flavor 探测/能力计算失败, 回退原生 mongodb 能力: %s", exc
        )
        # 回退: 直接走原生路径 (不再触发 profile 加载)
        unsupported_ops = compute_unsupported_ops(version)
        return {
            "version": version,
            "flavor": NATIVE_FLAVOR,
            "unsupported_ops": unsupported_ops,
            "unsupported_stage_variants": [],
            "syntax_constraints": [],
            "equivalent_hints": [],
            "agg_ops_unsupported": unsupported_ops,
        }
