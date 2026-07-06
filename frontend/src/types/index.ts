/* ════════════════════════════════════════════
 *  全局类型定义 — 与后端 schema 一一对应
 * ════════════════════════════════════════════ */

export interface Namespace {
  id: number;
  name: string;
  slug: string;
  description: string;
  created_at: string;
  created_by: number | null;
}

/** 所有支持的数据库类型 — 全局唯一定义, 组件/API 通过此类型而非散落的 union */
export type DbType = "mysql" | "mongodb" | "oracle";

/**
 * 数据库类型元信息 — 前端唯一 db_type 元数据中心。
 * 新增数据库时: 扩充 DbType union + 加一行 DB_TYPE_META。与 backend DRIVERS 注册表同步维护。
 */
export interface DbTypeMeta {
  short: string;
  label: string;
  color: string;
  isSql: boolean;
  defaultPort: number;
}

export const DB_TYPE_META: Record<DbType, DbTypeMeta> = {
  mysql:   { short: "My", label: "MySQL",   color: "blue",    isSql: true,  defaultPort: 3306 },
  mongodb: { short: "Mg", label: "MongoDB", color: "green",   isSql: false, defaultPort: 27017 },
  oracle:  { short: "Or", label: "Oracle",  color: "red",     isSql: true,  defaultPort: 1521 },
};

export interface DataSource {
  id: number;
  db_type: DbType;
  host: string;
  port: number;
  database: string;
  username: string;
  description: string;
  db_profile: Record<string, unknown>;
  timezone: string;
  created_at: string;
}

export interface GitRepo {
  id: number;
  url: string;
  branch: string;
  parse_status: "pending" | "cloning" | "parsing" | "parsed" | "error";
  error_message: string;
  created_at: string;
  parsed_at: string | null;
  has_report: boolean;
  completeness_score: number;
  worker_id: string;
  progress: number;
  progress_message: string;
  profile_id?: number | null;  // agentic extractor profile (NULL=自动识别)
}

export interface BatchStatus {
  active: boolean;
  progress: string;
  message: string;
}

export interface RepoListResponse {
  repos: GitRepo[];
  batch_status: BatchStatus | null;
}

export interface KnowledgeEntry {
  id: number;
  namespace_id: number | null;
  entry_type: "terminology" | "instance_alias" | "example" | "rule" | "route_hint";
  tier: "critical" | "normal";
  content: string;
  raw_input: string;
  description: string;
  source: "schema" | "manual" | "agent_learn" | "code_extract";
  status: "proposed" | "canonical" | "superseded" | "rejected";
  is_superseded: boolean;
  payload: Record<string, unknown> | null;
  refined_at: string | null;
  created_at: string;
  hypothetical_queries_json?: string;
  related_entry_ids_json?: string;
}

export interface ConflictItemOut {
  existing_id: number;
  reason: string;
  suggested: "merge" | "replace" | "coexist";
}

export interface KnowledgeEntryDraft {
  refined: string;
  description: string;
}

export interface KnowledgeEntryCreateResponse {
  entry: KnowledgeEntry | null;
  conflicts: ConflictItemOut[];
  overflow: boolean;
  split_candidates: KnowledgeEntryDraft[];
}

export interface QueryRequest {
  namespace_id: number;
  question: string;
  session_id?: string;
}

export interface QueryResponse {
  session_id: string;
  history_id: number;
  needs_clarification: boolean;
  clarification_message: string;
  generated_query: string;
  columns: string[];
  rows: any[][];
  row_count: number;
  chart_type: "line" | "bar" | "pie" | "card" | "table";
  category_column?: string;
  value_column?: string;
  chart_option: Record<string, any>;
  performance_warning: string;
  /* §4.6 截断显式 (绝不静默): 渲染源撞 IS_RENDER_ROW_LIMIT 时透传 */
  truncated?: boolean;
  rendered_row_count?: number;
  total_row_count?: number;
  error: string;
  /* Decomposer Routing P1 — 结构化澄清 */
  clarification_questions: ClarifyQuestion[];
  pending_id: number;
}

/* ── 结构化澄清 (Decomposer Routing P1) ── */
export interface ClarifyOption {
  value: string;
  label: string;
  meta: Record<string, any>;
}

export interface ClarifyQuestion {
  cond_id: string;
  prompt: string;
  options: ClarifyOption[];
  mode: "single" | "multi";
  overflow: boolean;
  sample_count: number;
  empty: boolean;
}

export interface QueryContinueRequest {
  pending_id: number;
  selections: Record<string, string[]>;
}

export interface PendingClarification {
  id: number;
  session_id: string;
  namespace_id: number;
  original_question: string;
  status: "pending" | "resolved" | "abandoned" | "overflow";
  clarification_questions: ClarifyQuestion[];
  created_at: string;
  expires_at: string;
}

export interface QueryHistory {
  id: number;
  session_id: string;
  role: string;
  content: string;
  generated_query: string;
  row_count: number;
  error: string;
  result_snapshot: string;
  created_at: string;
}

export interface SchemaRefreshResult {
  success: boolean;
  table_count: number;
  message: string;
}

/* ── 聊天消息 (前端本地状态) ── */
export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  query?: string;
  result?: QueryResponse;
  historyId?: number;  // 关联 QueryHistory.id, 用于分享
  timestamp: number;
  /* Decomposer Routing P1 — 结构化澄清卡片状态 */
  clarificationQuestions?: ClarifyQuestion[];
  pendingId?: number;
  clarifyResolved?: boolean;  // 用户已提交 → 卡片禁用
  clarifyCancelled?: boolean; // 用户取消 → 卡片禁用 (P4)
}

/* ── 解析报告 — Git 仓库解析质量评估 ── */
export interface ParserStats {
  files_scanned: number;
  files_parsed: number;
  files_skipped: number;
  files_errored: number;
  items_extracted: number;
  tables_found: string[];
}

export interface ParseReport {
  repo_id: number;
  duration_seconds: number;
  stats: ParserStats;
  ddls_trained: number;
  docs_trained: number;
  sqls_trained: number;
  completeness_score: number;
  evaluation_summary: string;
}

/* ── 用户认证 ── */
export type Role = "super_admin" | "admin" | "user";

export interface User {
  id: number;
  username: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  created_by: number | null;
}

export interface LoginResponse {
  access_token: string;
  user: User;
}

/* ── 分享 ── */
export interface ShareViewResponse {
  shared_at: string;
  shared_by_name: string;
  result: QueryResponse;
}

/* ════════════════════════════════════════════
 *  Phase 3 Task 3.3 — TerminologyConflict
 * ════════════════════════════════════════════ */
export interface TerminologyConflict {
  id: number;
  namespace_id: number;
  existing_entry_id: number;
  existing_payload?: Record<string, unknown> | null;  // 后端 JSON 解析后注入
  candidate_payload: string; // 原样 JSON 字符串, 前端自行 JSON.parse
  candidate_source: string;
  candidate_repo_id: number | null;
  status: "open" | "resolved" | "dismissed";
  created_at: string;
}

/* ════════════════════════════════════════════
 *  P0 对话体验 — Session / Readiness / Feedback
 * ════════════════════════════════════════════ */

export interface Session {
  id: string;
  namespace_id: number;
  title: string;
  created_by: number;
  created_at: string;
  updated_at: string | null;
}

export interface Blocker {
  type: string;
  message: string;
  admin_action: string;
  admin_route: string;
  user_action: string;
}

export interface ReadinessResult {
  ready: boolean;
  checks: Record<string, boolean>;
  blockers: Blocker[];
}
