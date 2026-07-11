"""MongoDB 异步驱动 — motor AsyncIOMotorClient + aggregation 执行."""
from __future__ import annotations

import json as _json
import logging
import time
from typing import Any

from bson import DBRef, ObjectId, json_util
from bson.decimal128 import Decimal128
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.engine.drivers._exceptions import (
    ConnectionFailureError,
    PayloadShapeMismatchError,
)
from app.engine.drivers.base import (
    CostEstimate,
    ExecuteResult,
    FieldDef,
    ProbeResult,
    SchemaSnapshot,
)
from app.models import DataSource
from app.models.base import local_now

log = logging.getLogger(__name__)


def _infer_bson_type(value: object) -> str:
    """从 sample 值推断 BSON 类型名."""
    if value is None:
        return "null"
    type_map = {
        "str": "string",
        "int": "int",
        "float": "double",
        "bool": "bool",
        "list": "array",
        "dict": "object",
    }
    name = type(value).__name__
    return type_map.get(name, name)


_BSON_MAX_DEPTH = 64  # noqa: hardcode — 深度护栏, 防病态嵌套递归爆栈; 真实文档远小于此


def _normalize_doc(doc: dict) -> dict:
    """行级规整: doc 顶层恒为 dict, 包一层确保类型为 dict (供 list[dict] append)."""
    result = _normalize_bson(doc)
    return result if isinstance(result, dict) else {"_value": result}


def _normalize_bson(value: object, _depth: int = 0) -> object:
    """递归把驱动返回的 BSON 特殊类型转成 LLM 友好结构.

    解决 DBRef 序列化退化为不可解析字符串 (如 "DBRef('c_x', ObjectId('...'))") 的问题:
    LLM 抠不出内嵌 id, 被迫反复试错。统一在执行后、回传前规整一次。

    - DBRef     → {"$ref": <collection>, "$id_str": <id 字符串>, "$id_type": <id 类型名>}
                  ($id_str 是裸字符串, LLM 直接拿去匹配目标集合的关联字段)
    - ObjectId  → str()  (不止顶层 _id, 任意嵌套位置)
    - Decimal128 → str()
    - bytes     → repr() (二进制不直喂 LLM)
    - dict/list → 递归
    - 其它原生 JSON 类型原样

    性能: 实测 ~0.03ms/行 (含 8 元素 DBRef 数组的重嵌套文档), 1000 行上限 ~31ms,
    远小于单次 Mongo 查询 (30-380ms) 与 LLM 轮次 (6-13s)。两处廉价护栏:
    (1) 基础标量 fast-path 前置, 跳过全部 isinstance 特判;
    (2) _depth 上限防病态嵌套爆栈 (超限降级 str(), 不抛异常阻断主路径)。
    """
    # fast-path: 绝大多数值是基础标量, 直接返回, 避开后续所有 isinstance 检查
    if value is None or type(value) in (str, int, float, bool):
        return value
    if _depth >= _BSON_MAX_DEPTH:
        return str(value)
    if isinstance(value, DBRef):
        return {
            "$ref": value.collection,
            "$id_str": str(value.id),
            "$id_type": type(value.id).__name__,
        }
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Decimal128):
        return str(value)
    if isinstance(value, bytes):
        return repr(value)
    if isinstance(value, dict):
        return {k: _normalize_bson(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_bson(v, _depth + 1) for v in value]
    return value


def _decode_extended_json(payload: dict) -> dict:
    """把 LLM 输出的 Extended JSON ($date/$oid/$numberLong/...) 解码成 BSON 原生类型.

    根因 (trace 6de74455): LLM 在 pipeline/filter 里用 {"$date":"2026-...Z"} 表达
    日期, parse_llm_json 走纯 json.loads 不认这层包装, 原样作为 Python dict 透传到
    motor. pymongo 只在 bson.json_util.loads 时才把 {"$date":...} 转成 datetime,
    对已解析的 dict 会按 BSON 编码规则编码成子文档 (Object, type 3) 而非 Date (type 9).
    $match 跨类型比较 (BSON 类型序 Object < Date) 使 $gte/$lt 恒假 → 0 行.

    仅在 mongo 驱动边界解码, 不上提全局 parser: 否则 datetime 渗入 knowledge /
    trace_json 等结构, 下游 json.dumps 会炸 (datetime 不可序列化). 见 design ADR-1.

    json_util 只转换类型 marker key ($date/$oid/...), 不碰管道操作符 key
    ($match/$group/$gte/$sum), 后者原样透传 — 故无需手写 walker 枚举类型. 见 ADR-2.

    失败安全: 解码异常原样返回 payload (不阻断查询, 仅 log.warning).
    """
    try:
        return json_util.loads(_json.dumps(payload))
    except Exception as exc:  # noqa: BLE001 — 解码失败不阻业务, 回退原 payload
        log.warning("[mongo_driver] extended-json decode failed, passthrough: %s", exc)
        return payload


def _classify_query_shape(query: dict) -> tuple[str, Any]:
    """聚合 vs 过滤的形态判定唯一来源 (scope C).

    query 携带 pipeline → ("aggregate", pipeline); 否则 → ("filter", filter_dict)。
    count / 数据读取 (single/probe/batched) / estimate_cost 三处共用, 杜绝形态判定
    在不同模式间漂移 (历史 bug: count 丢 pipeline, estimate_cost 用 truthiness)。

    用 `is not None` (而非 truthiness): 显式空 pipeline [] 归为 aggregate —— 与历史
    数据读取路径行为一致, 并纠正 estimate_cost 的 truthiness 误分类。
    """
    pipeline = query.get("pipeline")
    if pipeline is not None:
        return "aggregate", pipeline
    return "filter", query.get("filter", {})


_TAIL_ROW_STAGES = ("$limit", "$skip", "$sample")


def _strip_tail_row_stages(pipeline: list[dict]) -> list[dict]:
    """剥离 pipeline 尾部连续的行截断 stage ($limit/$skip/$sample).

    plan_executor 末步补 count 共用: planner 末步带 $limit 保护中间步, 计数前
    必须先剥离它再追加 $count, 否则结果被 planner LIMIT 封顶.
    仅剥尾部连续行 stage, 不动 pipeline 中间的 stage.
    """
    out = list(pipeline)
    while out and isinstance(out[-1], dict) and len(out[-1]) == 1 \
            and next(iter(out[-1]), None) in _TAIL_ROW_STAGES:
        out.pop()
    return out


class MongoDriver:
    """motor AsyncIOMotorClient 驱动, 实现 DataSourceDriver 协议."""

    db_type: str = "mongodb"
    paradigm: str = "document"

    def __init__(self) -> None:
        self._clients: dict[int, AsyncIOMotorClient] = {}

    def _get_client(self, ds: DataSource) -> AsyncIOMotorClient:
        """获取或创建 ds 对应的 motor client."""
        if ds.id is None:
            raise ValueError(
                "未落库的 DataSource (ds.id is None) 不可进 client 缓存; "
                "建源画像请用 fetch_db_profile 的一次性临时 client"
            )
        if ds.id in self._clients:
            return self._clients[ds.id]
        try:
            uri = f"mongodb://{ds.username}:{ds.password}@{ds.host}:{ds.port}/{ds.database}"
            client = AsyncIOMotorClient(
                uri,
                maxPoolSize=settings.mongo_pool_max_size,
                authSource=ds.database,
            )
        except Exception as exc:
            raise ConnectionFailureError(
                f"MongoDB 连接失败: {ds.host}:{ds.port}/{ds.database} — {exc}",
                suggestion="检查 host/port/credentials 是否正确",
            ) from exc
        self._clients[ds.id] = client
        return client

    # ── fetch_schema ─────────────────────────────────────

    async def fetch_schema(
        self,
        ds: DataSource,
        target: str | None = None,
    ) -> SchemaSnapshot | list[SchemaSnapshot]:
        log.info("[mongo_driver] fetch_schema ds=%d target=%s", ds.id, target)
        client = self._get_client(ds)
        db = client[ds.database]

        if target is None:
            # 列出所有集合
            names = await db.list_collection_names()
            results: list[SchemaSnapshot] = []
            for name in names:
                results.append(
                    SchemaSnapshot(
                        db_type="mongodb",
                        database=ds.database,
                        target=name,
                        description="",
                        fields=[],
                        indexes=[],
                        sample_count=0,
                    )
                )
            return results

        # 单集合详情
        coll = db[target]

        # 采样推断字段
        sample = await coll.find_one()
        fields: list[FieldDef] = []
        if sample:
            for key, value in sample.items():
                if key == "_id":
                    continue
                fields.append(
                    FieldDef(
                        name=key,
                        type=_infer_bson_type(value),
                        description="",
                        indexed=False,
                        nullable=True,
                    )
                )

        # 索引信息
        indexes: list[dict] = []
        async for idx_info in coll.list_indexes():
            indexes.append({
                "name": idx_info.get("name", ""),
                "keys": idx_info.get("key", {}),
                "unique": idx_info.get("unique", False),
            })

        # 标记 indexed 字段
        indexed_fields: set[str] = set()
        for idx in indexes:
            for k in idx.get("keys", {}):
                indexed_fields.add(k)
        for f in fields:
            if f["name"] in indexed_fields:
                f["indexed"] = True

        # 文档数估算
        sample_count = await coll.estimated_document_count()

        return SchemaSnapshot(
            db_type="mongodb",
            database=ds.database,
            target=target,
            description="",
            fields=fields,
            indexes=indexes,
            sample_count=sample_count,
        )

    # ── inspect_values ───────────────────────────────────

    async def inspect_values(
        self,
        ds: DataSource,
        target: str,
        field: str,
        limit: int = 10,
    ) -> list[dict]:
        log.info("[mongo_driver] inspect_values ds=%d target=%s field=%s", ds.id, target, field)
        client = self._get_client(ds)
        db = client[ds.database]
        coll = db[target]

        pipeline = [
            {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": limit},
        ]
        results: list[dict] = []
        async for doc in coll.aggregate(pipeline):
            results.append({"value": _normalize_bson(doc["_id"]), "count": doc["count"]})
        return results

    # ── estimate_cost ────────────────────────────────────

    async def estimate_cost(
        self,
        ds: DataSource,
        target: str,
        query: dict,
    ) -> CostEstimate:
        log.info("[mongo_driver] estimate_cost ds=%d target=%s", ds.id, target)
        # Extended JSON ($date/...) 解码 — 与 execute_query 同源, 防 explain 吃错配值
        query = _decode_extended_json(query)
        client = self._get_client(ds)
        db = client[ds.database]
        coll = db[target]

        shape, payload = _classify_query_shape(query)
        if shape == "aggregate":
            explain_result = await db.command(
                "explain",
                {"aggregate": target, "pipeline": payload, "cursor": {}},
                verbosity="queryPlanner",
            )
        else:
            explain_result = await db.command(
                "explain",
                {"find": target, "filter": payload},
                verbosity="queryPlanner",
            )

        # 估算行数 — 使用 estimated_document_count 作为上界
        estimated_rows = await coll.estimated_document_count()
        if estimated_rows > settings.query_cost_total_limit:
            level = "blocked"
        elif estimated_rows > settings.query_cost_single_layer_limit:
            level = "high"
        else:
            level = "ok"

        return CostEstimate(
            estimated_rows=estimated_rows,
            warning_level=level,
            raw_explain=explain_result,
        )

    # ── 2-number cap 辅助 ────────────────────────────────
    @staticmethod
    def _is_count_pipeline(pipeline: list) -> bool:
        """pipeline 末尾是 {$count: ...} → 计数查询 (LLM 自写, 驱动不再追加 $count)."""
        return bool(pipeline) and isinstance(pipeline[-1], dict) and "$count" in pipeline[-1]

    def _apply_mongo_limit(self, pipeline: list) -> tuple[list, int]:
        """$limit 三态 (对齐 SQL _apply_cap): 无→追加 default; >ceiling→替换; ≤→保留.

        不剥 $skip/$sample (LLM 意图). 末尾 $limit 检测, 不动中间 $limit.
        """
        p = list(pipeline)
        limit_idx = None
        for i in range(len(p) - 1, -1, -1):
            if isinstance(p[i], dict) and "$limit" in p[i]:
                limit_idx = i
                break
        if limit_idx is None:
            p.append({"$limit": settings.default_limit})
            return p, settings.default_limit
        val = p[limit_idx]["$limit"]
        if val > settings.hard_ceiling:
            p[limit_idx] = {"$limit": settings.hard_ceiling}
            return p, settings.hard_ceiling
        return p, val

    # ── execute_query ────────────────────────────────────

    async def execute_query(
        self,
        ds: DataSource,
        target: str,
        query: dict,
    ) -> ExecuteResult:
        log.info("[mongo_driver] execute_query ds=%d target=%s", ds.id, target)

        # payload 校验
        if "sql" in query:
            raise PayloadShapeMismatchError(
                "MongoDB driver 不接受 'sql' key",
                suggestion="使用 'pipeline' 或 'filter' key",
            )
        if "pipeline" not in query and "filter" not in query:
            raise PayloadShapeMismatchError(
                "execute_query 需要 'pipeline' 或 'filter' key",
                suggestion="payload 必须包含 'pipeline' (聚合) 或 'filter' (查询)",
            )

        # Extended JSON ($date/...) 解码为 BSON 原生类型 — trace 6de74455 根因修复
        query = _decode_extended_json(query)

        client = self._get_client(ds)
        db = client[ds.database]
        coll = db[target]

        t0 = time.perf_counter()

        # count 模式已删 — LLM 自写 $count (aggregate) 或系统补 count 自拼.
        shape, payload = _classify_query_shape(query)
        if shape == "aggregate":
            pipeline = list(payload)
            if self._is_count_pipeline(pipeline):
                # 计数: 执行原样 (返 1 行), 不加 cap, 不截断
                rows: list[dict] = []
                async for doc in coll.aggregate(pipeline):
                    rows.append(_normalize_doc(doc))
                applied = 1
                truncated = False
            else:
                pipeline, applied = self._apply_mongo_limit(pipeline)
                rows = []
                async for doc in coll.aggregate(pipeline):
                    rows.append(_normalize_doc(doc))
                truncated = len(rows) >= applied
        else:
            # find 模式: filter 无 $limit 概念, 用 default_limit cap
            applied = settings.default_limit
            cursor = coll.find(payload).limit(applied)
            rows = []
            async for doc in cursor:
                rows.append(_normalize_doc(doc))
            truncated = len(rows) >= applied

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log.info(
            "[mongo_driver] execute_query done ds=%d rows=%d elapsed_ms=%d",
            ds.id,
            len(rows),
            elapsed_ms,
        )
        return ExecuteResult(
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
        )

    # ── health_check ─────────────────────────────────────

    async def health_check(self, ds: DataSource) -> bool:
        try:
            client = self._get_client(ds)
            await client.admin.command("ping")
            return True
        except Exception:
            return False

    # ── list_object_names ────────────────────────────────

    async def list_object_names(self, ds: DataSource) -> list[str]:
        """list_collection_names → 返回库内全部集合名 (sorted)."""
        client = self._get_client(ds)
        return sorted(await client[ds.database].list_collection_names())

    # ── fetch_foreign_keys ─────────────────────────────────

    async def fetch_foreign_keys(
        self, ds: DataSource, target: str | None = None,
    ) -> list[dict]:
        """MongoDB 无外键概念,返 []. 零连库."""
        return []

    # ── fetch_db_profile ─────────────────────────────────

    async def fetch_db_profile(self, ds: DataSource) -> dict:
        """连库合成库级画像. 一次性临时 client, 不进 _clients. 降级安全.

        connected: ping 成功即 True (server 可达 + auth 通过), 与 buildInfo/version
        抽取解耦. 某些受限/DocumentDB 环境 buildInfo 需权限而 ping 不需 —— 用 ping
        判连通避免误拒 (D4 降级语义).
        """
        profile: dict = {"profiled_at": local_now().isoformat(), "connected": False}
        client = None
        try:
            # 用关键字参数传 host/port/认证, 不拼 URI f-string —— 密码含 @/:/ 时 URI 会断裂.
            # (现有 _get_client 的 f-string URI 有此缺陷, 此处不复制, 用关键字参数根治.)
            client = AsyncIOMotorClient(
                host=ds.host, port=ds.port,
                username=ds.username, password=ds.password,
                authSource=ds.database,
                serverSelectionTimeoutMS=settings.mongo_connect_timeout_ms,
            )
            db = client[ds.database]
            # ping 确认连通 (无需特殊权限, 与 buildInfo/version 抽取解耦)
            await db.command("ping")
            profile["connected"] = True
            # 版本 + flavor (复用 mongo_flavor.build_capabilities)
            try:
                info = await client.admin.command("buildInfo")
                version = info.get("version", "")
                if version:
                    profile["version"] = version
                    from app.engine.drivers.mongo_flavor import build_capabilities
                    caps = build_capabilities(dict(info), version)
                    profile["flavor"] = caps["flavor"]
                    profile["unsupported_ops"] = caps.get("unsupported_ops", [])
                    profile["unsupported_stage_variants"] = caps.get(
                        "unsupported_stage_variants", []
                    )
                    profile["syntax_constraints"] = caps.get("syntax_constraints", [])
                    profile["equivalent_hints"] = caps.get("equivalent_hints", [])
            except Exception:  # noqa: BLE001 — 降级
                pass
            # 对象数量 (collection 数, 只要数字不要清单)
            try:
                names = await db.list_collection_names()
                profile["object_count"] = len(names)
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001 — 连不上也返回 (只含 profiled_at)
            log.warning("[mongo_driver] fetch_db_profile failed ds_host=%s: %s", ds.host, exc)
        finally:
            if client is not None:
                client.close()
        return profile

    # ── probe_connectivity ───────────────────────────────

    async def probe_connectivity(self, ds: DataSource) -> ProbeResult:
        """一次性临时连接: 探测连通. MongoDB 不暴露时区设置, detected_timezone 始终 None."""
        client = None
        try:
            client = AsyncIOMotorClient(
                host=ds.host, port=ds.port,
                username=ds.username, password=ds.password,
                authSource=ds.database,
                serverSelectionTimeoutMS=settings.mongo_connect_timeout_ms,
            )
            await client[ds.database].command("ping")
            return ProbeResult(connected=True, detected_timezone=None)
        except Exception as exc:
            return ProbeResult(connected=False, failure_reason=str(exc)[:300])
        finally:
            if client:
                client.close()

    # ── lifecycle ────────────────────────────────────────

    async def invalidate_client(self, ds_id: int) -> None:
        """关闭并移除指定 ds 的 motor client (DataSource 删除/变更时调用)."""
        client = self._clients.pop(ds_id, None)
        if client is not None:
            client.close()

    async def close_all(self) -> None:
        """关闭所有 motor client."""
        for client in self._clients.values():
            client.close()
        self._clients.clear()
