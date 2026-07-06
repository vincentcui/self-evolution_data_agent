"""db_profile 按场景投影. 落库 db_profile_json 含全量, 暴露给 LLM 按 mode 取子集."""
_OVERVIEW_KEYS = ("version", "flavor", "charset", "object_count")
_SCHEMA_KEYS = ("version", "flavor", "charset")
_CAPS_KEYS = (
    "flavor", "unsupported_ops", "unsupported_stage_variants",
    "syntax_constraints", "equivalent_hints",
)


def _project_db_profile(profile: dict, mode: str) -> dict:
    keys = {"overview": _OVERVIEW_KEYS, "schema": _SCHEMA_KEYS, "caps": _CAPS_KEYS}.get(mode, ())
    return {k: profile[k] for k in keys if k in profile}


def _project_schema_caps(profile: dict) -> dict:
    """schema ∪ caps merge: fetch_schema / estimate_cost 供 LLM 一次拿全 schema + 能力限制.

    单次解析 db_profile_json 后调此函数, 避免三处调用点重复 {**schema, **caps} + 双解析.
    """
    return {**_project_db_profile(profile, "schema"), **_project_db_profile(profile, "caps")}
