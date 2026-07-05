from app.engine.tools._db_profile_projector import _project_db_profile


def test_overview_mode():
    profile = {"profiled_at":"2026-07-01","version":"8.0","charset":"utf8mb4",
               "flavor":"mongo","object_count":42,"error":None}
    out = _project_db_profile(profile, "overview")
    assert out == {"version":"8.0","charset":"utf8mb4","flavor":"mongo","object_count":42}

def test_schema_mode_strips_object_count():
    profile = {"version":"8.0","charset":"utf8mb4","flavor":"mongo","object_count":42}
    out = _project_db_profile(profile, "schema")
    assert out == {"version":"8.0","charset":"utf8mb4","flavor":"mongo"}
    assert "object_count" not in out

def test_filters_profiled_at_error():
    profile = {"profiled_at":"x","version":"8.0","error":"boom"}
    out = _project_db_profile(profile, "schema")
    assert "profiled_at" not in out and "error" not in out

def test_missing_keys_degrade():
    out = _project_db_profile({"version":"8.0"}, "schema")
    assert out == {"version":"8.0"}  # 缺 charset/flavor 不报错


def test_caps_mode():
    profile = {
        "version": "5.0", "flavor": "documentdb",
        "unsupported_ops": ["$median"], "unsupported_stage_variants": [],
        "syntax_constraints": [], "equivalent_hints": [], "object_count": 3,
    }
    out = _project_db_profile(profile, "caps")
    assert out == {
        "flavor": "documentdb", "unsupported_ops": ["$median"],
        "unsupported_stage_variants": [], "syntax_constraints": [],
        "equivalent_hints": [],
    }
    assert "version" not in out and "object_count" not in out
