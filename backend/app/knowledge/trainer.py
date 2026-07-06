"""
训练管道 — 编排: 克隆 → LLM 解析 → 构建文档 → 灌入引擎 → 质量评估
"""

import asyncio
import json
import re as _re
import time
from typing import Any, Callable, Coroutine

from langfuse import observe
from sqlalchemy import select

from app.config import settings
from app.db.metadata import async_session
from app.knowledge.evaluator import evaluate_parse_quality
from app.knowledge.git_manager import clone_or_update
from app.knowledge.parse_result import CodeParseResult, ParseReport, ParserStats
from app.knowledge.schema_builder import (
    build_ddl_from_jpa,
    build_doc_from_jpa,
)
from app.knowledge.trainer_purge import purge_legacy_for_full_rebuild
from app.logging_config import get_logger, trace_id_var
from app.models import GitRepo
from app.models.base import local_now
from app.models.extractor_profile import ExtractorProfile
from app.models.namespace import DataSource
from app.tracing import get_client as _get_lf_client

log = get_logger("trainer")

# 进度回调类型: async (percent, message) -> None
ProgressCallback = Callable[[int, str], Coroutine[Any, Any, None]]


def _repo_name(url: str) -> str:
    """git@gitlab.com:org/foo-service.git → foo-service"""
    return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


# ════════════════════════════════════════════
#  Agent → 下游通道映射 (Phase 2: agentic 提取接线)
# ════════════════════════════════════════════

def _serialize_sub_fields(sub_fields: list[dict]) -> list[dict]:
    """agent sub_fields → writer 期望的 dict 格式 (递归)."""
    out: list[dict] = []
    for sf in sub_fields:
        out.append({
            "name": sf.get("name"),
            "type": sf.get("type", "String"),
            "description": sf.get("description") or "",
            "nullable": sf.get("nullable"), "indexed": sf.get("indexed"),
            "enum_values": sf.get("enum_values", []),
            "sub_fields": _serialize_sub_fields(sf.get("sub_fields", [])),
        })
    return out


def _map_agent_to_channels(
    agent_objects: list[dict], knowledge_proposals: list[dict],
    coll_to_db: dict[str, tuple[str, str]] | None,
) -> tuple[CodeParseResult, list[dict]]:
    """Agent emit 产物 → CodeParseResult 7 通道 + business_examples (sql2nl).

    database 字段留给 write_canonical_candidates_from_parse 经 coll_to_db 补全,
    此处顺手填一份便于隔离测试与日志。enum_classes 通道留空 — agent 把枚举内联到
    字段 enum_values; 独立 EnumDictionary 由确定性安全网 (java glob) 兜底。
    """
    coll_to_db = coll_to_db or {}
    jpa_entities: list[dict] = []
    mongo_documents: list[dict] = []
    mybatis_entries: list[dict] = []
    business_terms: list[dict] = []
    business_rules: list[dict] = []
    business_examples: list[dict] = []

    for obj in agent_objects:
        name = obj.get("name")
        if not name:
            continue
        paradigm = obj.get("paradigm", "document")
        base = {
            "database": coll_to_db.get(name, ("", ""))[1],
            "source_ref": obj.get("source_ref") or "",
            "description": obj.get("description") or "",
            "fields": [],
            "relations": [{
                "from_target": name, "from_field": r.get("from_field"),
                "to_target": r.get("to_object"), "to_field": r.get("to_field"),
                "relation_type": r.get("relation_type", "foreign_key"),
                "is_required": True, "source": "agentic",
            } for r in obj.get("relations", [])],
        }
        for fd in obj.get("fields", []):
            base["fields"].append({
                "name": fd.get("name"),
                "type": fd.get("type", "String"),
                "description": fd.get("description") or "",
                "nullable": fd.get("nullable"), "indexed": fd.get("indexed"),
                "enum_values": fd.get("enum_values", []),
                "sub_fields": _serialize_sub_fields(fd.get("sub_fields", [])),
            })
        if paradigm == "relational":
            base["table_name"] = name
            base["table"] = name
            jpa_entities.append(base)
        elif paradigm == "document":
            base["collection"] = name
            base["collection_name"] = name
            base["class_name"] = name
            mongo_documents.append(base)

    for kp in knowledge_proposals:
        et = kp.get("entry_type")
        payload = dict(kp.get("payload") or {})
        if et == "route_hint":
            payload.setdefault("type", "select")  # _write_route_hints 按 type 过滤非 select
            mybatis_entries.append(payload)
        elif et == "terminology":
            payload.setdefault("primary_collection", "")
            # primary_database + db_type 均由下游程序化反查, 不让 agent 猜:
            #   primary_database ← coll_to_db (实际 DB 连接反查表→库)
            #   db_type          ← writer 经 DataSource 反查
            primary_coll = payload.get("primary_collection", "")
            if not payload.get("primary_database"):
                payload["primary_database"] = coll_to_db.get(primary_coll, ("", ""))[1]
            business_terms.append(payload)
        elif et == "rule":
            business_rules.append(payload)
        elif et == "example":
            business_examples.append(payload)

    code_result = CodeParseResult(
        jpa_entities=jpa_entities,
        mongo_documents=mongo_documents,
        mybatis_entries=mybatis_entries,
        business_terms_candidates=business_terms,
        business_rules_candidates=business_rules,
    )
    return code_result, business_examples


def _stats_from_agent(objects: list[dict]) -> ParserStats:
    """合成 ParserStats — agent 无文件级统计, 取对象数与对象名集合."""
    return ParserStats(
        items_extracted=len(objects),
        tables_found=[o.get("name", "") for o in objects if o.get("name")],
    )


async def _load_profile_hint(repo_id: int) -> str | None:
    """读 git_repos.profile_id → enabled profile 的 hint_text (可选纠偏)."""
    async with async_session() as db:
        repo = await db.get(GitRepo, repo_id)
        if not repo or not repo.profile_id:
            return None
        pf = await db.get(ExtractorProfile, repo.profile_id)
        if pf and pf.is_enabled:
            return pf.hint_text
    return None


async def _build_coll_to_db(ns_id: int, name: str) -> dict[str, tuple[str, str]]:
    """实时连接 namespace 下各 DataSource, 列其库表/集合 → {对象名: database} 反查表.

    用于补全 agent 产物缺失的 database 字段。连接失败的单个 DS 跳过 (best-effort)。
    """
    from app.engine.drivers import DRIVERS, get_driver

    coll_to_db: dict[str, tuple[str, str]] = {}
    async with async_session() as ds_db:
        ds_rows = list((await ds_db.execute(
            select(DataSource).where(DataSource.namespace_id == ns_id).order_by(DataSource.id)
        )).scalars().all())
        for ds in ds_rows:
            if ds.db_type not in DRIVERS:
                log.warning("[%s] collection→db 跳过未知 db_type=%s ds_id=%d",
                            name, ds.db_type, ds.id)
                continue
            try:
                driver = get_driver(ds.db_type)
                names = await driver.list_object_names(ds)
                for n in names:
                    coll_to_db.setdefault(n, (ds.db_type, ds.database))
            except Exception as e:
                log.warning("[%s] collection→db 反查连接失败 ds_id=%d db_type=%s: %s",
                            name, ds.id, ds.db_type, e)
        await ds_db.commit()
    if coll_to_db:
        log.info("[%s] collection→db 反查表 %d 条", name, len(coll_to_db))
    return coll_to_db


# ── enum agent system prompt (语言无关) ──────────────────

_ENUM_AGENT_SYSTEM_PROMPT = """\
<role>你是代码枚举/常量定义提取专家</role>
<goal>从仓库源码中提取所有枚举/常量类的完整定义。db_value 是数据库中实际存储的值 — 通过构造参数类型、getter 方法名、查找方法 (如 getByCode/getByValue) 和注释综合推断, 不要依赖固定的参数位置规则。</goal>

<output>
每个枚举通过 emit_enum_definition 提交:
- enum_class: 枚举类名
- fully_qualified_name: 全限定名 (可选)
- values: [{name: 枚举常量名, db_value: int|string 数据库存储值, description: 语义描述或 null}]
- source_file: 定义文件路径
</output>

<examples>
Java 构造参数:
  public enum OrderStatus { CREATED(1, "已创建"), PAID(2, "已支付"); }
  → emit_enum_definition(enum_class="OrderStatus", values=[{name:"CREATED", db_value:1, description:"已创建"}, {name:"PAID", db_value:2, description:"已支付"}])

Python StrEnum:
  class SourceStatus(str, Enum): MANUAL=("manual", "手动输入"); AUTO=("auto", "自动生成")
  → emit_enum_definition(enum_class="SourceStatus", values=[{name:"MANUAL", db_value:"manual", description:"手动输入"}, {name:"AUTO", db_value:"auto", description:"自动生成"}])

TypeScript number enum:
  enum Status { Active = 1, Inactive = 0 }
  → emit_enum_definition(enum_class="Status", values=[{name:"Active", db_value:1, description:null}, {name:"Inactive", db_value:0, description:null}])
</examples>

<escape>未发现枚举 → 如实报告, 不编造。</escape>"""


async def _run_enum_safety_net(local_path: str, ns_id: int, name: str) -> None:
    """枚举提取 agent — 语言无关探索 + emit_enum_definition 收口落地 EnumDictionary.

    用 agent loop (4 读工具 + emit_enum_definition) 替代旧 Java glob + per-file LLM,
    支持多语言枚举提取。写路径 (EnumDictionary upsert) 保持不变。
    """
    _t0 = time.monotonic()
    log.info("[%s] enum agent 开始...", name)
    import json as _json

    from app.engine.llm import build_assistant_message, chat_completion_with_tools
    from app.knowledge.enum_dictionary_writer import upsert_enum_dictionary_from_code
    from app.knowledge.enum_extractor import EnumDef, EnumValue
    from app.knowledge.extraction_tools import (
        EMIT_ENUM_DEFINITION_SPEC,
        EXTRACTION_TOOL_SPECS,
        find_files,
        grep,
        list_dir,
        read_file,
    )

    # ── tool specs: 4 read tools + emit_enum_definition ──
    tool_specs = EXTRACTION_TOOL_SPECS[:4] + [EMIT_ENUM_DEFINITION_SPEC]

    # ── tool functions (sandbox to local_path) ──
    emitted: list[dict] = []

    def _emit_handler(**data: object) -> dict:
        enum_class = str(data.get("enum_class", "")) if data.get("enum_class") else ""
        values = data.get("values")
        if not enum_class:
            return {"status": "error", "message": "enum_class 缺失"}
        if not values or not isinstance(values, list):
            return {"status": "error", "message": "values 非空数组"}
        if not all(isinstance(v, dict) and v.get("name") for v in values):
            return {"status": "error", "message": "values[].name 缺失"}
        emitted.append(dict(data))
        return {"status": "ok", "message": f"已提交: {enum_class}"}

    tool_fns: dict[str, Callable] = {
        "list_dir": lambda **kw: list_dir(str(kw.get("path", ".")), local_path),
        "read_file": lambda **kw: read_file(
            str(kw["path"]), local_path,
            kw.get("offset"), kw.get("limit"),
        ),
        "grep": lambda **kw: grep(
            str(kw["pattern"]), str(kw.get("path", ".")), local_path,
            bool(kw.get("recursive", True)),
        ),
        "find_files": lambda **kw: find_files(str(kw["glob"]), local_path),
        "emit_enum_definition": _emit_handler,
    }

    # ── agent loop ──
    dw = settings.agentic_extract_dead_loop_window
    max_iter = settings.agentic_extract_max_iterations
    recent: list[tuple[str, str, str]] = []
    iteration = 0

    messages: list[dict] = [
        {"role": "system", "content": _ENUM_AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"提取仓库 {local_path} 中所有枚举/常量类定义, "
            "逐个 emit_enum_definition 提交。"
        )},
    ]

    while iteration < max_iter:
        iteration += 1
        try:
            response = await chat_completion_with_tools(
                messages=messages, tools=tool_specs, thinking=False,
            )
        except Exception:
            log.exception("[%s] enum agent LLM 调用失败 (iteration=%d)", name, iteration)
            break

        if not response.tool_calls:
            break

        tool_results: list[tuple[Any, dict]] = []
        dead_loop_hit = False
        for tc in response.tool_calls:
            fn = tool_fns.get(tc.name)
            if fn is None:
                result: dict = {"status": "error", "error_type": "UNKNOWN_TOOL",
                                "message": f"未知工具: {tc.name}"}
            else:
                try:
                    result = fn(**tc.input)  # type: ignore[operator]
                except Exception as e:
                    result = {"status": "error", "error_type": type(e).__name__,
                              "message": str(e)}

            tc_sig = (
                tc.name,
                _json.dumps(tc.input, sort_keys=True, default=str),
                result.get("error_type", "") if result.get("status") == "error" else "ok",
            )
            recent.append(tc_sig)
            if len(recent) > dw:
                recent.pop(0)
            if len(recent) >= dw and \
               all(item == recent[-1] for item in recent[-dw:]):
                dead_loop_hit = True
                tool_results.append((tc, result))
                break
            tool_results.append((tc, result))

        tool_names = [tc.name for tc in response.tool_calls]
        log.info(
            "[%s] enum agent  iteration=%-3d  tools=%-40s  enums=%-3d",
            name, iteration, str(tool_names), len(emitted),
        )

        processed_tcs = [tc for tc, _ in tool_results]
        messages.append(build_assistant_message(response, tool_calls=processed_tcs))

        def _serialize(r: dict) -> str:
            raw = _json.dumps(r, ensure_ascii=False, default=str)
            return raw if len(raw) <= settings.agent_tool_result_max_chars else \
                raw[:settings.agent_tool_result_max_chars] + "\n\n[输出截断]"

        for tc, result in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _serialize(result),
            })

        if dead_loop_hit:
            log.warning("[%s] enum agent dead_loop, 保留 %d 条已提取", name, len(emitted))
            break

    # ── write to EnumDictionary (写路径不变) ──
    t_elapsed = time.monotonic() - _t0
    if emitted:
        async with async_session() as enum_db:
            for data in emitted:
                enum_def = EnumDef(
                    enum_class=str(data.get("enum_class", "")),
                    fully_qualified_name=str(data.get("fully_qualified_name", "")),
                    values=[
                        EnumValue(
                            name=str(v.get("name", "")),
                            db_value=v.get("db_value", v.get("name", "")),
                            description=v.get("description"),
                        )
                        for v in (data.get("values") or [])
                        if isinstance(v, dict)
                    ],
                )
                if enum_def.enum_class:
                    await upsert_enum_dictionary_from_code(enum_db, ns_id, enum_def)
            await enum_db.commit()
        log.info("[%s] enum agent: %d 个枚举类落 EnumDictionary  elapsed=%.1fs",
                 name, len(emitted), t_elapsed)
    else:
        log.info("[%s] enum agent: 未发现枚举  elapsed=%.1fs", name, t_elapsed)


def _sync_trace_id_to_log() -> None:
    """
    将 langfuse @observe 上下文中的 trace_id 同步到 logging contextvar.
    Worker 路径无 HTTP middleware, 必须在 @observe 入口手动同步,
    否则 worker 日志的 trace 字段为 "-".
    """
    lf = _get_lf_client()
    if lf is None:
        return
    try:
        tid = lf.get_current_trace_id()
        if tid:
            trace_id_var.set(tid)
    except Exception:
        pass


# ── 短命 session helpers — 用完即还连接 ──

async def _update_repo_status(repo_id: int, status: str):
    async with async_session() as db:
        repo = await db.get(GitRepo, repo_id)
        if repo:
            repo.parse_status = status
            await db.commit()


async def _update_repo_fields(repo_id: int, **fields):
    async with async_session() as db:
        repo = await db.get(GitRepo, repo_id)
        if repo:
            for k, v in fields.items():
                setattr(repo, k, v)
            await db.commit()


def _report_to_dict(report: ParseReport) -> dict:
    """ParseReport dataclass → 可序列化 dict"""
    return {
        "repo_id": report.repo_id,
        "duration_seconds": report.duration_seconds,
        "stats": {
            "files_scanned": report.stats.files_scanned,
            "files_parsed": report.stats.files_parsed,
            "files_skipped": report.stats.files_skipped,
            "files_errored": report.stats.files_errored,
            "items_extracted": report.stats.items_extracted,
            "tables_found": report.stats.tables_found,
        },
        "ddls_trained": report.ddls_trained,
        "docs_trained": report.docs_trained,
        "sqls_trained": report.sqls_trained,
        "query_patterns_trained": report.query_patterns_trained,
        "completeness_score": report.completeness_score,
        "evaluation_summary": report.evaluation_summary,
    }


# ════════════════════════════════════════════
#  带进度追踪的训练管道 (Phase 3)
# ════════════════════════════════════════════

def _build_docs(code_result):
    """同步构建 schema 文档 — 纯计算"""
    return (
        build_ddl_from_jpa(code_result.jpa_entities),
        build_doc_from_jpa(code_result.jpa_entities),
    )


def _collect_referenced_sql_tables(
    mybatis_entries: list[dict],
    jpa_entities: list[dict],
    coll_to_db: dict[str, tuple[str, str]],
) -> set[str]:
    """聚合本 repo 引用的 SQL 型表名，收窄 refresh_driver_canonicals 范围.

    信号:
        - mybatis: SELECT entry 的 SQL 中 FROM/JOIN 命中的表
        - jpa: @Table 关联的实体表
    coll_to_db 用于剔除非 SQL ds 的表名 (mongo collection 同名混入会被 dict 校验剔除).
    返回空集 → 本 repo 与 SQL 型数据源无关, refresh_driver_canonicals 早返 0 noop.
    """
    tables: set[str] = set()
    coll_key_by_casefold = {name.casefold(): name for name in coll_to_db}

    def _resolve_table_name(name: str) -> str | None:
        if name in coll_to_db:
            return name
        return coll_key_by_casefold.get(name.casefold())

    for entry in mybatis_entries or []:
        if (entry.get("type") or "").lower() != "select":
            continue
        sql = entry.get("canonical_sql") or entry.get("sql") or ""
        if not sql:
            continue
        for tbl in _re.findall(r"\b(?:FROM|JOIN)\s+([\w_]+)", sql, _re.IGNORECASE):
            if resolved := _resolve_table_name(tbl):
                tables.add(resolved)
    for entity in jpa_entities or []:
        tbl = entity.get("table_name") or entity.get("table") or ""
        if tbl and (resolved := _resolve_table_name(tbl)):
            tables.add(resolved)
    return tables


@observe(name="repo_training", as_type="chain")
async def run_training_pipeline_with_progress(
    repo_id: int, ns_id: int, ns_slug: str,
    repo_url: str, repo_branch: str,
    on_progress: ProgressCallback,
) -> ParseReport:
    """
    带进度回调的训练管道 — session-per-operation 模式
    每个 DB 写操作独立 session, 长时 I/O 阶段不持有连接

    阶段权重: 克隆 10% → 解析 30% → 构建 10% → 训练 30% → 评估 20%
    """
    _sync_trace_id_to_log()
    start = time.time()
    report = ParseReport(repo_id=repo_id)
    name = _repo_name(repo_url)
    log.info("[%s] 训练管道启动 repo_id=%d branch=%s",
             name, repo_id, repo_branch)

    # ── 1. 克隆 (0-10%) — to_thread 避免阻塞事件循环 ──
    await _update_repo_status(repo_id, "cloning")
    await on_progress(2, "克隆仓库...")
    t_clone = time.time()
    local_path, git_op = await asyncio.to_thread(clone_or_update, repo_url, repo_branch, repo_id)
    await _update_repo_fields(repo_id, local_path=local_path)
    log.info("[%s] %s 完成 耗时 %.1fs", name, git_op, time.time() - t_clone)
    await on_progress(10, "克隆完成")

    # ── 1.5 全量清场 (spec: 2026-05-21-git-source-full-purge) ──
    await on_progress(11, "清场中...")
    async with async_session() as purge_db:
        purge_result = await purge_legacy_for_full_rebuild(purge_db, repo_id, ns_id, repo_name=name)
        await purge_db.commit()
    log.info(
        "[%s] purge 完成 ke_deleted=%d open_tc=%d",
        name, purge_result.get("ke_deleted", 0), purge_result.get("term_conflicts", 0),
    )

    # ── 2. Agent 解析 (10-40%) — 替代旧 parse_repository ──
    await _update_repo_status(repo_id, "parsing")
    await on_progress(12, "LLM Agent 探索代码...")
    t_parse = time.time()
    hint_text = await _load_profile_hint(repo_id)
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction
    result = await orchestrated_extraction(repo_path=local_path, hint_text=hint_text, repo_name=name)

    # ── Step 3a: agent 状态守卫 ──
    if result.status == "failed":
        log.error("[%s] extraction agent failed: %s", name, result.reason)
        # 失败留痕: agentic LLM 调用失败写 ExtractionFailureLog, 供失败审计页可观测
        # (旧 Java pipeline 经 llm_retry 留痕; agentic 路径在此显式补回)
        async with async_session() as fail_db:
            from app.knowledge.explain_gate import write_extraction_failure
            await write_extraction_failure(
                fail_db,
                namespace_id=ns_id,
                repo_id=repo_id,
                extraction_kind="agentic_extraction",
                failure_type="llm_server_error",
                failure_message=result.reason or "agent error",
            )
            await fail_db.commit()
        await _update_repo_fields(repo_id, parse_status="error",
                                  error_message=result.reason or "agent error")
        report.evaluation_summary = f"提取失败: {result.reason}"
        report.duration_seconds = round(time.time() - start, 2)
        await on_progress(100, "提取失败")  # noqa: hardcode
        return report
    if result.status == "partial":
        log.warning("[%s] extraction agent partial: %s (objects=%d)",
                    name, result.reason, len(result.objects))
        await on_progress(38, f"提取部分完成: {result.reason} — {len(result.objects)} objects")
        # Persist partial reason — crashed subagent diagnostics must survive for audit
        async with async_session() as audit_db:
            from app.knowledge.explain_gate import write_extraction_failure
            await write_extraction_failure(
                audit_db,
                namespace_id=ns_id,
                repo_id=repo_id,
                extraction_kind="agentic_extraction",
                failure_type="partial_extraction",
                failure_message=result.reason or "partial extraction",
            )
            await audit_db.commit()

    report.stats = _stats_from_agent(result.objects)
    log.info(
        "[%s] Agent 解析完成 耗时 %.1fs 对象=%d 知识=%d",
        name, time.time() - t_parse, len(result.objects), len(result.knowledge_proposals),
    )
    await on_progress(40, "代码解析完成")

    # ── collection→db 反查表 (实时连 DataSource, 供 database 补全) ──
    coll_to_db = await _build_coll_to_db(ns_id, name)

    # ── agent 产物 → 7 通道 + business_examples (sql2nl) ──
    code_result, business_examples = _map_agent_to_channels(
        result.objects, result.knowledge_proposals, coll_to_db,
    )

    # ── 3. 构建文档 (40-50%) — 纯计算, to_thread 保险 ──
    await on_progress(42, "构建 Schema 文档...")
    ddls, jpa_docs = await asyncio.to_thread(
        _build_docs, code_result
    )
    log.info("[%s] Schema 构建 DDL=%d 文档=%d",
             name, len(ddls), len(jpa_docs))
    await on_progress(50, "文档构建完成")

    # ── 4.5 写入 schema canonical candidates ──
    await on_progress(56, "写入 schema 候选...")
    from app.knowledge.extraction_writer import write_canonical_candidates_from_parse

    await write_canonical_candidates_from_parse(
        namespace_id=ns_id,
        repo_id=repo_id,
        jpa_entities=code_result.jpa_entities,
        mongo_documents=code_result.mongo_documents,
        enum_classes=code_result.enum_classes,
        where_evidence=code_result.where_evidence,
        coll_to_db=coll_to_db or None,
        repo_name=name,
    )

    # ── 4.5b enum 确定性安全网 (D5/§6.3 — agent 漏标兜底, Java glob) ──
    await _run_enum_safety_net(local_path, ns_id, name)

    # ── 4.6 写入 knowledge entries proposed (含 sql2nl business_examples) ──
    await on_progress(60, "写入知识候选...")
    from app.knowledge.extraction_writer import extract_and_write_knowledge
    async with async_session() as ke_db:
        await extract_and_write_knowledge(
            ke_db,
            namespace_id=ns_id,
            repo_id=repo_id,
            mybatis_entries=code_result.mybatis_entries,
            business_terms=code_result.business_terms_candidates,
            business_rules=code_result.business_rules_candidates,
            business_examples=business_examples,
            repo_name=name,
        )
        await ke_db.commit()

    await on_progress(80, "候选写入完成")

    # ── 5. 质量评估 (80-95%) — to_thread 避免阻塞事件循环 ──
    await on_progress(82, "LLM 质量评估...")
    t_eval = time.time()
    all_trained = ddls + jpa_docs
    report.duration_seconds = round(time.time() - start, 2)
    report = await asyncio.to_thread(evaluate_parse_quality, report, all_trained)
    log.info("[%s] 评估完成 耗时 %.1fs score=%d",
             name, time.time() - t_eval, report.completeness_score)

    # ── 6. 存储结果 — 短命 session ──
    await _update_repo_fields(
        repo_id,
        parse_status="parsed",
        error_message="",
        parsed_at=local_now(),
        parse_report=json.dumps(_report_to_dict(report), ensure_ascii=False),
    )

    # ── 6.5 触发 promote ──
    # 必须在 step 6 (parse_status='parsed' commit) 之后:
    #   maybe_trigger_promote 闸门检查 all(r.parse_status=='parsed'), 若早于
    #   step 6 则当前 repo 自身永远拿不到最新状态, 全量重建场景下闸门永不通过.
    # 必须在 step 7 (terminology) 之前:
    #   terminology_refresher._load_canonicals 读 SchemaCanonicalObject, 若 SCO
    #   为空则整 ns 术语抽取直接 skip (reason=no_canonicals).
    # ── 6.4 MySQL introspect → mysql introspect candidate (Stage B B1) ──
    # 收窄到本 repo 引用过的表 (mybatis FROM/JOIN + jpa @Table). 跨 repo 并集
    # 由 candidate value_hash 幂等 + ns 级累积自然形成. 未引用的表永不入 candidate.
    referenced_tables = _collect_referenced_sql_tables(
        code_result.mybatis_entries, code_result.jpa_entities, coll_to_db,
    )
    await on_progress(93, "SQL Schema introspect...")
    async with async_session() as introspect_db:
        try:
            from app.engine.db_types import SQL_DB_TYPES
            from app.knowledge.schema_canonical import refresh_driver_canonicals
            total_ds_count = 0
            for sql_db_type in SQL_DB_TYPES:
                ds_count = await refresh_driver_canonicals(
                    introspect_db, ns_id, ns_slug, db_type=sql_db_type,
                    referenced_targets=referenced_tables,
                    repo_name=name,
                    # 不在此内部 promote: 汇聚交给 step 6.5 maybe_trigger_promote 统一
                    # 处理 (与 MongoDB 候选路径对齐), 避免同一训练内重复全量 promote.
                    trigger_promote=False,
                )
                total_ds_count += ds_count
            await introspect_db.commit()
            log.info("[%s] SQL introspect 完成, 处理表数=%d", name, total_ds_count)
        except Exception as e:
            log.warning(
                "[%s] SQL introspect 失败 (best-effort): %s", name, e,
            )
            await introspect_db.rollback()

    # ── 6.5 触发 promote ──
    await on_progress(95, "汇聚候选...")
    from app.knowledge.canonical_promote import maybe_trigger_promote
    async with async_session() as promo_db:
        await maybe_trigger_promote(promo_db, ns_id)
        await promo_db.commit()

    # ── 6.6 补充索引信息 (indexes_json + field indexed) ──
    from app.knowledge.schema_canonical import (
        backfill_indexes_from_driver,
        cleanup_stale_fk_relationships,
    )
    async with async_session() as idx_db:
        await backfill_indexes_from_driver(idx_db, ns_id)
        await cleanup_stale_fk_relationships(idx_db, ns_id)
        await idx_db.commit()

    # ── 7. 业务术语刷新已从训练管道移除 ──
    # 术语抽取改为用户手动触发 (POST /api/namespaces/{ns_id}/terminology/refresh),
    # 解决 SCO description 冲突未解决时术语质量低的时序问题.

    await on_progress(100, "完成")  # noqa: hardcode

    total = time.time() - start
    log.info("[%s] 训练管道完成 repo_id=%d 总耗时 %.1fs score=%d",
             name, repo_id, total, report.completeness_score)

    return report


# ════════════════════════════════════════════
#  Phase 2 Task 2.1 — 全量解析前置清场
# ════════════════════════════════════════════
# purge_legacy_for_full_rebuild 实现已迁移至 app.knowledge.trainer_purge;
# 文件顶部已 re-export 该名字以保持外部 import 兼容.
