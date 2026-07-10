"""trace_refiner 不再产 route_hint (spec 2026-07-08 C4)."""
from app.knowledge.trace_refiner import ALLOWED_TYPES, _PROMPT


def test_allowed_types_excludes_route_hint():
    assert "route_hint" not in ALLOWED_TYPES
    assert ALLOWED_TYPES == frozenset({"terminology", "instance_alias", "example", "rule"})


def test_prompt_has_no_route_hint():
    assert "route_hint" not in _PROMPT
