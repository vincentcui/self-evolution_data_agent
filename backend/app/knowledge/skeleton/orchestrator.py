"""Orchestrator — scan → explore → split → parallel subagents (exception-isolated) → merge.

Rev 2 架构:
    scan_skeleton → explore_repo → split_file_list → 并发 fan-out → merge_results

降级规则:
    - scan_skeleton 异常     → 单 agent (skeleton=None)
    - explorer 返回空文件列表 → 单 agent (skeleton=None)
    - split 后 ≤1 unit       → 单 agent (skeleton=work_unit or None)
    - 子 agent 崩溃          → SubagentResult(result=None), 其他不受影响
"""
from __future__ import annotations

import asyncio
import logging

from langfuse import observe

from app.config import settings
from app.knowledge.extraction_agent import ExtractionResult, run_extraction_agent
from app.knowledge.skeleton._base import SubagentResult, merge_results
from app.knowledge.skeleton.explorer import explore_repo
from app.knowledge.skeleton.scanner import scan_skeleton
from app.knowledge.skeleton.splitter import split_file_list
from app.tracing import get_client as _lf_client

logger = logging.getLogger(__name__)


def _lf_obs(name: str, *, input: dict | None = None, output: dict | None = None) -> None:
    """langfuse 手动子 span — 小 payload, try/except 保护, 不阻断主业务."""
    lf = _lf_client()
    if lf is None:
        return
    try:
        span = lf.start_observation(name=name, as_type="span",
                                    input=input, output=output)
        span.end()
    except Exception:
        pass


@observe(name="orchestrated_extraction", as_type="chain", capture_input=False, capture_output=False)
async def orchestrated_extraction(
    *,
    repo_path: str,
    hint_text: str | None = None,
    repo_name: str = "",
    namespace_id: int | None = None,
) -> ExtractionResult:
    """Rev 2 divide-and-conquer 提取.

    阶段 (带降级):
        1. scan_skeleton    — 失败则单 agent (skeleton=None)
        2. explore_repo     — 返回空 focus_files 则单 agent (skeleton=None)
        3. split_file_list  — ≤1 unit 则单 subagent
        4. 并发 fan-out     — Semaphore + exception-isolated
        5. merge_results    — 汇聚所有子结果
    """
    # ── 阶段 1: 扫描骨架 ────────────────────────────────────────────
    try:
        skeleton = scan_skeleton(repo_path)
    except Exception:
        logger.warning(
            "[%s] skeleton scan failed — fallback to single-agent",
            repo_name,
            exc_info=True,
        )
        return await run_extraction_agent(
            repo_path=repo_path,
            hint_text=hint_text,
            repo_name=repo_name,
            namespace_id=namespace_id,
        )

    # ── 阶段 2: Explorer 语义过滤 ────────────────────────────────────
    explorer_result = await explore_repo(
        repo_path=repo_path,
        skeleton=skeleton,
        repo_name=repo_name,
        namespace_id=namespace_id,
    )

    files_n = len(explorer_result.focus_files)
    classes_n = len(explorer_result.focus_classes)
    logger.info(
        "[%s] Explorer done: %d files, %d classes, status=%s exit_reason=%s",
        repo_name, files_n, classes_n,
        explorer_result.status,
        explorer_result.reasoning[:200] if explorer_result.reasoning else "?",
    )
    # 文件列表分片打印 — 不截断, 每 20 个一行
    _batch = 20
    if files_n > 0:
        for i in range(0, files_n, _batch):
            chunk = explorer_result.focus_files[i:i + _batch]
            logger.info("[%s] Explorer focus_files [%d-%d/%d]: %s",
                        repo_name, i + 1, min(i + _batch, files_n), files_n, chunk)
    if classes_n > 0:
        for i in range(0, classes_n, _batch):
            chunk = explorer_result.focus_classes[i:i + _batch]
            logger.info("[%s] Explorer focus_classes [%d-%d/%d]: %s",
                        repo_name, i + 1, min(i + _batch, classes_n), classes_n, chunk)
    # ── langfuse 手动提交: Explorer seed 指标 ──
    _lf_obs("explorer.seeds", input={}, output={
        "focus_files_count": files_n,
        "focus_classes_count": classes_n,
        "status": explorer_result.status,
        "exit_reason": (explorer_result.reasoning or "")[:500],
    })

    if not explorer_result.focus_files:
        logger.warning("[%s] explorer empty — fallback to single-agent", repo_name)
        return await run_extraction_agent(
            repo_path=repo_path,
            hint_text=hint_text,
            repo_name=repo_name,
            namespace_id=namespace_id,
        )

    # ── 阶段 3: 均匀切片 ─────────────────────────────────────────────
    work_units = split_file_list(
        explorer_result.focus_files,
        explorer_result.focus_classes,
        skeleton.class_index,
    )

    if len(work_units) <= 1:
        # 0 或 1 个 unit — 跳过 fan-out 开销
        logger.info(
            "[%s] splitter → %d unit(s), skip fan-out → single-agent (files=%d classes=%d)",
            repo_name, len(work_units),
            len(work_units[0].focus_files) if work_units else 0,
            len(work_units[0].focus_classes) if work_units else 0,
        )
        return await run_extraction_agent(
            repo_path=repo_path,
            skeleton=work_units[0] if work_units else None,
            hint_text=hint_text,
            repo_name=repo_name,
            namespace_id=namespace_id,
        )

    # KEEP 此精确日志 — Gate 5 验收解析
    logger.info("[%s] orchestrating %d subagents", repo_name, len(work_units))
    logger.info(
        "[%s] WorkUnits: %s", repo_name,
        [(wu.name, len(wu.focus_files), len(wu.focus_classes)) for wu in work_units],
    )
    # ── langfuse 手动提交: fan-out 指标 ──
    _lf_obs("orchestrator.fan_out", input={}, output={
        "subagent_count": len(work_units),
        "work_units": [
            {"name": wu.name, "files": len(wu.focus_files), "classes": len(wu.focus_classes)}
            for wu in work_units
        ],
    })

    # ── 阶段 4: 并发 fan-out ─────────────────────────────────────────
    sem = asyncio.Semaphore(settings.agentic_extract_subagent_concurrency)

    async def _run_one(wu):
        async with sem:
            try:
                result = await run_extraction_agent(
                    repo_path=repo_path,
                    skeleton=wu,
                    hint_text=hint_text,
                    repo_name=(
                        f"{repo_name}/{wu.name}"
                        if repo_name
                        else wu.name
                    ),
                    namespace_id=namespace_id,
                )
                return SubagentResult(work_unit_name=wu.name, result=result)
            except Exception:
                logger.exception("[%s/%s] subagent crashed", repo_name, wu.name)
                return SubagentResult(work_unit_name=wu.name, result=None)

    sub_results = await asyncio.gather(*[_run_one(wu) for wu in work_units])

    # ── 阶段 5: 合并 ─────────────────────────────────────────────────
    merged = merge_results(sub_results)
    logger.info(
        "[%s] orchestrated done — %d obj, %d knowledge, status=%s reason=%s",
        repo_name,
        len(merged.objects),
        len(merged.knowledge_proposals),
        merged.status,
        merged.reason,
    )
    # ── langfuse 手动提交: merge 最终指标 ──
    ok_count = sum(1 for sr in sub_results if sr.result and sr.result.status == "ok")
    failed_count = sum(1 for sr in sub_results if sr.result is None or sr.result.status == "failed")
    partial_count = len(sub_results) - ok_count - failed_count
    _lf_obs("orchestrator.merge", input={}, output={
        "objects_count": len(merged.objects),
        "knowledge_count": len(merged.knowledge_proposals),
        "status": merged.status,
        "subagent_ok": ok_count,
        "subagent_failed": failed_count,
        "subagent_partial": partial_count,
        "reason": merged.reason[:500],
    })
    return merged
