"""trainer — token 参数透传测试"""
from unittest.mock import patch, AsyncMock, MagicMock

import pytest


class TestTrainerTokenPassThrough:
    """验证 run_training_pipeline_with_progress 接受 token 并传给 clone_or_update"""

    @pytest.mark.asyncio
    @patch("app.knowledge.explain_gate.write_extraction_failure", new_callable=AsyncMock)
    @patch("app.knowledge.skeleton.orchestrator.orchestrated_extraction", new_callable=AsyncMock)
    @patch("app.knowledge.trainer.async_session")
    @patch("app.knowledge.trainer.clone_or_update")
    @patch("app.knowledge.trainer._update_repo_status", new_callable=AsyncMock)
    @patch("app.knowledge.trainer._update_repo_fields", new_callable=AsyncMock)
    @patch("app.knowledge.trainer.purge_legacy_for_full_rebuild", new_callable=AsyncMock)
    @patch("app.knowledge.trainer._load_profile_hint", new_callable=AsyncMock)
    @patch("app.knowledge.trainer._sync_trace_id_to_log")
    async def test_token_passed_to_clone_or_update(
        self, mock_sync, mock_hint, mock_purge, mock_fields, mock_status,
        mock_clone, mock_session, mock_ext, mock_write_fail,
    ):
        """token 参数透传到 clone_or_update"""
        from app.knowledge.trainer import run_training_pipeline_with_progress

        mock_clone.return_value = ("/tmp/repo", "clone")
        mock_hint.return_value = None
        mock_purge.return_value = {}

        # mock async_session
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

        # orchestrated_extraction 返回 failed → 提前返回
        mock_result = MagicMock()
        mock_result.status = "failed"
        mock_result.reason = "test mock"
        mock_ext.return_value = mock_result

        on_progress = AsyncMock()

        await run_training_pipeline_with_progress(
            1, 1, "test", "https://github.com/test/repo.git", "master",
            on_progress,
            token="my_token",
        )

        # 验证 clone_or_update 被调用时传了 token
        call_args = mock_clone.call_args
        assert call_args is not None, "clone_or_update was not called"
        assert call_args.kwargs.get("token") == "my_token"
