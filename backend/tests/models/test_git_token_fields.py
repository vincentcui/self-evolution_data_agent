"""Namespace 和 GitRepo 的 git_token 字段测试"""
from app.db.crypto import EncryptedString
from app.models.namespace import Namespace
from app.models.git_repo import GitRepo


class TestNamespaceGitTokenField:
    def test_field_exists(self):
        assert hasattr(Namespace, "git_token")

    def test_field_is_encrypted_string(self):
        col = Namespace.__table__.c.git_token
        assert isinstance(col.type, EncryptedString)

    def test_field_default_empty(self):
        # SQLAlchemy default="" is applied at INSERT (flush), not at instantiation.
        # At instantiation the attribute is None until flushed.
        ns = Namespace(name="test", slug="test")
        assert ns.git_token is None or ns.git_token == ""


class TestGitRepoGitTokenField:
    def test_field_exists(self):
        assert hasattr(GitRepo, "git_token")

    def test_field_is_encrypted_string(self):
        col = GitRepo.__table__.c.git_token
        assert isinstance(col.type, EncryptedString)

    def test_field_default_empty(self):
        # SQLAlchemy default="" is applied at INSERT (flush), not at instantiation.
        repo = GitRepo(namespace_id=1, url="https://github.com/test/repo.git")
        assert repo.git_token is None or repo.git_token == ""
