"""
业务词典抽取 — 从 Mongo canonical 的中文描述中抽取业务名词锚点

Map-Reduce 流程:
1. 分批 Map: canonicals 按 BATCH_SIZE 切片, 每批单独喂给 LLM
2. Consolidation Reduce: 跨批合并同义词 (batch1 抽 "条目"[明细] 与 batch2 抽 "明细" 要合并)
3. 幻觉过滤: LLM 可能生成不存在的 collection, 对照 seen_colls 校验

错误处理原则 (见 03-engineering.md §C):
- 禁止 silent default, 每个 except 至少 log.error + 降级标志
- 单批次失败不阻断其他批次
- 所有批次都失败 → raise TerminologyExtractionFailedAll
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from app.engine.json_parser import parse_llm_json
from app.engine.llm import chat_completion

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  公共常量
# ══════════════════════════════════════════════════════════════════════════════

BATCH_SIZE = 30           # 每批 canonicals 数, 约 6KB corpus
CONSOLIDATE_THRESHOLD = 80  # term 总数 ≤ 此值 → 一把归并; 否则分层归并
CONSOLIDATE_CHUNK = 60


# ══════════════════════════════════════════════════════════════════════════════
#  数据结构
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CanonicalLite:
    """extractor 输入 — 最小必要字段, 解耦 ORM."""
    canonical_id: int
    collection: str              # 纯 collection 名 (不含 database 前缀)
    database: str                # 数据库名 (来自 SchemaCanonicalObject.database where db_type='mongodb')
    identity_key: str            # "{database}.{collection}"
    description: str             # 一句话用途
    purpose_detail: str          # 详细业务语义

    def to_prompt_line(self) -> str:
        """组装 LLM 输入: [id=N] identity + 用途 + 详细.

        ID 前缀让 LLM 输出 source_canonical_ids 时直接引用整数 PK,
        不必自行复刻 collection 名 — 路由信息在程序端反查, LLM 零路由责任.
        """
        parts = [f"### [id={self.canonical_id}] {self.identity_key}"]
        if self.description:
            parts.append(f"用途: {self.description}")
        if self.purpose_detail:
            parts.append(f"详细: {self.purpose_detail}")
        return "\n".join(parts)


@dataclass
class ExtractedTerm:
    """抽取的单个业务名词.

    路由真相源 = source_canonical_ids (LLM 引用的 canonical PK 列表).
    extractor 在 consolidate 后程序化反查 canonical → 填 source_collections /
    primary_canonical_id / primary_collection / primary_database 衍生字段.
    db_type 由 refresher 经 DataSource 反查, 不在 extractor 处填.
    """
    term: str
    synonyms: list[str]
    source_canonical_ids: list[int] = field(default_factory=list)
    # ── 衍生字段, 由 extractor 反查 canonical 程序化填充, 非 LLM 输出 ──
    source_collections: list[str] = field(default_factory=list)
    primary_canonical_id: int | None = None
    primary_collection: str | None = None
    primary_database: str | None = None
    db_type: str | None = None


@dataclass
class FailedBatch:
    """失败批次元数据 — 供 trainer 回馈日志 + 后续独立重建."""
    idx: int
    canonical_ids: list[int]
    reason: str


@dataclass
class RefreshReport:
    """词典刷新完整报告 — 供 trainer / UI / Langfuse 消费."""
    canonicals_seen: int = 0
    merged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (term, reason)
    failed_batches: list[FailedBatch] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""

    @property
    def partial_failure(self) -> bool:
        return bool(self.failed or self.failed_batches)

    def to_dict(self) -> dict:
        return {
            "canonicals_seen": self.canonicals_seen,
            "merged_count": len(self.merged),
            "deleted_count": len(self.deleted),
            "failed_count": len(self.failed),
            "failed_batches_count": len(self.failed_batches),
            "sample_merged": self.merged[:5],
            "sample_deleted": self.deleted[:5],
            "sample_failed": self.failed[:5],
            "skipped": self.skipped,
            "reason": self.reason,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }


class TerminologyExtractionFailedAll(RuntimeError):
    """所有批次都失败时 raise, trainer 捕获不阻断主流水线."""

    def __init__(self, msg: str, failures: list[FailedBatch]):
        super().__init__(msg)
        self.failures = failures


# ══════════════════════════════════════════════════════════════════════════════
#  LLM Prompt
# ══════════════════════════════════════════════════════════════════════════════

_EXTRACT_SYSTEM = """你是业务语义抽取专家。输入是一批 MongoDB 集合的「[id=N] identity_key + 用途 + 详细」描述, 每条 canonical 已带唯一整数 ID.

你的任务:
1. 抽取其中的**中文业务名词** (实体类型 / 业务对象), 如 "条目" "订单" "商品" "品牌" "标签" 等
2. 为每个业务名词归纳同义词 (简称 / 英文别名 / 口语化表达)
3. 用 **source_canonical_ids** 字段记录该业务名词来自哪些 canonical 的整数 ID

输出严格 JSON (不要 markdown 围栏, 不要任何解释):
{
  "terms": [
    {
      "term": "条目",
      "synonyms": ["明细", "项", "item"],
      "source_canonical_ids": [12, 47]
    }
  ]
}

抽取原则:
- 只抽**业务领域名词**, 不抽 "状态" / "记录" / "信息" 等通用词
- 不同 canonical 描述中指代同一概念的要合并到同一 term, 把它们的 ID 都列入 source_canonical_ids
- source_canonical_ids 必须是输入中真实出现过的 [id=N] 整数, 不要编造
- 优先粗粒度: 抽 "订单" 即可, 不抽 "订单模板" / "订单快照" 等派生; 派生作为 synonyms 或独立 term
- 每个 term 的 synonyms 限 0-5 个, 不要堆叠
- 若本批次无法抽出任何业务名词 (如全英文无中文), 返回 {"terms": []}
"""


_CONSOLIDATE_SYSTEM = """你是业务词典归并专家。输入是多个批次抽取的原始 terms 列表, 其中可能存在:
- 同义词: 如 term="条目" 与 term="明细" 实际指同一业务概念
- 派生词: 如 term="订单模板" 与 term="订单" 实际应合并到粗粒度 "订单"
- 重复项: 完全相同的 term 出现多次

每个 term 自带 source_canonical_ids (整数 ID 列表), 标识它来自哪些 canonical 集合.

【保守合并原则 — 非常重要】
1. **只合并同名主表的同义词** (如 条目/item/明细 → 合并为 "条目"; source_canonical_ids 取并集)
2. **派生表、标签表、日志表、外键关联表, 各自独立成 term, 不合并到主词**
3. **不同业务范畴的 canonical 不合并**, 即使描述中含相同关键词
4. **source_canonical_ids 数量限制**: 合并后建议 ≤ 3 个 ID, 超过 3 个说明合并过度, 应拆分
   - 若某 term 确有 >3 个紧密相关的真实主表 ID (罕见), 仅保留语义最贴近的 3 个, 其余剔除
5. **不要新增未出现的 ID**, 不要编造

输出严格 JSON (不要 markdown 围栏, 不要任何解释):
{
  "terms": [
    {
      "term": "订单",
      "synonyms": ["单子", "order"],
      "source_canonical_ids": [42]
    },
    {
      "term": "订单模板",
      "synonyms": ["order template"],
      "source_canonical_ids": [55]
    }
  ]
}

归并原则汇总:
- 保留粗粒度主词, 同义词合并但派生独立
- source_canonical_ids 取并集去重, 严格控制在 3 个以内
- 每个 term 的 synonyms 限 0-6 个
- 每个 term 的 source_canonical_ids 限 1-3 个
"""


# ══════════════════════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════════════════════

def _chunk(items: list, n: int) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _truncate(s: str, n: int = 300) -> str:
    return s if len(s) <= n else s[:n] + "…"


def _parse_terms_json(raw: str) -> list[ExtractedTerm]:
    """解析 LLM 输出为 ExtractedTerm 列表. 失败抛 ValueError 由上层处理.

    LLM 现在只返 term + synonyms + source_canonical_ids, 路由信息 (collection/
    database/db_type) 由 extractor 后续程序化反查 canonical 填充.
    """
    data = parse_llm_json(raw, expect="dict")
    if data is None:
        raise ValueError(f"LLM JSON 解析失败: {raw[:200]!r}")
    terms_raw = data.get("terms") or []
    out: list[ExtractedTerm] = []
    for t in terms_raw:
        term = (t.get("term") or "").strip()
        if not term:
            continue
        syns = [s.strip() for s in (t.get("synonyms") or []) if s and s.strip()]
        ids_raw = t.get("source_canonical_ids") or []
        ids: list[int] = []
        for v in ids_raw:
            try:
                ids.append(int(v))
            except (TypeError, ValueError):
                log.warning("[extract_terms] term=%r 跳过非整数 canonical_id=%r", term, v)
        out.append(ExtractedTerm(
            term=term, synonyms=syns, source_canonical_ids=ids,
        ))
    return out


def _term_to_json(t: ExtractedTerm) -> dict:
    return {
        "term": t.term,
        "synonyms": t.synonyms,
        "source_canonical_ids": t.source_canonical_ids,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  单批抽取 (Map)
# ══════════════════════════════════════════════════════════════════════════════

def _extract_one_batch(
    batch: list[CanonicalLite], batch_idx: int, namespace_id: int | None = None,
) -> list[ExtractedTerm]:
    """单批 LLM 调用. 任何异常由调用方捕获 → 记失败批次."""
    corpus = "\n\n".join(c.to_prompt_line() for c in batch)
    log.info(
        "[extract_terms] batch=%d size=%d corpus=%d chars",
        batch_idx, len(batch), len(corpus),
    )
    raw = chat_completion(
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": corpus},
        ],
        max_tokens=4096,  # noqa: hardcode
        namespace_id=namespace_id,
    )
    if not raw or not raw.strip():
        raise LLMEmptyResponseError(f"batch={batch_idx} LLM 返回空")
    return _parse_terms_json(raw)


class LLMEmptyResponseError(RuntimeError):
    """LLM 返回空文本 — 抖动或截断, 记失败批次."""


# ══════════════════════════════════════════════════════════════════════════════
#  归并 (Reduce)
# ══════════════════════════════════════════════════════════════════════════════

def _consolidate_one_pass(
    raw_terms: list[ExtractedTerm], namespace_id: int | None = None,
) -> list[ExtractedTerm]:
    """单次 LLM 归并. 若失败返回原 terms 的程序化去重版 (兜底不丢数据)."""
    if not raw_terms:
        return []
    payload = {"terms": [_term_to_json(t) for t in raw_terms]}
    try:
        raw = chat_completion(
            messages=[
                {"role": "system", "content": _CONSOLIDATE_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            max_tokens=4096,  # noqa: hardcode
            namespace_id=namespace_id,
        )
        if not raw or not raw.strip():
            raise LLMEmptyResponseError("consolidation LLM 返回空")
        return _parse_terms_json(raw)
    except Exception as e:
        log.error(
            "[extract_terms] consolidation 失败, 回退程序化去重 (by term 字段): %s",
            e, exc_info=True,
        )
        return _dedup_by_term(raw_terms)


def _dedup_by_term(terms: list[ExtractedTerm]) -> list[ExtractedTerm]:
    """兜底程序化去重: 同 term 合并 synonyms / source_canonical_ids."""
    merged: dict[str, ExtractedTerm] = {}
    for t in terms:
        if t.term not in merged:
            merged[t.term] = ExtractedTerm(
                term=t.term,
                synonyms=list(t.synonyms),
                source_canonical_ids=list(t.source_canonical_ids),
            )
        else:
            e = merged[t.term]
            e.synonyms = sorted(set(e.synonyms) | set(t.synonyms))
            e.source_canonical_ids = sorted(
                set(e.source_canonical_ids) | set(t.source_canonical_ids)
            )
    return list(merged.values())


def _consolidate_terms(
    raw_terms: list[ExtractedTerm], namespace_id: int | None = None,
) -> list[ExtractedTerm]:
    """跨批次归并: 总数 ≤ 80 一次过; 否则分层归并."""
    if len(raw_terms) <= CONSOLIDATE_THRESHOLD:
        return _consolidate_one_pass(raw_terms, namespace_id=namespace_id)
    partial: list[ExtractedTerm] = []
    for chunk in _chunk(raw_terms, CONSOLIDATE_CHUNK):
        partial.extend(_consolidate_one_pass(chunk, namespace_id=namespace_id))
    # 分层归并后可能还需一次总归并
    if len(partial) > CONSOLIDATE_CHUNK:
        return _consolidate_one_pass(partial, namespace_id=namespace_id)
    return partial


# ══════════════════════════════════════════════════════════════════════════════
#  幻觉过滤 + 路由反查 (程序化 — Mongo Canonical 是真相源)
# ══════════════════════════════════════════════════════════════════════════════

def _filter_and_resolve_routing(
    terms: list[ExtractedTerm], canonicals_by_id: dict[int, CanonicalLite],
) -> list[ExtractedTerm]:
    """ID 级幻觉过滤 + 程序化反查路由.

    LLM 只输出 source_canonical_ids; collection/database/primary_canonical_id
    全部从 canonicals_by_id 反查. db_type 留给 refresher 经 DataSource 推断.

    被丢弃的 term:
      - source_canonical_ids 全部不存在 (LLM 编造 ID)
      - 反查后 primary_collection 仍为空 (理论上不可能, 防御)
    """
    kept: list[ExtractedTerm] = []
    for t in terms:
        valid_ids = [i for i in t.source_canonical_ids if i in canonicals_by_id]
        invalid_ids = set(t.source_canonical_ids) - set(valid_ids)
        if invalid_ids:
            log.warning(
                "[extract_terms] term=%r 过滤幻觉 canonical_ids=%s",
                t.term, sorted(invalid_ids),
            )
        if not valid_ids:
            log.warning("[extract_terms] term=%r 无真实 source_canonical_ids, 丢弃", t.term)
            continue

        # 取首 ID 作为 primary canonical (consolidate prompt 已要求按相关性排序)
        primary_id = valid_ids[0]
        primary = canonicals_by_id[primary_id]
        # 反查全部 source canonicals 的 collection 名 (去重保序)
        seen: set[str] = set()
        source_collections: list[str] = []
        for cid in valid_ids:
            c = canonicals_by_id[cid]
            if c.collection not in seen:
                seen.add(c.collection)
                source_collections.append(c.collection)

        t.source_canonical_ids = valid_ids
        t.primary_canonical_id = primary_id
        t.primary_collection = primary.collection
        t.primary_database = primary.database
        t.source_collections = source_collections
        # db_type 不在 extractor 处填 — 由 refresher 经 DataSource 反查 (避免 extractor 依赖 ORM)
        kept.append(t)
    return kept


# ══════════════════════════════════════════════════════════════════════════════
#  公开入口
# ══════════════════════════════════════════════════════════════════════════════

def extract_terms_sync(
    canonicals: list[CanonicalLite], namespace_id: int | None = None,
) -> tuple[list[ExtractedTerm], list[FailedBatch]]:
    """同步版抽词 (供 asyncio.to_thread 包装). 返回 (consolidated_terms, failed_batches).

    失败策略:
    - 单批失败: 记 failed_batches 继续其他批
    - 全失败: raise TerminologyExtractionFailedAll
    - consolidation 失败: 兜底程序化去重 (不 raise)
    """
    if not canonicals:
        return [], []

    failed: list[FailedBatch] = []
    all_terms: list[ExtractedTerm] = []

    for idx, batch in enumerate(_chunk(canonicals, BATCH_SIZE)):
        try:
            terms = _extract_one_batch(batch, batch_idx=idx, namespace_id=namespace_id)
            all_terms.extend(terms)
            log.info(
                "[extract_terms] batch=%d 成功 canonicals=%d terms=%d",
                idx, len(batch), len(terms),
            )
        except LLMEmptyResponseError as e:
            log.error(
                "[extract_terms] batch=%d LLM 空返回 canonical_ids=%s: %s",
                idx, [c.canonical_id for c in batch], e,
            )
            failed.append(FailedBatch(
                idx=idx, canonical_ids=[c.canonical_id for c in batch],
                reason="llm_empty",
            ))
        except (json.JSONDecodeError, ValueError) as e:
            log.error(
                "[extract_terms] batch=%d JSON 解析失败: %s", idx, e, exc_info=True,
            )
            failed.append(FailedBatch(
                idx=idx, canonical_ids=[c.canonical_id for c in batch],
                reason=f"bad_json: {_truncate(str(e), 100)}",
            ))
        except Exception as e:
            log.error(
                "[extract_terms] batch=%d 未预期异常: %s", idx, e, exc_info=True,
            )
            failed.append(FailedBatch(
                idx=idx, canonical_ids=[c.canonical_id for c in batch],
                reason=f"{type(e).__name__}: {_truncate(str(e), 100)}",
            ))

    if not all_terms:
        raise TerminologyExtractionFailedAll(
            f"所有 {len(failed)} 批抽词都失败", failures=failed,
        )

    # Reduce
    consolidated = _consolidate_terms(all_terms, namespace_id=namespace_id)

    # ID 级幻觉过滤 + 程序化反查路由 (collection / database 来自 canonical 真相源)
    canonicals_by_id = {c.canonical_id: c for c in canonicals}
    filtered = _filter_and_resolve_routing(consolidated, canonicals_by_id)

    log.info(
        "[extract_terms] 完成: raw=%d consolidated=%d filtered=%d failed_batches=%d",
        len(all_terms), len(consolidated), len(filtered), len(failed),
    )
    return filtered, failed


async def extract_terms(
    canonicals: list[CanonicalLite], namespace_id: int | None = None,
) -> tuple[list[ExtractedTerm], list[FailedBatch]]:
    """异步入口 — 线程池包裹同步 LLM 调用, 避免阻塞事件循环."""
    return await asyncio.to_thread(extract_terms_sync, canonicals, namespace_id)
