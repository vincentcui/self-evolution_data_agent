"""
解析质量评估器 — LLM 评估解析完整性
输入: 解析统计 + 已训练文档摘要
输出: 评分(0-100) + 摘要
"""

import logging

from langfuse import observe

from app.config import settings
from app.engine.json_parser import parse_llm_json
from app.engine.llm import chat_completion_checked
from app.knowledge.parse_result import ParseReport

logger = logging.getLogger(__name__)

# ── 评估 prompt 模板 — 维度 4 由 _build_eval_prompt 动态填充 ──
_EVAL_PROMPT_TEMPLATE = """\
你是数据库知识库质量评估专家。基于以下代码解析结果和训练文档, 评估知识库的完整性。

评估维度:
1. 覆盖率: 发现的表/集合是否足以支撑常见业务查询
2. 关联完整性: 表间关系是否清晰定义
3. 字段语义: 字段含义是否从注释/命名中充分提取
4. {dimension_4}

返回严格 JSON (不要包含 markdown 代码块标记):
{{
  "score": 75,
  "summary": "评估摘要: 发现了 X 张表..., 字段覆盖度 XX%..."
}}

score 评分标准:
- 90-100: 表/集合覆盖充分, 关系清晰, 语义丰富
- 70-89: 基本可用, 少量信息缺失
- 50-69: 可用但有明显缺口
- 0-49: 严重不足, 需要补充大量信息"""

# ── 维度 4 文案 ──
_DIM4_MYSQL = "示例查询覆盖度: MyBatis SQL 是否覆盖了主要查询模式"
_DIM4_MONGO = "查询模式覆盖度: DAO 层查询模式是否覆盖了主要数据访问场景"
_DIM4_MIXED = "查询覆盖度: MySQL 的 MyBatis SQL 和 MongoDB 的 DAO 查询模式是否覆盖主要场景"


def _build_eval_prompt(report: ParseReport, trained_docs: list[str]) -> str:
    """
    构建评估 prompt — 纯函数, 可独立测试
    按 DB 类型动态切换维度 4 + stats_summary 扩展
    """
    # ── DB 类型检测 (纯数值判断, 不调 LLM) ──
    has_mysql = report.ddls_trained > 0 or report.sqls_trained > 0
    has_mongo = report.query_patterns_trained > 0 or any(
        doc.startswith("MongoDB 集合") for doc in trained_docs
    )

    if has_mysql and has_mongo:
        dim4 = _DIM4_MIXED
    elif has_mongo:
        dim4 = _DIM4_MONGO
    else:
        dim4 = _DIM4_MYSQL

    system_prompt = _EVAL_PROMPT_TEMPLATE.format(dimension_4=dim4)

    # ── stats_summary ──
    stats_summary = (
        f"扫描文件: {report.stats.files_scanned}, "
        f"成功解析: {report.stats.files_parsed}, "
        f"跳过: {report.stats.files_skipped}, "
        f"失败: {report.stats.files_errored}\n"
        f"提取项目: {report.stats.items_extracted}\n"
        f"发现表/集合: {', '.join(report.stats.tables_found) or '无'}\n"
        f"训练文档: DDL={report.ddls_trained}, 描述={report.docs_trained}, "
        f"SQL={report.sqls_trained}, 查询模式={report.query_patterns_trained}"
    )

    # 取前 50 条训练文档控制 token
    docs_preview = "\n".join(trained_docs[:50])
    if len(trained_docs) > 50:
        docs_preview += f"\n... 共 {len(trained_docs)} 条, 仅展示前 50 条"

    user_content = f"解析统计:\n{stats_summary}\n\n训练文档摘要:\n{docs_preview}"

    return f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_content}"


@observe(name="knowledge_evaluation", as_type="chain")
def evaluate_parse_quality(
    report: ParseReport, trained_docs: list[str]
) -> ParseReport:
    """
    LLM 评估解析质量, 填充 report 的 completeness_score、evaluation_summary.
    """
    # ── 构建 prompt ──
    full_prompt = _build_eval_prompt(report, trained_docs)
    # 从 full_prompt 中分离 system / user
    system_part = full_prompt.split("\n\nUSER:\n")[0].removeprefix("SYSTEM:\n")
    user_part = full_prompt.split("\n\nUSER:\n")[1]

    logger.info("质量评估开始 files_scanned=%d items=%d trained_docs=%d",
                report.stats.files_scanned, report.stats.items_extracted, len(trained_docs))

    try:
        messages = [
            {"role": "system", "content": system_part},
            {"role": "user", "content": user_part},
        ]

        # 首次尝试 evaluator_max_tokens_first, 截断则以 retry 上限重试一次
        resp = chat_completion_checked(messages=messages, temperature=0.1,
                                       max_tokens=settings.evaluator_max_tokens_first, thinking=False)
        if resp.truncated:
            logger.warning("评估输出被截断 (len=%d), 以 max_tokens=%d 重试", len(resp.text), settings.evaluator_max_tokens_retry)
            resp = chat_completion_checked(messages=messages, temperature=0.1,
                                           max_tokens=settings.evaluator_max_tokens_retry, thinking=False)
            if resp.truncated:
                logger.warning("重试仍截断 (len=%d), 尝试修复 JSON", len(resp.text))

        raw = resp.text
        data = parse_llm_json(raw, expect="dict")
        if data:
            report.completeness_score = max(0, min(100, int(data.get("score", 0))))  # noqa: hardcode
            report.evaluation_summary = data.get("summary", "")
            logger.info("质量评估完成 score=%d", report.completeness_score)
        else:
            report.completeness_score = 0
            report.evaluation_summary = "评估失败: LLM 返回格式异常"
    except Exception as e:
        logger.warning("Quality evaluation failed: %s", e)
        report.completeness_score = 0
        report.evaluation_summary = f"评估失败: {e}"

    return report
