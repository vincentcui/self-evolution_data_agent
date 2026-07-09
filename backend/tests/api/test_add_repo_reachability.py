"""add_repo — 可达性校验测试"""
from app.api.knowledge import _enrich_repo_out
from app.models.git_repo import GitRepo


class TestAddRepoReachability:
    def test_mask_token_in_enrich_repo_out(self):
        """验证 _enrich_repo_out 包含 git_token_masked"""
        repo = GitRepo(
            id=1, namespace_id=1, url="https://github.com/test/repo.git",
            branch="master", git_token="ghp_abcdefghij",
        )
        out = _enrich_repo_out(repo)
        assert out["git_token_masked"] == "ghp_****hij"

    def test_mask_token_empty_when_no_token(self):
        repo = GitRepo(
            id=1, namespace_id=1, url="https://github.com/test/repo.git",
            branch="master", git_token="",
        )
        out = _enrich_repo_out(repo)
        assert out["git_token_masked"] == ""
