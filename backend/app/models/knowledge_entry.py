"""
知识条目 — 唯一 canonical (Stage 1 升级)

scope 设计: namespace_id 为 NULL 时表示全局知识, 所有命名空间共享
status 四态机: proposed (待审) → canonical (进 RAG) / superseded (被替代) / rejected (拒绝)
"""

from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, LOCAL_NOW

# ── 状态字面量收紧 — 替代散落 6 处 status 字符串拼写 (防 typo) ──
KnowledgeStatus = Literal["proposed", "canonical", "superseded", "rejected"]
"""knowledge_entries.status 四态机的类型别名.

写入路径 (upsert_knowledge_entry / write_audit / 模型构造) 应优先用此 Literal,
让 type checker 在编译期捕获拼写错误 ("canonial" / "approved" 等).
"""


KnowledgeSource = Literal["manual", "agent_learn", "schema", "code_extract"]
"""knowledge_entries.source 渠道维度类型别名.

写入路径 (extraction_writer / terminology_refresher / knowledge API / knowledge_tools)
优先用此 Literal, 让 type checker 在编译期捕获拼写错误.
"""


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int | None] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=True
    )

    # ── 类型与状态 ────────────────────────────────────────
    entry_type: Mapped[str] = mapped_column(String(20))
    """∈ {terminology, instance_alias, example, rule, route_hint}"""

    status: Mapped[KnowledgeStatus] = mapped_column(String(16), default="proposed")
    """∈ {proposed, canonical, superseded, rejected} — 见 KnowledgeStatus Literal"""

    tier: Mapped[str] = mapped_column(String(16), default="normal")
    """∈ {critical, normal} — critical 直注入 prompt, normal 进 RAG"""

    # ── 内容 ────────────────────────────────────────────
    content: Mapped[str] = mapped_column(Text)
    """自然语言精炼 (供 RAG embedding + 审核员阅读)"""

    payload: Mapped[str] = mapped_column(Text, default="{}")
    """JSON, 按 entry_type 的 Pydantic schema (app/schemas/knowledge_payload.py)"""

    # ── 来源追溯 ────────────────────────────────────────
    source: Mapped[str] = mapped_column(String(20), default="manual")
    """∈ {manual, agent_learn, schema, code_extract} — 见 KnowledgeSource Literal"""

    raw_input: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")

    # ── 关联与审计 ──────────────────────────────────────
    repo_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("git_repos.id", ondelete="SET NULL"), nullable=True
    )

    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    """agent 写入时的 {trace_id, user_answer, related_query_history_id, ...}"""

    is_superseded: Mapped[bool] = mapped_column(default=False)
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_entries.id", ondelete="SET NULL"), nullable=True,
    )

    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    refined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=LOCAL_NOW)

    # ── Stage 2 抓手 A: HyQE ────────────────────────
    hypothetical_queries_json: Mapped[str] = mapped_column(
        Text, default="[]"
    )
    """[{q: str, generated_at: ISO8601, model: str}], rule / route_hint 入库时
       LLM 同步生成. 仅前端审核 UI 展示用 (ChromaDB 多向量是真相源)."""

    # ── Stage 2 抓手 B: 召回反馈环 ────────────────────
    recall_count: Mapped[int] = mapped_column(default=0)
    """累计被 lookup_knowledge 召回 (返给 agent) 的次数."""
    adopted_count: Mapped[int] = mapped_column(default=0)
    """召回后, 同 trace 内 agent 推进 (隐式信号: 见 §B.2.3) 的次数."""
    negative_signal_count: Mapped[int] = mapped_column(default=0)
    """召回后, 同 trace 内 agent 紧跟 fetch_schema / clarify_with_user 的"未解决"次数."""
    last_recalled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    """上次召回时间. 衰减 cron 90 天阈值参考."""

    # ── Stage 2 抓手 D: A-MEM 演化 ─────────────────────
    related_entry_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    """[{related_entry_id: int, relation: equivalent|supplement|conflict,
       llm_reason: str ≤40 字, detected_at: ISO8601}].
       入库即演化期 LLM 判定后写入. 审核 UI 展示, approve 时驱动合并/补充/覆盖."""
