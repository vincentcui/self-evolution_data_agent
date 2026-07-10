"""Tool registry + system prompt template — 10 多态工具 (Stage 3 升级).

10 个 agent tool 的统一注册表 (name → callable) 和 LLM 输入 schema 描述.

设计约束:
- input_schema 只暴露 LLM-visible 字段, 不含 runtime context (db / namespace_id /
  ns_slug / trace_id / sse_emit) — 这些由 agent_loop dispatcher 注入.
- TOOL_SPECS 的 name 必须严格对齐 @observe(name="tool.<name>") 字符串.
- 数据访问类工具必传 (db_type, database, target) 三件套.
"""
from __future__ import annotations

from collections.abc import Callable

from app.engine.tools.catalog_tools import list_databases, list_tables
from app.engine.tools.data_access_tools import (
    estimate_cost,
    execute_query,
    fetch_schema,
    inspect_values,
)
from app.engine.tools.interaction_tools import clarify_with_user
from app.engine.tools.knowledge_tools import lookup_knowledge, save_knowledge
from app.engine.tools.plan_tools import (
    execute_plan_tool,
    generate_query_plan,
    present_result_tool,
)
from app.knowledge.prompt_loader import load_prompt

_PRESENT_RESULT_DESC = load_prompt("present_result").body
_HUMANIZE_HINT = load_prompt("humanize_query_gen").body

# ════════════════════════════════════════════
#  数据型 / 图表型 tool 集中常量
#  api/query.py final_answer 反扫 tool_trace 与 _write_query_history 共用
# ════════════════════════════════════════════
EXEC_TOOLS: tuple[str, ...] = (
    "execute_query",
    "execute_plan",
)
"""产生最终结果数据的 tool — final_answer 反扫提取 rows/columns/count 等."""

CHART_TOOLS: tuple[str, ...] = ("present_result",)
"""声明最终结果集 + 图表列角色的 tool — finalization 据此 ref 渲染."""


# ════════════════════════════════════════════
#  REGISTRY — name → callable (LLM tool name 对齐)
# ════════════════════════════════════════════

REGISTRY: dict[str, Callable] = {
    # 知识层
    "lookup_knowledge": lookup_knowledge,
    "save_knowledge": save_knowledge,
    # 数据访问 (多态, 支持 MySQL + MongoDB)
    "fetch_schema": fetch_schema,
    "inspect_values": inspect_values,
    "estimate_cost": estimate_cost,
    "execute_query": execute_query,
    # 用户交互
    "clarify_with_user": clarify_with_user,
    # Plan 生成 / 执行 / 图表
    "generate_query_plan": generate_query_plan,
    "execute_plan": execute_plan_tool,
    "present_result": present_result_tool,
    # 库表目录 (纯 PG 读, 冷启动打底)
    "list_databases": list_databases,
    "list_tables": list_tables,
}


# ════════════════════════════════════════════
#  工具 input 字段映射 — 抽取器消费方真相源
# ════════════════════════════════════════════
# 历史教训 (2026-05-14): extractor-protocol stage Task 2 写的 helper 在
# api/query.py 把工具名/字段名硬编码 (fetch_collection_schema / input.collection),
# 但 stage 3 多态化把工具改名为 fetch_schema / inspect_values / execute_query,
# 字段从 collection 改 target. 抽取器看不到 stage 3 工具 → 知识沉淀失效.
# 本常量集中维护工具→字段映射, 工具改名时仅改这里一处.

# 数据访问工具的 collection-target 字段名 (空字符串 = 该工具不持有 target).
TOOL_TARGET_FIELD: dict[str, str] = {
    # 4 件套数据访问工具 (stage 3 多态)
    "fetch_schema":      "target",
    "inspect_values":    "target",
    "estimate_cost":     "target",
    "execute_query":     "target",
    # 多步执行 (走 plan.steps[].collection, 不直接持有 target)
    "execute_plan":        "",
    "generate_query_plan": "",
    # 非数据工具
    "present_result":    "",
    "lookup_knowledge":  "",
    "save_knowledge":    "",
    "clarify_with_user": "",
    # 库表目录 (持有 database, 不持有 target)
    "list_databases":    "",
    "list_tables":       "",
}


# ════════════════════════════════════════════
#  TOOL_SPECS — Anthropic tool schema
#  仅暴露 LLM-visible 字段, 不含 runtime context kwargs
# ════════════════════════════════════════════

_LOOKUP_ENTRY_TYPES = ["terminology", "instance_alias", "example", "rule", "route_hint"]
_SAVE_ENTRY_TYPES = ["terminology", "instance_alias", "example", "rule"]


TOOL_SPECS: list[dict] = [
    # ── 知识层 ──
    {
        "name": "lookup_knowledge",
        "description": (
            "检索知识库的可复用查询模式、业务术语与导航知识. "
            "Use when: 多步关联查询前找可复用查询模板; "
            "问题里的业务名词你不确定对应哪个库/表/字段时, "
            "查术语 (types=[\"terminology\"]) 拿它的库/表路由. "
            "Do not use when: fetch_schema 已拿到足够字段, "
            "或前一次同 query 召回为空且判断知识库确实无相关条目; "
            "召回为空属正常, 直接基于 fetch_schema 继续. "
            "types 按需选: "
            "terminology(业务术语→库/表路由) / instance_alias(口语别名→具体记录ID) / "
            "example(问题-查询成功对, 含可执行 final_query_plan) / "
            "route_hint(跨集合导航路径) / "
            "rule(业务约束或关联模式); 不传 types 则全类型混合检索. "
            "返回: 每条含 content/entry_type/distance/payload. 各类型 payload 字段: "
            "terminology {term:业务名词, target:所属表/集合, "
            "database:所属库, db_type:数据库类型, synonyms:[同义词]}; "
            "instance_alias {alias:口语别名, "
            "target_collection:目标表/集合, target_database:目标库, "
            "target_id:目标记录ID, id_field:ID字段名(_id默认)}; "
            "example {question_pattern:语义骨架, collections:[表名/集合名], "
            "join_keys:[{from:源表.字段, to:目标表.字段}], "
            "final_query_plan:{steps:[{db_type:数据库类型, database:库名, "
            "collection:表名/集合名, operation:sql|aggregate|filter, "
            "query:{sql:SQL串 或 pipeline:[聚合阶段]}}]}, "
            "result_summary?:结果描述}; "
            "route_hint {collection_path:[有序集合路径], "
            "navigation_note:导航说明(关联字段/关联类型/嵌套位置/避坑)}; "
            "rule {rule_text:规则描述, "
            "rule_kind:business_constraint|filter_default|join_pattern, "
            "priority:优先级(默认0)}. "
            "部分条目另含 references"
            "(条目文本提及的业务术语自动解析出的库/表路由, "
            "直接用, 免去对提及术语二次查 terminology): "
            "[{term:业务术语, target:所属表/集合, database:所属库, db_type:数据库类型}]. "
            "输入示例: {\"query\": \"订单关联用户\", \"types\": [\"example\"], \"k\": 5}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "中文检索语"},
                "types": {
                    "type": "array",
                    "items": {"enum": _LOOKUP_ENTRY_TYPES},
                    "description": "限定 entry_type 子集",
                },
                "k": {"type": "integer", "description": "Top-K 数量"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "save_knowledge",
        "description": (
            "把本轮会话学到的知识写入知识库待审池. "
            "Use when: clarify 获得用户确认后沉淀可复用知识. "
            "Do not use when: 信息仅对当前查询有效, 或该业务名词的库/表路由已在已提供给你的术语中. "
            "payload 按 entry_type 不同:\n"
            "- terminology: {term, primary_collection, primary_database, "
            "db_type: mysql|mongodb|oracle, synonyms?:[], source_collections?:[]}\n"
            "- instance_alias: {alias, target_collection, target_database, "
            "target_id, id_field?:'_id', canonical_name?}\n"
            "- example: {question_pattern:语义骨架, "
            "collections:[表名/集合名], "
            "join_keys:[{from:源表.字段, "
            "to:目标表.字段}], "
            "final_query_plan:{steps:[{db_type:数据库类型, database, "
            "collection:表名/集合名, operation:sql|aggregate|filter, "
            "query:{sql:SQL串 或 pipeline:[Mongo聚合阶段]}}]}, "
            "result_summary?:一句话结果形态}\n"
            "- rule: {rule_text, applies_to_collections?:[]}\n"
            "输入示例: {\"entry_type\":\"example\", \"content\":\"按状态分组统计订单数\", "
            "\"payload\":{question_pattern:.., collections:[..], join_keys:[..], final_query_plan:{..}, result_summary:..}, "
            "\"evidence\":{\"trace_ids\":[\"t1\"],\"reasoning\":\"从本次查询 trace 提取\"}, "
            "\"tier\":\"normal\"}\n"
            "返回 {entry_id, status} 或 {success:false, reason}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_type": {"enum": _SAVE_ENTRY_TYPES},
                "content": {"type": "string"},
                "payload": {"type": "object"},
                "evidence": {"type": "object"},
                "tier": {"enum": ["normal", "critical"]},
            },
            "required": ["entry_type", "content", "payload", "evidence"],
        },
    },
    # ── 数据访问 (多态) ──
    {
        "name": "fetch_schema",
        "description": (
            "获取指定数据源某表/集合的 schema (字段/类型/索引/关系). "
            "返回 {target, description, fields, indexes, relationships, "
            "source, timezone, db_profile}. "
            "timezone 是数据源数据的 IANA 时区名 (如 Asia/Shanghai); "
            "查日期范围 (如'X年X月') 时按此时区算 [月初00:00, 下月初00:00) 边界, "
            "生成带时区偏移的日期字面量 (document 系用 Extended JSON "
            "{\"$date\":\"2026-06-01T00:00:00+08:00\"}, "
            "relational 系用 SQL 日期字面量), 勿用 UTC 零点. "
            "db_profile 含 version/charset(字符串字段编码)/flavor(mongo/documentdb 操作差异) + 能力限制 (unsupported_ops/unsupported_stage_variants/syntax_constraints/equivalent_hints). "
            "Use when: 已知目标库名, 需字段结构/索引/关系/时区/库画像. "
            "Do not use when: 仅需库名列表 (用 list_databases). "
            "Input example: {\"db_type\":\"mysql\",\"database\":\"shop_db\",\"target\":\"orders\"}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "db_type": {"type": "string", "description": "从锚点 [...] 部分读"},
                "database": {"type": "string"},
                "target": {"type": "string", "description": "表名/集合名"},
            },
            "required": ["db_type", "database", "target"],
        },
    },
    {
        "name": "inspect_values",
        "description": (
            "探目标字段的 distinct 值分布 (默认 top 10). "
            "Use when: 需要判断字段形态(枚举/ID格式/数值区间). "
            "Do not use when: fetch_schema 已列明枚举. "
            "返回 {values: [...]} 的 top-N 频次列表."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "db_type": {"type": "string", "description": "从锚点读"},
                "database": {"type": "string"},
                "target": {"type": "string"},
                "field": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["db_type", "database", "target", "field"],
        },
    },
    {
        "name": "estimate_cost",
        "description": (
            "预估查询扫描行数与风险等级. "
            "Use when: 大表查询前自评. "
            "Do not use when: 目标明显是小表. "
            "返回 {estimated_rows, warning_level: ok|high|blocked, timezone, db_profile}. "
            "timezone 是数据源 IANA 时区名 (同 fetch_schema). "
            "db_profile 含 version/charset/flavor + 能力限制 (unsupported_ops/unsupported_stage_variants/syntax_constraints/equivalent_hints); "
            "构造 aggregate pipeline 前三类限制全比对, 命中按 equivalent_hints 改用等效写法. "
            "Input example: {\"db_type\":\"mysql\",\"database\":\"shop_db\",\"target\":\"orders\",\"query\":{\"sql\":\"SELECT * FROM orders WHERE id=1\"}}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "db_type": {"type": "string", "description": "从锚点读"},
                "database": {"type": "string"},
                "target": {"type": "string"},
                "query": {
                    "type": "object",
                    "description": (
                        "查询载荷. MySQL/Oracle: {sql:'SELECT...'}. "
                        "Oracle 用 Oracle SQL 方言, 不支持 LIMIT. "
                        "MongoDB: {pipeline:[...]} 或 {filter:{...}}"
                    ),
                },
            },
            "required": ["db_type", "database", "target", "query"],
        },
    },
    {
        "name": "execute_query",
        "description": (
            "执行查询, 按 mode 控制粒度. "
            "Use when: 已拿到 schema 和过滤条件, 准备实际取数. "
            "Do not use when: 跨 db_type 或跨 database (用 generate_query_plan). "
            "mode: count=只求总数, 驱动包装返标量 (勿自行聚合); "
            "probe=小样本探查; single=完整结果; batched=分批. "
            "query 形态: MySQL/Oracle 用 {sql:'...'} (Oracle 用 Oracle SQL 方言, 不写 LIMIT), "
            "MongoDB 用 {pipeline:[...]}. "
            "返回 {rows, row_count, truncated, elapsed_ms, result_ref}. "
            "result_ref 是本次执行的稳定句柄, 后续 present_result.ref 直接复制它的值."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "db_type": {"type": "string", "description": "从锚点读"},
                "database": {"type": "string"},
                "target": {"type": "string"},
                "query": {
                    "type": "object",
                    "description": (
                        "MySQL: {sql:'SELECT...'}, "
                        "MongoDB: {pipeline:[...]} 或 {filter:{...}}"
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["single", "probe", "count", "batched"],
                    "default": "single",
                },
                "batch_size": {"type": "integer", "default": 1000},
            },
            "required": ["db_type", "database", "target", "query"],
        },
    },
    # ── 库表目录 (纯 PG 读, 冷启动打底) ──
    {
        "name": "list_databases",
        "description": (
            "列出当前所有已配置的数据源 (db_type / 数据库名 / 用户填写的用途描述 / "
            "库级画像 version/charset/flavor/object_count / 数据源时区 timezone). "
            "Use when: 不清楚有哪些库可查, 或已有知识中无匹配的库, 或需确认数据源时区. "
            "Do not use when: 已有知识 (术语/schema/规则) 足以确定目标库名. "
            "返回 {databases: [{db_type, database, description, timezone, db_profile}], count}. "
            "timezone 是数据源数据的 IANA 时区名 (如 Asia/Shanghai); "
            "查日期范围 (如'X年X月') 时按此 timezone 算边界偏移, 生成带偏移字面量, 勿默认 UTC. "
            "Input example: {} (无入参, namespace 由上下文绑定)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_tables",
        "description": (
            "列出指定数据库下已提取的所有表/集合的名称和用途描述 (只列表名, 不返回字段明细). "
            "入参 database 为库名 (来自 list_databases). "
            "Use when: 知道库名但不确定有哪些表. "
            "Do not use when: 需要表的字段明细 (用 fetch_schema); 或已确定目标表. "
            "返回 {database, tables: [{target, description, field_count, reviewed}], count}. "
            "若 tables 为空, 阅读 status 与 hint 字段判断下一步: "
            "status=no_schema_extracted 表示该库未提取 schema; "
            "status=unknown_database 表示库名不在数据源列表 (回 list_databases 对照). "
            'Input example: {"database": "my_orders_db"}.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "database": {"type": "string", "description": "库名, 来自 list_databases"},
            },
            "required": ["database"],
        },
    },
    # ── 用户交互 ──
    {
        "name": "clarify_with_user",
        "description": (
            "向用户提澄清问题, 阻塞等待回答或超时. "
            "Use when: 多候选无法自决, 或用户需求有歧义. "
            "Do not use when: 信息可通过再调一次工具得到. "
            "返回 {user_answer, timed_out}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["question", "options", "reason"],
        },
    },
    # ── Plan 编排 ──
    {
        "name": "generate_query_plan",
        "description": (
            "把已备齐的上下文交给规划器, 生成可串行执行的跨引擎多步查询 Plan. "
            "Use when: 已确认涉及的 库.集合 及其 schema, 且需要多步或跨库/跨引擎(MySQL↔MongoDB)编排. "
            "Do not use when: 单集合单步可直接 execute_query, 或字段/集合仍需探查. "
            "Input example: collections=[{\"db_type\":\"mysql\",\"database\":\"shop_db\",\"collection\":\"orders\"},"
            "{\"db_type\":\"mongodb\",\"database\":\"catalog_db\",\"collection\":\"products\"}], "
            "filters=[{\"collection\":\"orders\",\"field\":\"status\",\"op\":\"eq\",\"value\":1}], "
            "schemas={\"orders\":{\"fields\":[\"id\",\"product_id\",\"status\"]}}. "
            "Output: {plan:{strategy, steps:[{step_idx, db_type, database, collection, operation, ...}]}} — 交 execute_plan 执行."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户原始中文问题"},
                "collections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "db_type": {"enum": ["mysql", "mongodb", "oracle"]},
                            "database": {"type": "string"},
                            "collection": {"type": "string", "description": "集合名(Mongo)或表名(MySQL)"},
                        },
                        "required": ["db_type", "database", "collection"],
                    },
                    "description": "Plan 涉及的全部 库.集合, 每步执行需要 db_type + database",
                },
                "filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "collection": {"type": "string"},
                            "field": {"type": "string"},
                            "op": {"type": "string", "description": "如 eq / regex / gt / in"},
                            "value": {},
                        },
                    },
                    "description": "可选过滤提示, 规划器结合 rules/schema 收敛为最终查询条件",
                },
                "schemas": {
                    "type": "object",
                    "description": "各集合/表 schema, key=集合名或表名, value=字段定义",
                },
                "knowledge": {"type": "array", "items": {"type": "string"}},
                "rules": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question", "collections", "schemas"],
        },
    },
    {
        "name": "execute_plan",
        "description": (
            "串行执行 multi-step Plan, 返最终行+列名+步骤 trace. "
            "Use when: generate_query_plan 已产出合法 plan. "
            "Do not use when: plan 尚未成形. "
            "返回 {rows, columns, last_step_idx, truncated, total_row_count, result_ref}. "
            "result_ref 是本次执行的稳定句柄, 后续 present_result.ref 直接复制它的值."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": {"type": "object", "description": "QueryPlan dict"},
            },
            "required": ["plan"],
        },
    },
    {
        "name": "present_result",
        "description": _PRESENT_RESULT_DESC,
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string",
                        "description": "目标执行的 tool_call_id (execute_query/execute_plan 的成功调用)."},
                "chart_spec": {
                    "type": "object",
                    "properties": {
                        "chart_type": {"type": "string",
                                       "enum": ["card", "line", "pie", "bar", "table"]},
                        "x": {"type": "string", "description": "横轴/分类轴列名."},
                        "series_by": {"type": "string",
                                      "description": "可选: 按此列分多系列, 留空=单系列."},
                        "value": {"type": "string", "description": "数值列名."},
                        "code_label_map": {"type": "object",
                                           "description": "可选: {列名:{编码:可读名}} 兜底翻译."},
                    },
                    "required": ["chart_type"],
                },
            },
            "required": ["ref", "chart_spec"],
        },
    },
]


# ════════════════════════════════════════════
#  System Prompt — config 值在构建时注入
# ════════════════════════════════════════════

SYSTEM_PROMPT_TEMPLATE = """你是数据分析助手 (NL2Query). 给定用户的中文问题, \
一步步把它翻成可执行的数据查询并返回结果.

[业务术语锚点] 的 `[db_type]` 前缀决定走哪个驱动; \
所有数据访问类工具都按锚点给出的 (db_type, database, target) 三件套执行.

# 目录探索 (知识不足时的自主探索)

当已有知识 (术语/schema/规则) 不足以确定应查哪个库或哪些表时, 使用目录工具自主探索:

- list_databases: 列出当前所有已配置的数据源 (db_type / 库名 / 用途描述 / 库级画像). 当你不清楚有哪些库可查时调用.
- list_tables(database): 列出指定库下已提取的表名和用途描述. 当你知道库名但不确定有哪些表时调用.

探索策略:
1. 按各数据源的用途描述 (description) 做语义路由: 判断哪个库与用户问题最相关, 不盲目遍历所有库.
   例: 问题关于"订单金额", 选用途含订单/交易语义的库.
2. list_tables 返回空 (status=no_schema_extracted) 时:
   - 若还有其他语义相关的数据源, 对其继续 list_tables.
   - 若所有相关库均无表, 告知用户: 对该数据源执行「刷新schema」或解析 Git 仓库后可显著提升查询准确性.
3. 所有数据源均无 description (无法语义路由) 时, 不要臆测选库 — 直接 clarify_with_user 询问用户应查哪个库.

# 工作流骨架

1. **读上下文**: 先看 [业务术语锚点] — 用户问题里的已知业务实体已预注入, \
格式 `术语 → [db_type] database.target`.
2. **补缺知识**: 锚点未覆盖的业务名词/别名 → lookup_knowledge. \
types 按需选择: instance_alias(别名→记录ID) / example(历史成功对) / route_hint(关联路径) / rule(查询规则). \
不传 types 则全类型混合检索.
3. **确认 schema**: 不熟悉字段名时调 fetch_schema. \
字段值不明调 inspect_values.
4. **验证候选规模**: 模糊指代 → execute_query(mode="probe") 小样本验证. \
0 命中或异常多 → 自决重试/改字段/clarify_with_user.
5. **歧义澄清**: 多候选无法自决 → clarify_with_user. \
用户回答后 save_knowledge 沉淀.
6. **代价评估**: 大表查询前先 estimate_cost. \
只要行数用 execute_query(mode="count") — 照常写取数 query, 驱动包装返总数. \
单步聚合若结果会超行上限 → 不要用更窄条件分多次 single 拼接, \
改走 generate_query_plan → execute_plan (产出单一完整结果集).
7. **执行**: 单源单步 → execute_query(mode="single"); \
多步或跨源 → generate_query_plan → execute_plan. \
拿到最终结果后调 present_result(ref=该次执行的 tool_call_id, chart_spec={{...}}) 收尾 — \
不要把数据行复制进入参, 渲染器会用 ref 取服务端完整结果. \
chart_spec.chart_type 选型: card=单值/指标卡; line=随时间或有序维度的趋势; \
pie=少数分类占比; bar=分类对比; table=多维或无法用上述表达. \
对比多个对象 (如多地区/多类别) 时用 series_by 指定分组列, 渲染出多条系列.
8. **能力兼容**: 构造 aggregate pipeline 前, 比对 fetch_schema / estimate_cost 返回的 \
db_profile 里的能力限制 (unsupported_ops / unsupported_stage_variants / syntax_constraints). \
命中任一项时按 equivalent_hints 改用等效写法; 无 hint 时改用不命中该限制的等价表达.

# 数据源协议

数据访问类工具 (fetch_schema/inspect_values/estimate_cost/execute_query) \
必传 (db_type, database, target) 三件套:
- db_type 从锚点 `[...]` 读, 不要猜
- query 字段形态由 db_type 决定: \
MySQL 用 {{sql: "SELECT ... LIMIT n"}}, \
Oracle 用 {{sql: "SELECT ..."}} (Oracle SQL 方言, 不支持 LIMIT; \
行数保护用 FETCH FIRST n ROWS ONLY 或不写, 执行层自动包装), \
MongoDB 用 {{pipeline: [...]}} 或 {{filter: {{...}}}}
- 结果中的 DBRef 字段呈现为 {{$ref: 目标集合名, $id_str: 目标记录ID字符串}}; \
需关联时取 $id_str 当普通字符串匹配目标集合的关联字段.

# 代价控制铁律

- 关联超 2 层: 先 estimate_cost 看每层规模.
- 任一层估算 > {single_layer_limit:,} 行 → execute_query(mode="batched") 分批.
- 三层连乘估算 > {total_limit:,} 行 → clarify_with_user 询问分组策略.
- 用户只问 "个数/占比" → execute_query(mode="count"), 照常写取数 query 驱动返标量总数; \
要每组明细数量走 mode="single" 自行分组.

# 错误消费与循环规避

工具报错是修复指引。处理步骤:
- 先读错误信息: 逐字读, 它常直接指出哪个字段或哪个语法有问题, 比你猜的准确.
- 再判类别: 这轮报错是"当前思路下某步写法不对"还是"当前思路本身走不通"? — 不要默认属于其一.
- 同类场景不同写法: 方向对但写法有误 → 只改出错的那一步, 不整条换路.
- 同类场景已试数种写法均失败: 大概率不是写法问题, 换策略或 clarify_with_user.
- 避免无效重试: 连续同类错误时列已试写法, 不重复已失败的; 同一 tool 同样参数连调 — 立即停.

# 证据不足时

宁可 clarify_with_user 或返回部分结果, 不要编造字段名/表名/集合名/枚举值.

# 展示列可读化

{humanize_hint}

{critical_section}

{anchors_section}

{reflection_section}
"""


def build_system_prompt(
    *,
    settings,
    namespace,
    anchors: list | None = None,
    critical: list | None = None,
) -> str:
    """注入 config 阈值 + 知识段渲染 system prompt."""
    _ = namespace
    critical_section = ""
    if critical:
        lines = ["## 关键规则 (critical)"]
        lines.extend(f"- {c}" for c in critical)
        critical_section = "\n".join(lines)

    anchors_section = ""
    if anchors:
        lines = ["## 业务术语锚点 (terminology)"]
        for a in anchors:
            syn = f" (同义: {', '.join(a.synonyms)})" if a.synonyms else ""
            lines.append(
                f"- {a.term}{syn} → [{a.db_type}] "
                f"{a.database}.{a.target}"
            )
        anchors_section = "\n".join(lines)

    # ── Stage 2 抓手 C: Self-RAG reflection — 走模板变量, 与 critical/anchors
    #    同模式, 避免再被拼接到末尾时与 SYSTEM_PROMPT_TEMPLATE 内的工作流编号 (例如 8.) 撞号.
    reflection_section = ""
    if settings.agent_reflection_enabled:
        reflection_section = (
            "## 反思日志 (reflection)\n"
            "\n"
            "每次工具调用前在 text 块内输出反思, 用结构化锚点 `[REFLECTION]...[/REFLECTION]` "
            "包裹 (没有锚点的内容会被跳过):\n"
            "\n"
            "[REFLECTION]\n"
            "confidence: 0.8\n"
            "reason: 锚点未覆盖 VIP, 调 lookup_knowledge\n"
            "alternative: 直接 fetch_schema 但术语不明会浪费查询\n"
            "[/REFLECTION]\n"
            "\n"
            "字段说明:\n"
            "- confidence: 0.0-1.0, 你对此次决策的信心\n"
            "- reason: ≤30 字, 为什么调这个 tool\n"
            "- alternative: 你考虑过但放弃的备选 tool, 留空表示无\n"
            "\n"
            "这段不影响业务, 仅供运维 dashboard 统计 (你不会读到自己的过往 reflection)."
        )

    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        single_layer_limit=settings.query_cost_single_layer_limit,
        total_limit=settings.query_cost_total_limit,
        humanize_hint=_HUMANIZE_HINT,
        critical_section=critical_section,
        anchors_section=anchors_section,
        reflection_section=reflection_section,
    )

    return prompt
