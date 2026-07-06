"""
知识录入治理 — 摘要归纳 + 长度裁决 + 冲突检测 (合并入口)

对外 API (按 P2 任务推进逐步扩充):
- refine_knowledge:      将用户原始输入精炼为 (refined, description, overflow)
- propose_split:         (Task 2.2) tier=critical 超长时的拆分候选
- detect_conflicts:      (Task 2.3) 与既有知识比对, 返回 ConflictReport
"""
import logging
from dataclasses import dataclass, field

from langfuse import observe

from app.config import settings
from app.engine.llm import chat_completion

log = logging.getLogger(__name__)


# ─────────────────────────── 自定义异常 ───────────────────────────

class IntakeLLMError(RuntimeError):
    """intake 链路 LLM 调用失败 (split / conflict 检测).

    P1-22 D9 决策:
    - propose_split / detect_conflicts 失败必须 raise 让 api 层感知并返 503.
      静默 fallback 空结果 → split 失败与"不可分"语义混淆 / conflict 漏检录脏知识.
    - refine_knowledge 失败保留 fallback (raw 原文本身有效, 仅丢失 LLM 整理).
    """

# ─────────────────────────── 常量 ───────────────────────────
CRITICAL_MAX_CHARS = 200       # tier=critical 单条硬上限 (模块强制)  # noqa: hardcode
NORMAL_MAX_CHARS = settings.knowledge_normal_max_chars  # tier=normal 原文上限 (Stage 2 Task 8 搬 settings)
_CONFLICT_EXISTING_LIMIT = 20  # detect_conflicts 单次比对的既有条目硬上限 (防 prompt 爆炸)  # noqa: hardcode
CONFLICT_CANDIDATE_LIMIT = _CONFLICT_EXISTING_LIMIT  # 对外公开同值, 供调用方 .limit(N) 复用
_CONFLICT_REASON_MAX = 100     # 单条冲突 reason 截断上限  # noqa: hardcode
_VALID_SUGGESTED = {"merge", "replace", "coexist"}

# ─────────────────────────── entry_type 5 类宪章 ───────────────────────────
# Stage 1 写入治理: 与 KnowledgeEntry.entry_type 文档串、payload schema、前端下拉一一对应
# 任何写入点 (manual / git / agent) 均必须从此白名单取值
VALID_ENTRY_TYPES: frozenset[str] = frozenset({
    "terminology",      # 业务术语 → 集合的映射, 替代旧 business_terms
    "instance_alias",   # 别名 → 具体记录的映射 (仅 RAG 召回, 不进自动机)
    "example",          # Q-MQL 历史成功对
    "rule",             # 查询规则 / 业务约束, 替代旧 namespace_rules
    "route_hint",       # agent loop 学到的多层关联策略
})
# 兼容 Task 11 plan 别名 (内部模块仍可读)
_VALID_ENTRY_TYPES = VALID_ENTRY_TYPES

# ─────────────────────────── Prompt ───────────────────────────
_REFINE_PROMPT = """你是业务知识库编辑。把用户的知识录入改写为一条清晰、无歧义、可机读的{scope}条目。

要求:
1. 去冗余、去指代 (如"那种""上面说的")
2. 保留字段名、表名、数值等专有信息
3. 单句或极短段落, 不超过 {limit} 个汉字
4. 生成一句 ≤20 字的说明, 供索引/审核使用

严格 JSON 输出:
{{"refined":"精炼后的条目内容","description":"一句话说明"}}"""

_SPLIT_PROMPT = """用户想录入的强约束条目太长, 把它拆分成多个独立、原子化的约束。

要求:
1. 每条 ≤200 字且语义自洽
2. 保留字段名、表名、数值等专有信息
3. 为每条生成 ≤20 字的说明

严格 JSON 输出:
{"candidates":[{"refined":"...","description":"..."},...]}"""

_CONFLICT_PROMPT = """判断新知识是否与既有知识库存在语义冲突或重复。

对每条存在冲突的既有条目, 输出:
- existing_id: 既有条目 id
- reason: ≤40 字说明冲突点
- suggested: merge(合并为一条) | replace(用新条目覆盖旧) | coexist(语义不同, 允许共存)

严格 JSON:
{"conflicts":[{"existing_id":1,"reason":"...","suggested":"merge"}]}
无冲突: {"conflicts":[]}"""


# ─────────────────────────── 数据结构 ───────────────────────────
@dataclass
class RefineResult:
    refined: str
    description: str
    overflow: bool   # critical 场景超过 CRITICAL_MAX_CHARS 时为 True


@dataclass
class ConflictItem:
    existing_id: int
    reason: str
    suggested: str  # "merge" | "replace" | "coexist"


@dataclass
class ConflictReport:
    items: list[ConflictItem] = field(default_factory=list)


# ─────────────────────────── 内部工具 ───────────────────────────
def _scope_label(entry_type: str, tier: str) -> str:
    """LLM prompt 中的"业务条目类型"标签 — 与 5 类 entry_type + critical tier 对齐."""
    # critical tier 总是"强约束业务规则", 与 entry_type 无关
    if tier == "critical":
        return "强约束业务规则"
    return {
        "terminology":    "业务术语",
        "example":        "查询示例 (Q-MQL pair)",
        "rule":           "查询规则 / 业务约束",
        "route_hint":     "决策路径偏好",
    }.get(entry_type, "业务知识")


def _overflow_for(text: str, tier: str) -> bool:
    """critical tier 超长判定 — fallback 路径也要走这个, 防止长 raw 绕过 split."""
    return tier == "critical" and len(text) > CRITICAL_MAX_CHARS


# ─────────────────────────── 对外 API ───────────────────────────
@observe(name="knowledge_refine", as_type="chain")
def refine_knowledge(entry_type: str, raw: str, tier: str) -> RefineResult:
    """
    精炼用户输入。LLM 失败时降级为返回原始内容 (description="", overflow=False)。

    Stage 1 写入治理: entry_type 强制走 5 类宪章, 拼错 / 历史遗留值在入口拦截,
    避免向下游 ChromaDB / payload schema 传播污染.
    """
    if entry_type not in VALID_ENTRY_TYPES:
        raise ValueError(
            f"unknown entry_type: {entry_type!r}, expect one of {sorted(VALID_ENTRY_TYPES)}"
        )
    limit = CRITICAL_MAX_CHARS if tier == "critical" else NORMAL_MAX_CHARS
    prompt = _REFINE_PROMPT.format(scope=_scope_label(entry_type, tier), limit=limit)

    try:
        text = chat_completion(
            [{"role": "system", "content": prompt},
             {"role": "user", "content": raw}],
            temperature=0.1,
            max_tokens=1024,  # noqa: hardcode
        )
    except Exception as e:
        # refine 失败保持 fallback — raw 原文本身有效 (P1-22 D9 有意识决策)
        log.error("[intake] refine LLM 失败, 降级使用原始输入: %s", e)
        return RefineResult(refined=raw, description="", overflow=_overflow_for(raw, tier))

    from app.engine.json_parser import parse_llm_json
    data = parse_llm_json(text, expect="dict")
    if data is None:
        log.warning("[intake] refine 返回无 JSON: %s", text[:200])
        return RefineResult(refined=raw, description="", overflow=_overflow_for(raw, tier))

    refined = (data.get("refined") or raw).strip()
    description = (data.get("description") or "").strip()
    return RefineResult(refined=refined, description=description, overflow=_overflow_for(refined, tier))


@observe(name="knowledge_split", as_type="chain")
def propose_split(raw: str) -> list[RefineResult]:
    """
    critical tier 超长时让 LLM 拆成多条原子候选。
    LLM 故障 / 无 JSON / 解析失败均返回空列表。
    """
    try:
        text = chat_completion(
            [{"role": "system", "content": _SPLIT_PROMPT},
             {"role": "user", "content": raw}],
            temperature=0.2,  # 拆分比精炼需要更高创造性, 故略高于 refine 的 0.1
            max_tokens=2048,  # noqa: hardcode
        )
    except Exception as e:
        log.error("[intake] split LLM 失败: %s", e)
        raise IntakeLLMError(
            f"split LLM 不可用, 知识录入暂时无法处理 — 请稍后重试: {e}"
        ) from e

    from app.engine.json_parser import parse_llm_json
    data = parse_llm_json(text, expect="dict")
    if data is None:
        return []

    out: list[RefineResult] = []
    for c in data.get("candidates") or []:
        r = (c.get("refined") or "").strip()
        d = (c.get("description") or "").strip()
        if r:
            out.append(RefineResult(
                refined=r, description=d,
                overflow=_overflow_for(r, "critical"),
            ))
    return out


@observe(name="knowledge_conflict_detect", as_type="chain")
def detect_conflicts(new_content: str, existing: list[dict]) -> ConflictReport:
    """
    与既有知识比对, 让 LLM 标记冲突。
    existing 每项需含 id/content; 空 list 时直接返回空报告 (不调 LLM)。
    所有失败模式均返回空 ConflictReport。
    """
    if not existing:
        return ConflictReport()

    # 硬上限防 prompt 爆炸
    corpus = "\n".join(f"[{e['id']}] {e['content']}" for e in existing[:_CONFLICT_EXISTING_LIMIT])
    user_msg = f"【新条目】\n{new_content}\n\n【既有知识】\n{corpus}"

    try:
        text = chat_completion(
            [{"role": "system", "content": _CONFLICT_PROMPT},
             {"role": "user", "content": user_msg}],
            temperature=0.1,
            max_tokens=1024,  # noqa: hardcode
        )
    except Exception as e:
        log.error("[intake] conflict LLM 失败: %s", e)
        raise IntakeLLMError(
            f"conflict 检测 LLM 不可用, 无法保证不引入冲突 — 请稍后重试: {e}"
        ) from e

    from app.engine.json_parser import parse_llm_json
    data = parse_llm_json(text, expect="dict")
    if data is None:
        return ConflictReport()

    items: list[ConflictItem] = []
    for c in data.get("conflicts") or []:
        try:
            raw_reason = str(c.get("reason", ""))
            if len(raw_reason) > _CONFLICT_REASON_MAX:
                log.debug("[intake] conflict reason 被截断: %s...", raw_reason[:40])
            suggested = c.get("suggested", "coexist")
            if suggested not in _VALID_SUGGESTED:
                suggested = "coexist"
            items.append(ConflictItem(
                existing_id=int(c["existing_id"]),
                reason=raw_reason[:_CONFLICT_REASON_MAX],
                suggested=suggested,
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return ConflictReport(items=items)
