"""Shared query-shape classifier + its three consumers (count / data reads / estimate_cost).

Task 2.1 — CI-level hard regression gate for BUG 1 (count-mode ignored `pipeline`).
Uses an in-memory fake motor collection with generic MongoDB $match/$count/$limit
semantics — NO live DocumentDB, runs in the normal backend CI suite.

cd backend && python -m pytest tests/drivers/test_mongo_query_shape.py -q \
    --timeout=120 --timeout-method=thread
"""
from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from app.engine.drivers.mongo import MongoDriver, _classify_query_shape, _decode_extended_json
from app.models import DataSource

# ──────────────────────────────────────────────────────────
#  In-memory fake motor collection (generic MongoDB semantics)
# ──────────────────────────────────────────────────────────

def _match_value(cond: object, value: object) -> bool:
    """Apply a single field condition (equality or a small operator subset)."""
    if isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
        for op, operand in cond.items():
            if op == "$options":
                continue
            if op == "$in":
                if value not in operand:
                    return False
            elif op == "$nin":
                if value in operand:
                    return False
            elif op == "$ne":
                if value == operand:
                    return False
            elif op == "$gt":
                if value is None or not value > operand:
                    return False
            elif op == "$gte":
                if value is None or not value >= operand:
                    return False
            elif op == "$lt":
                if value is None or not value < operand:
                    return False
            elif op == "$lte":
                if value is None or not value <= operand:
                    return False
            elif op == "$regex":
                flags = re.IGNORECASE if "i" in cond.get("$options", "") else 0
                if value is None or re.search(operand, str(value), flags) is None:
                    return False
            else:  # unknown operator → conservative no-match
                return False
        return True
    return value == cond


def _match_doc(doc: dict, query: dict) -> bool:
    for key, cond in query.items():
        if key == "$and":
            if not all(_match_doc(doc, sub) for sub in cond):
                return False
        elif key == "$or":
            if not any(_match_doc(doc, sub) for sub in cond):
                return False
        elif not _match_value(cond, doc.get(key)):
            return False
    return True


class _AsyncIter:
    """Async-iterable wrapper, mirroring a motor aggregate cursor."""

    def __init__(self, items: list[dict]) -> None:
        self._items = items

    async def __aiter__(self):  # noqa: ANN204 — async generator
        for it in self._items:
            yield it


class FakeCursor:
    """Mimics `coll.find(filter).limit(n)` → async-iterable cursor."""

    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs
        self._limit: int | None = None

    def limit(self, n: int) -> "FakeCursor":
        self._limit = n
        return self

    async def __aiter__(self):  # noqa: ANN204
        docs = self._docs if self._limit is None else self._docs[: self._limit]
        for d in docs:
            yield d


class FakeCollection:
    """Minimal in-memory motor collection: real $match/$count/$limit/$skip semantics."""

    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)
        self.aggregate_pipelines: list[list[dict]] = []
        self.find_filters: list[dict] = []
        self.find_limits: list[int | None] = []

    async def count_documents(self, filter_: dict) -> int:
        return sum(1 for d in self._docs if _match_doc(d, filter_))

    def aggregate(self, pipeline: list[dict]) -> _AsyncIter:
        self.aggregate_pipelines.append(pipeline)
        out = list(self._docs)
        for stage in pipeline:
            if "$match" in stage:
                out = [d for d in out if _match_doc(d, stage["$match"])]
            elif "$skip" in stage:
                out = out[stage["$skip"]:]
            elif "$limit" in stage:
                out = out[: stage["$limit"]]
            elif "$count" in stage:
                field = stage["$count"]
                out = [{field: len(out)}] if out else []
        return _AsyncIter(out)

    def find(self, filter_: dict) -> FakeCursor:
        self.find_filters.append(filter_)
        matched = [d for d in self._docs if _match_doc(d, filter_)]
        cursor = FakeCursor(matched)
        # capture limit when set
        orig_limit = cursor.limit

        def _track(n: int) -> FakeCursor:
            self.find_limits.append(n)
            return orig_limit(n)

        cursor.limit = _track  # type: ignore[assignment]
        return cursor


def make_ds(ds_id: int = 1) -> DataSource:
    ds = MagicMock(spec=DataSource)
    ds.id = ds_id
    ds.host = "h"
    ds.port = 27017
    ds.username = "u"
    ds.password = "p"
    ds.database = "db"
    return ds


def driver_with(coll: FakeCollection, ds_id: int = 1) -> MongoDriver:
    """Build a MongoDriver wired to a fake collection (no real connection)."""
    driver = MongoDriver()
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = coll
    client = MagicMock()
    client.__getitem__.return_value = fake_db
    driver._clients[ds_id] = client  # bypass _get_client real connect
    return driver


# 10 docs total; 3 match {"category": "phone"} → K=3, T=10 (K < T).
SEED_DOCS = (
    [{"_id": i, "category": "phone"} for i in range(3)]
    + [{"_id": 100 + i, "category": "laptop"} for i in range(7)]
)
MATCH = {"category": "phone"}
K = 3
T = 10


# ──────────────────────────────────────────────────────────
#  _classify_query_shape
# ──────────────────────────────────────────────────────────

class TestClassifyQueryShape:
    def test_pipeline_shape(self):
        pipe = [{"$match": {"x": 1}}]
        assert _classify_query_shape({"pipeline": pipe}) == ("aggregate", pipe)

    def test_filter_shape(self):
        flt = {"x": 1}
        assert _classify_query_shape({"filter": flt}) == ("filter", flt)

    def test_empty_pipeline_is_aggregate(self):
        # is-not-None (not truthiness): empty [] → aggregate
        assert _classify_query_shape({"pipeline": []}) == ("aggregate", [])

    def test_neither_key_defaults_to_empty_filter(self):
        assert _classify_query_shape({}) == ("filter", {})


# ──────────────────────────────────────────────────────────
#  HARD GATE — the 14783 falsification (BUG 1)
# ──────────────────────────────────────────────────────────

class TestCountHardGate:
    @pytest.mark.asyncio
    async def test_pipeline_and_filter_counts_agree_and_differ_from_total(self):
        """Pipeline-shape count == filter-shape count == K, and != whole-collection T.

        Fails on the old code (count branch read only `filter`, so the pipeline
        form returned T). Durable, ds-independent proof of BUG 1.
        """
        coll = FakeCollection(SEED_DOCS)
        driver = driver_with(coll)
        ds = make_ds()

        pipeline_res = await driver.execute_query(
            ds, "c", {"pipeline": [{"$match": MATCH}]}, mode="count"
        )
        filter_res = await driver.execute_query(
            ds, "c", {"filter": MATCH}, mode="count"
        )

        pipeline_count = pipeline_res["rows"][0]["count"]
        filter_count = filter_res["rows"][0]["count"]

        assert pipeline_count == K
        assert filter_count == K
        assert pipeline_count == filter_count
        assert pipeline_count != T  # the load-bearing inequality (was 14783==14783)


# ──────────────────────────────────────────────────────────
#  Count branch behavior
# ──────────────────────────────────────────────────────────

class TestCountBehavior:
    @pytest.mark.asyncio
    async def test_filter_path_uses_count_documents_unchanged(self):
        coll = FakeCollection(SEED_DOCS)
        driver = driver_with(coll)
        res = await driver.execute_query(
            make_ds(), "c", {"filter": MATCH}, mode="count"
        )
        assert res["rows"] == [{"count": K}]
        assert res["row_count"] == 1
        assert res["truncated"] is False
        # filter path must not touch aggregate
        assert coll.aggregate_pipelines == []

    @pytest.mark.asyncio
    async def test_zero_match_pipeline_returns_count_zero(self):
        coll = FakeCollection(SEED_DOCS)
        driver = driver_with(coll)
        res = await driver.execute_query(
            make_ds(), "c", {"pipeline": [{"$match": {"category": "nope"}}]},
            mode="count",
        )
        assert res["rows"] == [{"count": 0}]

    @pytest.mark.asyncio
    async def test_caller_pipeline_not_mutated(self):
        coll = FakeCollection(SEED_DOCS)
        driver = driver_with(coll)
        caller_pipeline = [{"$match": MATCH}]
        await driver.execute_query(
            make_ds(), "c", {"pipeline": caller_pipeline}, mode="count"
        )
        # the appended {"$count": "count"} must stay local
        assert caller_pipeline == [{"$match": MATCH}]

    @pytest.mark.asyncio
    async def test_count_output_field_named_count(self):
        coll = FakeCollection(SEED_DOCS)
        driver = driver_with(coll)
        res = await driver.execute_query(
            make_ds(), "c", {"pipeline": [{"$match": MATCH}]}, mode="count"
        )
        assert list(res["rows"][0].keys()) == ["count"]

    @pytest.mark.asyncio
    async def test_empty_pipeline_counts_whole_collection(self):
        coll = FakeCollection(SEED_DOCS)
        driver = driver_with(coll)
        res = await driver.execute_query(
            make_ds(), "c", {"pipeline": []}, mode="count"
        )
        assert res["rows"] == [{"count": T}]


# ──────────────────────────────────────────────────────────
#  Data-read branch (single) — unchanged after refactor
# ──────────────────────────────────────────────────────────

class TestDataReadShape:
    @pytest.mark.asyncio
    async def test_pipeline_appends_limit_and_aggregates(self):
        coll = FakeCollection(SEED_DOCS)
        driver = driver_with(coll)
        res = await driver.execute_query(
            make_ds(), "c", {"pipeline": [{"$match": MATCH}]}, mode="single"
        )
        assert res["row_count"] == K
        # exactly one aggregate, with a trailing $limit appended
        assert len(coll.aggregate_pipelines) == 1
        assert coll.aggregate_pipelines[0][-1] == {"$limit": 1000}
        # find() not used on the aggregate path
        assert coll.find_filters == []

    @pytest.mark.asyncio
    async def test_filter_uses_find_with_limit(self):
        coll = FakeCollection(SEED_DOCS)
        driver = driver_with(coll)
        res = await driver.execute_query(
            make_ds(), "c", {"filter": MATCH}, mode="single"
        )
        assert res["row_count"] == K
        assert coll.find_filters == [MATCH]
        assert coll.find_limits == [1000]
        # filter path must not touch aggregate
        assert coll.aggregate_pipelines == []

    @pytest.mark.asyncio
    async def test_caller_pipeline_not_mutated_on_data_read(self):
        coll = FakeCollection(SEED_DOCS)
        driver = driver_with(coll)
        caller_pipeline = [{"$match": MATCH}]
        await driver.execute_query(
            make_ds(), "c", {"pipeline": caller_pipeline}, mode="single"
        )
        assert caller_pipeline == [{"$match": MATCH}]


# ──────────────────────────────────────────────────────────
#  estimate_cost — empty pipeline [] now explains aggregate
# ──────────────────────────────────────────────────────────

class TestEstimateCostShape:
    def _driver_with_command_capture(self, ds_id: int = 1):
        driver = MongoDriver()
        fake_coll = MagicMock()
        fake_coll.estimated_document_count = AsyncMock(return_value=5)
        fake_db = MagicMock()
        fake_db.command = AsyncMock(return_value={"ok": 1})
        fake_db.__getitem__.return_value = fake_coll
        client = MagicMock()
        client.__getitem__.return_value = fake_db
        driver._clients[ds_id] = client
        return driver, fake_db

    @pytest.mark.asyncio
    async def test_empty_pipeline_explains_aggregate(self):
        # regression guard for the `if pipeline:` truthiness fix
        driver, fake_db = self._driver_with_command_capture()
        await driver.estimate_cost(make_ds(), "c", {"pipeline": []})
        args, _ = fake_db.command.call_args
        assert args[0] == "explain"
        assert "aggregate" in args[1]
        assert args[1]["pipeline"] == []

    @pytest.mark.asyncio
    async def test_nonempty_pipeline_explains_aggregate(self):
        driver, fake_db = self._driver_with_command_capture()
        pipe = [{"$match": MATCH}]
        await driver.estimate_cost(make_ds(), "c", {"pipeline": pipe})
        args, _ = fake_db.command.call_args
        assert "aggregate" in args[1]
        assert args[1]["pipeline"] == pipe

    @pytest.mark.asyncio
    async def test_filter_explains_find(self):
        driver, fake_db = self._driver_with_command_capture()
        await driver.estimate_cost(make_ds(), "c", {"filter": MATCH})
        args, _ = fake_db.command.call_args
        assert "find" in args[1]
        assert args[1]["filter"] == MATCH


# ──────────────────────────────────────────────────────────
#  Extended JSON ($date/...) decode — trace 6de74455 根因
#  LLM 用 {"$date":"...Z"} 表达日期; parse_llm_json 不解码, 原样
#  dict 透传给 motor → pymongo 编码成 Object (type 3) 而非 Date (type 9)
#  → $match 跨类型比较恒假 → 0 行. 驱动入口须解码成真 datetime.
# ──────────────────────────────────────────────────────────

class TestExtendedJsonDecode:
    """$date Extended JSON 必须在驱动边界解码为 datetime, 否则 0 行."""

    @pytest.mark.asyncio
    async def test_execute_query_decodes_dollar_date_to_datetime(self):
        coll = FakeCollection([])
        driver = driver_with(coll)
        query = {
            "pipeline": [
                {"$match": {"createTime": {
                    "$gte": {"$date": "2026-06-01T00:00:00.000Z"},
                    "$lt": {"$date": "2026-07-01T00:00:00.000Z"},
                }}},
                {"$group": {"_id": "$createUser", "courseCount": {"$sum": 1}}},
                {"$sort": {"courseCount": -1}},
            ]
        }
        await driver.execute_query(make_ds(), "c", query, mode="single")
        captured = coll.aggregate_pipelines[-1]
        match = captured[0]["$match"]
        gte = match["createTime"]["$gte"]
        lt = match["createTime"]["$lt"]
        # 修复前: gte/lt 是 dict {"$date":...}; 修复后: 真 datetime
        assert isinstance(gte, datetime), f"$date 未解码, 仍是 {type(gte)}"
        assert isinstance(lt, datetime), f"$date 未解码, 仍是 {type(lt)}"
        # bson.json_util.loads 解码 $date 为 naive datetime (tzinfo=None);
        # pymongo 4.16 把 naive 与 aware-UTC 编码为同一 BSON instant, 忠实保留 LLM 的 Z 标记.
        # 精确断言 (非仅 year/month/day) 钉死瞬时值, 防边界漂移.
        assert gte == datetime(2026, 6, 1)
        assert lt == datetime(2026, 7, 1)

    @pytest.mark.asyncio
    async def test_execute_query_preserves_pipeline_operators(self):
        """解码不能误伤管道操作符 key ($match/$group/$sum 原样保留)."""
        coll = FakeCollection([])
        driver = driver_with(coll)
        query = {"pipeline": [
            {"$match": {"category": "phone"}},
            {"$group": {"_id": "$category", "n": {"$sum": 1}}},
        ]}
        await driver.execute_query(make_ds(), "c", query, mode="single")
        captured = coll.aggregate_pipelines[-1]
        assert captured[0] == {"$match": {"category": "phone"}}
        assert captured[1] == {"$group": {"_id": "$category", "n": {"$sum": 1}}}

    @pytest.mark.asyncio
    async def test_count_mode_decodes_dollar_date(self):
        """count 路径同样共享入口解码 (trace 6de74455 第 3/4 次调用即 count)."""
        coll = FakeCollection([])
        driver = driver_with(coll)
        query = {"pipeline": [{"$match": {"createTime": {
            "$gte": {"$date": "2026-06-01T00:00:00.000Z"}}}}]}
        await driver.execute_query(make_ds(), "c", query, mode="count")
        # count 路径追加 {"$count":"count"} 后送 aggregate; 取首个 $match
        captured = coll.aggregate_pipelines[-1]
        gte = captured[0]["$match"]["createTime"]["$gte"]
        assert isinstance(gte, datetime)

    @pytest.mark.asyncio
    async def test_estimate_cost_decodes_dollar_date(self):
        # reuse shared helper instance method from TestEstimateCostShape
        helper = TestEstimateCostShape()
        driver, fake_db = helper._driver_with_command_capture()
        query = {"pipeline": [{"$match": {"createTime": {
            "$gte": {"$date": "2026-06-01T00:00:00.000Z"}}}}]}
        await driver.estimate_cost(make_ds(), "c", query)
        args, _ = fake_db.command.call_args
        pipe = args[1]["pipeline"]
        gte = pipe[0]["$match"]["createTime"]["$gte"]
        assert isinstance(gte, datetime), f"estimate_cost 未解码 $date: {type(gte)}"

    def test_decode_extended_json_handles_oid_and_numberlong(self):
        """解码非 $date marker 同样生效 — 固化 ADR-2 的更广契约 (LLM 也用 $oid 过滤 _id)."""
        decoded = _decode_extended_json({
            "_id": {"$oid": "6008236b737a8f0001fa5e63"},
            "n": {"$numberLong": "5"},
            "name": "ok",  # 普通字段原样
        })
        assert isinstance(decoded["_id"], ObjectId)
        assert str(decoded["_id"]) == "6008236b737a8f0001fa5e63"
        assert decoded["n"] == 5
        assert decoded["name"] == "ok"

    def test_decode_extended_json_passthrough_on_non_serializable(self):
        """payload 含原生不可序列化对象 (如已存在的 datetime) 时 fail-safe 回退原 payload."""
        from datetime import datetime as _dt
        native = {"t": _dt(2026, 6, 1)}
        out = _decode_extended_json(native)
        assert out is native, "fail-safe 应原样返回 (同一对象), 不阻查询"
