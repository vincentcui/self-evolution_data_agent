"""Namespace API — git_token 掩码测试"""
from app.api.namespace import _mask_namespace_out
from app.knowledge.git_reachability import mask_token
from app.models.namespace import Namespace
from datetime import datetime


class TestNamespaceMaskOut:
    def test_mask_populated_correctly(self):
        """_mask_namespace_out 正确设置 git_token_masked"""
        ns = Namespace(
            id=1, name="test", slug="test", description="",
            git_token="ghp_abcdefghij",
            created_at=datetime(2026, 1, 1),
            created_by=None,
        )
        out = _mask_namespace_out(ns)
        assert out.git_token_masked == "ghp_****hij"

    def test_mask_empty_when_no_token(self):
        ns = Namespace(
            id=1, name="test", slug="test", description="",
            git_token="",
            created_at=datetime(2026, 1, 1),
            created_by=None,
        )
        out = _mask_namespace_out(ns)
        assert out.git_token_masked == ""

    def test_mask_function_consistency(self):
        """掩码函数与 mask_token 一致"""
        assert mask_token("ghp_abcdefghij") == "ghp_****hij"
        assert mask_token("") == ""
