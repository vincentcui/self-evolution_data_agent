"""迁移脚本纯函数测试 (spec 2026-07-08 C8)."""
from scripts.migrate_route_hint_payload import transform_route_hint_payload


def test_transform_reason_to_navigation_note():
    old = {"question_pattern": "q", "collection_path": ["a", "b"],
           "join_fields": [{"a": "x", "b": "y"}], "cost_strategy": "default",
           "avoid_path": [], "reason": "走 sku 关联"}
    out = transform_route_hint_payload(old)
    assert out == {"collection_path": ["a", "b"], "navigation_note": "走 sku 关联"}


def test_transform_missing_reason_defaults_empty():
    old = {"question_pattern": "q", "collection_path": ["a"]}
    out = transform_route_hint_payload(old)
    assert out == {"collection_path": ["a"], "navigation_note": ""}


def test_transform_missing_collection_path_defaults_empty():
    old = {"reason": "r"}
    out = transform_route_hint_payload(old)
    assert out == {"collection_path": [], "navigation_note": "r"}


def test_transform_idempotent_skip():
    """已是新 shape (含 navigation_note) → 返新 shape 不动 (幂等守卫在调用层判断)."""
    old = {"collection_path": ["a"], "navigation_note": "n"}
    out = transform_route_hint_payload(old)
    assert out == {"collection_path": ["a"], "navigation_note": "n"}


def test_transform_drops_all_old_fields():
    old = {"question_pattern": "q", "collection_path": [], "join_fields": [],
           "cost_strategy": "default", "avoid_path": ["x"], "reason": "r",
           "extra_unknown": "drop_me"}
    out = transform_route_hint_payload(old)
    assert set(out.keys()) == {"collection_path", "navigation_note"}
