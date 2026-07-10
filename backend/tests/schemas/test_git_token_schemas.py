"""Pydantic schema git_token 字段测试"""
from app.schemas import (
    NamespaceCreate,
    NamespaceUpdate,
    NamespaceOut,
    GitRepoCreate,
    GitRepoOut,
)


class TestNamespaceSchemas:
    def test_create_has_git_token_default_empty(self):
        ns = NamespaceCreate(name="test", slug="test")
        assert ns.git_token == ""

    def test_create_accepts_git_token(self):
        ns = NamespaceCreate(name="test", slug="test", git_token="ghp_abc123")
        assert ns.git_token == "ghp_abc123"

    def test_update_git_token_optional(self):
        ns = NamespaceUpdate()
        assert ns.git_token is None

    def test_out_has_git_token_masked(self):
        assert "git_token_masked" in NamespaceOut.model_fields


class TestGitRepoSchemas:
    def test_create_has_git_token_default_empty(self):
        repo = GitRepoCreate(url="https://github.com/test/repo.git")
        assert repo.git_token == ""

    def test_create_accepts_git_token(self):
        repo = GitRepoCreate(url="https://github.com/test/repo.git", git_token="ghp_abc123")
        assert repo.git_token == "ghp_abc123"

    def test_out_has_git_token_masked(self):
        assert "git_token_masked" in GitRepoOut.model_fields
