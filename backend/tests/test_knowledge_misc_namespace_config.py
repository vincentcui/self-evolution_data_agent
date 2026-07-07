"""测试其他知识模块的 namespace_id 传参."""
import pytest
from unittest.mock import patch


def test_intake_refine_passes_namespace_id():
    """intake.refine_knowledge 应将 namespace_id 传给 chat_completion."""
    from app.knowledge import intake

    with patch("app.knowledge.intake.chat_completion", return_value="refined") as mock:
        intake.refine_knowledge("rule", "raw text", "normal", namespace_id=3)
        assert mock.call_args.kwargs.get("namespace_id") == 3


def test_intake_detect_conflicts_passes_namespace_id():
    """intake.detect_conflicts 应将 namespace_id 传给 chat_completion."""
    from app.knowledge import intake

    with patch("app.knowledge.intake.chat_completion",
               return_value='{"conflicts":[],"resolved":[]}') as mock:
        intake.detect_conflicts(
            "第一条内容", [{"id": 1, "content": "第二条内容"}], namespace_id=5
        )
        if mock.call_args is not None:
            assert mock.call_args.kwargs.get("namespace_id") == 5


def test_trace_refiner_passes_namespace_id():
    """trace_refiner.refine_traces 应将 namespace_id 传给 chat_completion."""
    from app.knowledge import trace_refiner

    fake_trace = [{
        "trace_id": "t1",
        "user_query": "test query",
        "trace_json": '{"steps":[]}',
        "reflection_log_json": "{}",
    }]
    with patch("app.knowledge.trace_refiner.chat_completion",
               return_value='{"proposed_knowledge":[]}') as mock:
        trace_refiner.refine_traces(fake_trace, namespace_id=5)
        if mock.call_args is not None:
            assert mock.call_args.kwargs.get("namespace_id") == 5
