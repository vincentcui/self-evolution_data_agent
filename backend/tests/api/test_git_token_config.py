"""git_token_config API 测试"""
from unittest.mock import MagicMock

from app.api.git_token_config import _to_out


class TestGitTokenConfigOut:
    def test_to_out_masks_token(self):
        cfg = MagicMock()
        cfg.id = 1
        cfg.name = "GitHub PAT"
        cfg.token = "ghp_abcdefghij"
        cfg.description = ""
        cfg.is_active = True
        cfg.created_at = "2026-01-01T00:00:00"
        cfg.updated_at = None
        cfg.created_by = 1
        out = _to_out(cfg)
        assert out.token_masked == "ghp_****hij"

    def test_to_out_empty_token(self):
        cfg = MagicMock()
        cfg.id = 1
        cfg.name = "test"
        cfg.token = ""
        cfg.description = ""
        cfg.is_active = False
        cfg.created_at = "2026-01-01T00:00:00"
        cfg.updated_at = None
        cfg.created_by = None
        out = _to_out(cfg)
        assert out.token_masked == ""
