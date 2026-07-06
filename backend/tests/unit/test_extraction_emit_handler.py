"""emit handler dispatch — validate_emit chain via mocked LLM. No real LLM."""
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from app.knowledge.extraction_agent import run_extraction_agent

pytestmark = pytest.mark.asyncio


@dataclass
class _TC:
    id: str
    name: str
    input: dict
    parse_error: str | None = None


@dataclass
class _Resp:
    text: str = ""
    tool_calls: list = None
    reasoning_content: str | None = None


async def test_emit_handler_appends_valid_and_rejects_invalid():
    """有效 emit → 入 objects; 非法 paradigm → validate_emit 拒绝, 不入 objects."""
    valid = _Resp(tool_calls=[_TC("c1", "emit_schema_object", {
        "paradigm": "relational", "kind": "table", "name": "orders",
        "fields": [{"name": "id", "type": "Long"}], "source_ref": "Order.java:1",
    })])
    invalid = _Resp(tool_calls=[_TC("c2", "emit_schema_object", {
        "paradigm": "graph", "kind": "table", "name": "bad",
        "fields": [{"name": "x", "type": "String"}], "source_ref": "Bad.java:1",
    })])
    end = _Resp(text="done", tool_calls=[])

    mock_llm = AsyncMock(side_effect=[valid, invalid, end])
    with patch("app.knowledge.extraction_agent.chat_completion_with_tools", mock_llm):
        result = await run_extraction_agent(repo_path="/tmp/fake", max_iterations=10)

    names = [o.get("name") for o in result.objects]
    assert "orders" in names, "有效对象应入库"
    assert "bad" not in names, "非法 paradigm 应被 validate_emit 拒绝, 不入库"


async def test_emit_handler_no_typeerror_on_dispatch():
    """fn(**tc.input) → _handler(**data) 签名匹配, 不抛 TypeError."""
    valid = _Resp(tool_calls=[_TC("c1", "emit_schema_object", {
        "paradigm": "document", "kind": "collection", "name": "products",
        "fields": [{"name": "_id", "type": "ObjectId"}], "source_ref": "Product.java:1",
    })])
    end = _Resp(text="done", tool_calls=[])
    mock_llm = AsyncMock(side_effect=[valid, end])
    with patch("app.knowledge.extraction_agent.chat_completion_with_tools", mock_llm):
        result = await run_extraction_agent(repo_path="/tmp/fake", max_iterations=10)
    assert result.status == "ok"
    assert "products" in [o.get("name") for o in result.objects]
