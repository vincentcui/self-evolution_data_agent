"""GitTokenConfig 模型测试"""
from app.db.crypto import EncryptedString
from app.models.git_token_config import GitTokenConfig


class TestGitTokenConfigModel:
    def test_field_exists(self):
        assert hasattr(GitTokenConfig, "token")

    def test_token_is_encrypted_string(self):
        col = GitTokenConfig.__table__.c.token
        assert isinstance(col.type, EncryptedString)

    def test_default_values(self):
        # SQLAlchemy defaults are applied at INSERT (flush), not at instantiation.
        cfg = GitTokenConfig(name="test")
        assert cfg.token is None or cfg.token == ""
        assert cfg.is_active is None or cfg.is_active is False
        assert cfg.is_deleted is None or cfg.is_deleted is False
