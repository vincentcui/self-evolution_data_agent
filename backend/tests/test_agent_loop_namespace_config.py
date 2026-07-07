"""测试 agent_loop 将 namespace_id 传给 LLM 调用."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_agent_loop_production_path_binds_namespace_id_via_partial():
    """生产路径 (llm=None) 应通过 functools.partial 将 namespace_id 绑定到 chat_completion_with_tools."""
    from app.engine.agent_loop import run_agent_loop
    from app.engine.llm import ToolUseResponse

    fake_response = ToolUseResponse(
        text="done", tool_calls=[], stop_reason="end_turn", usage={}
    )

    with patch("app.engine.agent_loop.chat_completion_with_tools",
               new_callable=AsyncMock, return_value=fake_response) as mock_llm_fn:
        await run_agent_loop(
            trace_id="test-trace",
            question="test",
            tools_registry={},
            tool_specs=[],
            sse_emit=AsyncMock(),
            user_correction_queue=asyncio.Queue(),
            llm=None,  # 生产路径：不注入 mock
            namespace_id=42,
        )

        call_kwargs = mock_llm_fn.call_args.kwargs
        assert call_kwargs.get("namespace_id") == 42, (
            "生产路径下 namespace_id 未传递给 chat_completion_with_tools"
        )
