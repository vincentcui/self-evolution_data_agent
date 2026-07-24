"""Extraction prompt loader — prompts/ .md 优先, 模块内常量兜底。

agentic-repo-extractor 后, schema 抽取归 agent loop (extraction_agent.py),
旧 Java 多轮 prompt 常量 (enum/relationship/where/terminology/rule) 已随
code_parser / relationship_extractor / dynamic_branch 一并删除。LLM 调用重试 +
失败留痕原 llm_extract_with_retry 亦随其调用方删除 (瞬时重试下沉至 SDK 默认重试,
失败留痕走 explain_gate.write_extraction_failure)。

此模块仅保留 extraction-agent-base 的加载入口 + .md 缺失时的降级兜底常量。
"""
from __future__ import annotations


def load_prompt_or_fallback(name: str) -> str:
    """Try loading prompt from prompts/ .md file, fall back to in-module constant."""
    try:
        from app.knowledge.prompt_loader import load_prompt
        tpl = load_prompt(name)
        return tpl.body
    except Exception:
        fallback = _PROMPT_FALLBACK_MAP.get(name)
        if fallback:
            return fallback
        raise


# ── agentic-repo-extractor: extraction-agent-base fallback (degraded mode only) ──
# 正常路径走 prompts/extraction-agent-base.md (load_prompt 成功); 此常量仅 .md
# 缺失时兜底, 内容为模板正文的极简摘要 (非逐字), 由 ${max_depth} 运行时替换.
_EXTRACTION_AGENT_BASE_FALLBACK = (
    "你是代码 schema 提取专家。分析仓库源码，提取所有数据持久化定义。"
    "按探索原则自主发现实体、递归展开字段、提取枚举、标记关联关系。"
    "嵌套深度上限 ${max_depth} 层。每个持久化对象通过 emit_schema_object 提交。"
    "发现可复用查询模式时通过 emit_knowledge(entry_type=example) 提交 question + native query body。"
)


_PROMPT_FALLBACK_MAP: dict[str, str] = {
    "extraction-agent-base": _EXTRACTION_AGENT_BASE_FALLBACK,
}
