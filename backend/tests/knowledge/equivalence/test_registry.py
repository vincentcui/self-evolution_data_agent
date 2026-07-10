"""T1 — equivalence registry 骨架测试.

5 case:
1. priority 排序: 注册顺序无关, applicable_rules 按 priority 升序返回
2. db_type 通配: rule.db_type="*" 匹配任意 db_type
3. kind 通配: rule.kind="*" 匹配任意 kind
4. applicable_rules 顺序: 同时匹配多条 rule 时按 priority 排序
5. 全部 miss: 无匹配 rule 时返回空列表

注: registry isolation 由 tests/knowledge/equivalence/conftest.py 的
`_isolate_equivalence_registry` autouse fixture 提供 — snapshot-restore 模式,
测试前清空, 测试后还原生产注册的 5 条 rule, 防止跨测试污染其它 promote 集成测.
"""

from app.knowledge.equivalence.registry import (
    EquivalenceRule,
    applicable_rules,
    register,
)
from app.knowledge.equivalence.types import EquivalenceChecker  # noqa: F401


# ── 测试用 dummy checker ──

def _dummy_checker(cands, *, namespace_id: int | None = None):
    """符合 EquivalenceChecker Protocol 的 dummy."""
    return None


class TestPrioritySorting:
    def test_rules_sorted_by_priority_regardless_of_registration_order(self):
        """注册顺序无关, applicable_rules 按 priority 升序返回."""
        register(EquivalenceRule(
            db_type="*", kind="*", priority=15,
            checker=_dummy_checker, name="low",
        ))
        register(EquivalenceRule(
            db_type="*", kind="*", priority=0,
            checker=_dummy_checker, name="high",
        ))
        register(EquivalenceRule(
            db_type="*", kind="*", priority=5,
            checker=_dummy_checker, name="mid",
        ))

        rules = applicable_rules("mysql", "field_description")
        names = [r.name for r in rules]
        assert names == ["high", "mid", "low"]


class TestDbTypeWildcard:
    def test_star_db_type_matches_any(self):
        """db_type='*' 匹配任意 db_type."""
        register(EquivalenceRule(
            db_type="*", kind="enum_values", priority=0,
            checker=_dummy_checker, name="universal",
        ))

        assert len(applicable_rules("mysql", "enum_values")) == 1
        assert len(applicable_rules("mongodb", "enum_values")) == 1
        assert len(applicable_rules("postgresql", "enum_values")) == 1

    def test_specific_db_type_only_matches_that_type(self):
        """db_type='mongodb' 只匹配 mongodb."""
        register(EquivalenceRule(
            db_type="mongodb", kind="field_description", priority=5,
            checker=_dummy_checker, name="mongo_only",
        ))

        assert len(applicable_rules("mongodb", "field_description")) == 1
        assert len(applicable_rules("mysql", "field_description")) == 0


class TestKindWildcard:
    def test_star_kind_matches_any(self):
        """kind='*' 匹配任意 kind."""
        register(EquivalenceRule(
            db_type="mysql", kind="*", priority=0,
            checker=_dummy_checker, name="any_kind",
        ))

        assert len(applicable_rules("mysql", "enum_values")) == 1
        assert len(applicable_rules("mysql", "field_description")) == 1
        assert len(applicable_rules("mysql", "sample_values")) == 1

    def test_specific_kind_only_matches_that_kind(self):
        """kind='enum_values' 只匹配 enum_values."""
        register(EquivalenceRule(
            db_type="*", kind="enum_values", priority=0,
            checker=_dummy_checker, name="enum_only",
        ))

        assert len(applicable_rules("mysql", "enum_values")) == 1
        assert len(applicable_rules("mysql", "field_description")) == 0


class TestMultiRuleOrdering:
    def test_multiple_matching_rules_ordered_by_priority(self):
        """多条 rule 同时匹配时按 priority 排序."""
        register(EquivalenceRule(
            db_type="mongodb", kind="field_description", priority=5,
            checker=_dummy_checker, name="mongo_struct",
        ))
        register(EquivalenceRule(
            db_type="*", kind="*", priority=15,
            checker=_dummy_checker, name="semantic_llm",
        ))
        register(EquivalenceRule(
            db_type="*", kind="field_description", priority=1,
            checker=_dummy_checker, name="non_empty",
        ))

        rules = applicable_rules("mongodb", "field_description")
        names = [r.name for r in rules]
        assert names == ["non_empty", "mongo_struct", "semantic_llm"]


class TestNoMatch:
    def test_empty_registry_returns_empty(self):
        """空 registry 返回空列表."""
        assert applicable_rules("mysql", "enum_values") == []

    def test_no_matching_rules_returns_empty(self):
        """有 rule 但不匹配时返回空列表."""
        register(EquivalenceRule(
            db_type="postgresql", kind="enum_values", priority=0,
            checker=_dummy_checker, name="pg_only",
        ))

        assert applicable_rules("mysql", "field_description") == []
