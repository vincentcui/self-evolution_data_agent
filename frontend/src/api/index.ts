/* ════════════════════════════════════════════
 *  API 层 — axios 封装
 *  所有后端交互集中于此, 组件不直接调 axios
 * ════════════════════════════════════════════ */

export * from "./correction";
export * from "./sseClient";

import axios from "axios";
import type {
  DataSource,
  GitRepo,
  KnowledgeEntry,
  KnowledgeEntryCreateResponse,
  LoginResponse,
  Namespace,
  ParseReport,
  QueryHistory,
  QueryResponse,
  ReadinessResult,
  RepoListResponse,
  SchemaRefreshResult,
  Session,
  ShareViewResponse,
  User,
  WorkbenchSummary,
} from "@/types";

export const http = axios.create({ baseURL: "/api", timeout: 60_000 });

// ── 请求拦截: 自动附加 JWT ──
http.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// ── 响应拦截: 401 自动跳转登录 ──
http.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      // 已在登录页则不跳转(避免登录失败时页面刷新导致错误提示消失)
      if (window.location.pathname !== "/login") {
        localStorage.removeItem("token");
        localStorage.removeItem("user");
        window.location.href = "/login";
      }
    }
    return Promise.reject(err);
  },
);

/* ── 命名空间 ── */
export const fetchNamespaces = () =>
  http.get<Namespace[]>("/namespaces").then((r) => r.data);

export const createNamespace = (data: {
  name: string;
  slug: string;
  description?: string;
}) => http.post<Namespace>("/namespaces", data).then((r) => r.data);

export const updateNamespace = (id: number, data: { name?: string; description?: string }) =>
  http.put<Namespace>(`/namespaces/${id}`, data).then((r) => r.data);

export const deleteNamespace = async (id: number) => {
  // 两段式删除: 后端 dry_run 默认 true 仅返回预览不删数据.
  // step1 拿预览 (affected_count + confirm_token), step2 真删.
  const preview = await http
    .delete(`/namespaces/${id}`, { params: { dry_run: true } })
    .then((r) => r.data);
  await http.delete(`/namespaces/${id}`, {
    params: {
      dry_run: false,
      ...(preview?.confirm_required ? { confirm_token: preview.confirm_token } : {}),
    },
  });
};

/* ── 数据源 ── */
export const addDataSource = (nsId: number, data: Record<string, any>) =>
  http.post<DataSource>(`/namespaces/${nsId}/datasources`, data).then((r) => r.data);

export const probeDatasource = (nsId: number, data: Record<string, any>) =>
  http
    .post<{ connected: boolean; detected_timezone: string | null; failure_reason: string | null }>(
      `/namespaces/${nsId}/datasources/probe`,
      data,
    )
    .then((r) => r.data);

export const fetchDataSources = (nsId: number) =>
  http.get<DataSource[]>(`/namespaces/${nsId}/datasources`).then((r) => r.data);

export const refreshSchema = (nsId: number, dsId: number) =>
  http
    .post<SchemaRefreshResult>(`/namespaces/${nsId}/datasources/${dsId}/refresh-schema`)
    .then((r) => r.data);

export const deleteDataSource = (nsId: number, dsId: number) =>
  http.delete(`/namespaces/${nsId}/datasources/${dsId}`);

/* ── Git 仓库 ── */
export const fetchRepos = (nsId: number) =>
  http.get<RepoListResponse>(`/namespaces/${nsId}/repos`).then((r) => r.data);

export const addRepo = (nsId: number, data: { url: string; branch?: string }) =>
  http.post<GitRepo>(`/namespaces/${nsId}/repos`, data).then((r) => r.data);

export const deleteRepo = (nsId: number, repoId: number) =>
  http.delete(`/namespaces/${nsId}/repos/${repoId}`);

export const parseRepo = (nsId: number, repoId: number) =>
  http.post(`/namespaces/${nsId}/repos/${repoId}/parse`).then((r) => r.data);

export const getRepoProgress = (nsId: number, repoId: number) =>
  http.get(`/namespaces/${nsId}/repos/${repoId}/progress`).then((r) => r.data);

export const batchParseRepos = (nsId: number, force = false) =>
  http.post(`/namespaces/${nsId}/repos/batch-parse`, null, { params: force ? { force: true } : {} }).then((r) => r.data);

export const cancelParse = (nsId: number, repoId: number) =>
  http.post(`/namespaces/${nsId}/repos/${repoId}/cancel`).then((r) => r.data);

export const fetchRepoMappings = (nsId: number, repoId: number) =>
  http.get(`/namespaces/${nsId}/repos/${repoId}/mappings`).then((r) => r.data);

export const addRepoMapping = (nsId: number, repoId: number, datasourceId: number) =>
  http.post(`/namespaces/${nsId}/repos/${repoId}/mappings`, { datasource_id: datasourceId }).then((r) => r.data);

export const deleteRepoMapping = (nsId: number, repoId: number, mappingId: number) =>
  http.delete(`/namespaces/${nsId}/repos/${repoId}/mappings/${mappingId}`);

export const getParseReport = (nsId: number, repoId: number) =>
  http.get<ParseReport>(`/namespaces/${nsId}/repos/${repoId}/report`).then((r) => r.data);


/* ── 知识库 ── */
export const fetchKnowledge = (nsId: number) =>
  http.get<KnowledgeEntry[]>(`/namespaces/${nsId}/knowledge`).then((r) => r.data);

export const createKnowledge = (data: {
  entry_type: string;
  content: string;
  namespace_id?: number | null;
  tier?: string;
  payload?: Record<string, unknown> | null;
  raw_input?: string;
  evidence?: Record<string, unknown> | null;
}) => http.post<KnowledgeEntryCreateResponse>("/knowledge", data).then((r) => r.data);

export const deleteKnowledge = (entryId: number) =>
  http.delete(`/knowledge/${entryId}`);

export const getKnowledgeEntry = (entryId: number) =>
  http.get<KnowledgeEntry>(`/knowledge/${entryId}`).then((r) => r.data);

export const patchKnowledge = (
  entryId: number,
  data: { content?: string; tier?: string; description?: string; status?: string },
) => http.patch<KnowledgeEntry>(`/knowledge/${entryId}`, data).then((r) => r.data);

export const supersedeKnowledge = (entryId: number) =>
  http.post<KnowledgeEntry>(`/knowledge/${entryId}/supersede`).then((r) => r.data);

/* ── 查询 ── */
export const sendQuery = (data: {
  namespace_id: number;
  question: string;
  session_id?: string;
}) => http.post<QueryResponse>("/query", data).then((r) => r.data);

/* ── 历史 ── */
export const fetchHistory = (nsId: number) =>
  http.get<QueryHistory[]>(`/namespaces/${nsId}/history`).then((r) => r.data);

/* ── 认证 API ── */
export const login = (data: { username: string; password: string }) =>
  http.post<LoginResponse>("/auth/login", data).then((r) => r.data);

export const changePassword = (data: { old_password: string; new_password: string }) =>
  http.put("/auth/password", data).then((r) => r.data);

/* ── 用户管理 API ── */
export const fetchUsers = () =>
  http.get<User[]>("/users").then((r) => r.data);

export const createUser = (data: { username: string; password: string; role: string }) =>
  http.post<User>("/users", data).then((r) => r.data);

export const updateUser = (id: number, data: { role?: string; is_active?: boolean }) =>
  http.put<User>(`/users/${id}`, data).then((r) => r.data);

export const deleteUser = (id: number) =>
  http.delete(`/users/${id}`);

export const setUserAccess = (id: number, namespace_ids: number[]) =>
  http.put(`/users/${id}/access`, { namespace_ids }).then((r) => r.data);

export const getUserAccess = (id: number) =>
  http.get<Namespace[]>(`/users/${id}/access`).then((r) => r.data);

export const resetUserPassword = (id: number, new_password: string) =>
  http.post(`/users/${id}/reset-password`, { new_password }).then((r) => r.data);

/* ── 分享 ── */
// 独立 axios 实例, 无 JWT — 公开查看接口
const publicHttp = axios.create({ baseURL: "/api", timeout: 60_000 });

export const createShare = (queryHistoryId: number, expiresAt?: string) =>
  http.post("/share", { query_history_id: queryHistoryId, expires_at: expiresAt }).then((r) => r.data);

export const viewShare = (token: string) =>
  publicHttp.get<ShareViewResponse>(`/share/${token}`).then((r) => r.data);

export const deactivateShare = (token: string) =>
  http.delete(`/share/${token}`);

export const listShares = () =>
  http.get("/share").then((r) => r.data);

/* ════════════════════════════════════════════
 *  Stage 3 知识审核闭环 — audit API
 * ════════════════════════════════════════════ */

export interface AuditQueueOut {
  items: KnowledgeEntry[];
  total: number;
  page: number;
  size: number;
}

export interface AuditLogEntry {
  id: number;
  entry_id: number | null;
  actor_id: number | null;
  action: string;
  from_status: string | null;
  to_status: string;
  reason: string;
  diff_json: string;
  created_at: string;
}

export interface ConflictPreviewResult {
  conflicts: Array<{ existing_id: number; reason: string; suggested: string }>;
}

export const fetchAuditQueue = (params: {
  namespace_id?: number;
  entry_type?: string;
  status?: string;
  source?: string;
  q?: string;
  page?: number;
  size?: number;
}) => http.get<AuditQueueOut>("/knowledge/audit/queue", { params }).then((r) => r.data);

export const approveEntry = (
  entryId: number,
  body: { reason?: string; edits?: Record<string, any>; supersede_ids?: number[] } = {},
) =>
  http.post<KnowledgeEntry>(`/knowledge/audit/${entryId}/approve`, body).then((r) => r.data);

export const rejectEntry = (entryId: number, reason: string) =>
  http.post<KnowledgeEntry>(`/knowledge/audit/${entryId}/reject`, { reason }).then((r) => r.data);

export const batchAudit = (
  actions: Array<{
    entry_id: number; action: "approve" | "reject";
    reason?: string; edits?: Record<string, any>; supersede_ids?: number[];
  }>,
  confirmToken?: string,
) =>
  http.post<{ affected_count: number; success_ids: number[] }>(
    "/knowledge/audit/batch",
    { actions, confirm_token: confirmToken },
  ).then((r) => r.data);

export const restoreEntry = (entryId: number, reason: string) =>
  http.post<KnowledgeEntry>(`/knowledge/${entryId}/restore`, { reason }).then((r) => r.data);

export const fetchAuditLog = (entryId: number) =>
  http.get<AuditLogEntry[]>(`/knowledge/audit/${entryId}/log`).then((r) => r.data);

export const editKnowledge = (
  entryId: number,
  body: {
    content?: string;
    tier?: string;
    payload?: Record<string, any>;
    hypothetical_queries?: string[];
    reason: string;
  },
) =>
  http.put<{ entry: KnowledgeEntry; conflicts: any[] }>(
    `/knowledge/${entryId}`, body,
  ).then((r) => r.data);

export const deleteKnowledgeWithMode = (
  entryId: number, mode: "soft" | "hard", reason: string,
) =>
  http.delete(`/knowledge/${entryId}`, { params: { mode, reason } }).then((r) => r.data);

export const previewConflict = (body: {
  namespace_id: number | null;
  entry_type: string;
  content: string;
  entry_id?: number;
}) => http.post<ConflictPreviewResult>("/knowledge/audit/conflict-preview", body).then((r) => r.data);

/* ════════════════════════════════════════════
 *  Phase 3 Task 3.2 — terminology 联动数据源 API
 * ════════════════════════════════════════════ */

export interface NamespaceDatabase {
  database: string;
  db_type: import("@/types").DbType;
  datasource_id: number;
  host: string;
}

export const getDatabases = (nsId: number) =>
  http
    .get<{ databases: NamespaceDatabase[] }>(`/namespaces/${nsId}/databases`)
    .then((r) => r.data);

export const getCollections = (nsId: number, database: string) =>
  http
    .get<{ database: string; db_type: import("@/types").DbType | null; collections: string[] }>(
      `/namespaces/${nsId}/collections`,
      { params: { database } },
    )
    .then((r) => r.data);

export const resolveTerminologyConflict = (
  nsId: number,
  conflictId: number,
  choice: "keep_existing" | "replace" | "merge_both" | "reject_both" | "manual_edit",
  editedPayload?: Record<string, unknown>,
) =>
  http
    .post(`/namespaces/${nsId}/terminology/conflicts/${conflictId}/resolve`, {
      resolution_choice: choice,
      ...(editedPayload !== undefined ? { edited_payload: editedPayload } : {}),
    })
    .then((r) => r.data);

export const listTerminologyConflicts = (nsId: number, status: string = "open") =>
  http
    .get<{ conflicts: import("@/types").TerminologyConflict[] }>(
      `/namespaces/${nsId}/terminology/conflicts`,
      { params: { status } },
    )
    .then((r) => r.data);

/* ════════════════════════════════════════════
 *  Phase 3 Schema Canonical v2 API
 * ════════════════════════════════════════════ */

import type {
  EnumCanonical,
  EvidenceOnlyField,
  EvidenceResponse,
  ExtractionFailure,
  PendingCandidateGroup,
  PendingCounts,
  PendingEnumBinding,
  PromoteReport,
  SchemaAuditLogEntry,
  SchemaCandidate,
  SchemaCanonicalObject,
  SchemaConflict,
} from "@/types/schema-canonical";

export const schemaCanonicalApi = {
  listCanonicals: (nsId: number, params?: { db_type?: string }) =>
    http.get<SchemaCanonicalObject[]>(`/namespaces/${nsId}/schema-canonical`, { params }).then((r) => r.data),

  getPendingCounts: (nsId: number) =>
    http.get<PendingCounts>(`/namespaces/${nsId}/schema-canonical/pending-counts`).then((r) => r.data),

  promote: (nsId: number) =>
    http.post<PromoteReport>(`/namespaces/${nsId}/schema-canonical/promote`).then((r) => r.data),

  listConflicts: (nsId: number, status = "open") =>
    http.get<SchemaConflict[]>(`/namespaces/${nsId}/schema-canonical/conflicts`, { params: { status } }).then((r) => r.data),

  getConflict: (nsId: number, cid: number) =>
    http.get<SchemaConflict>(`/namespaces/${nsId}/schema-canonical/conflicts/${cid}`).then((r) => r.data),

  resolveConflict: (nsId: number, cid: number, body: { resolution_choice: string; resolution_value?: Record<string, unknown>; reason?: string }) =>
    http.post<SchemaConflict>(`/namespaces/${nsId}/schema-canonical/conflicts/${cid}/resolve`, body).then((r) => r.data),

  listCandidates: (nsId: number, scoId: number, params?: { field_path?: string; status?: string }) =>
    http.get<SchemaCandidate[]>(`/namespaces/${nsId}/schema-canonical/${scoId}/candidates`, { params }).then((r) => r.data),

  getEvidence: (nsId: number, scoId: number, field: string) =>
    http.get(`/namespaces/${nsId}/schema-canonical/${scoId}/evidence`, { params: { field } }).then((r) => r.data),

  confirmField: (nsId: number, scoId: number, body: { field_path: string; action: "confirm" | "correct" | "ignore"; corrected_value?: Record<string, unknown>; reason?: string }) =>
    http.post(`/namespaces/${nsId}/schema-canonical/${scoId}/confirm-field`, body).then((r) => r.data),

  lock: (nsId: number, scoId: number, body?: { field_path?: string; reason?: string }) =>
    http.post(`/namespaces/${nsId}/schema-canonical/${scoId}/lock`, body || {}).then((r) => r.data),

  unlock: (nsId: number, scoId: number, body?: { field_path?: string }) =>
    http.post(`/namespaces/${nsId}/schema-canonical/${scoId}/unlock`, body || {}).then((r) => r.data),

  listAuditLog: (nsId: number, params?: { action?: string; limit?: number; cursor?: number }) =>
    http.get<SchemaAuditLogEntry[]>(`/namespaces/${nsId}/schema-canonical/audit-log`, { params }).then((r) => r.data),

  listExtractionFailures: (nsId: number) =>
    http.get<ExtractionFailure[]>(`/namespaces/${nsId}/extraction-failures`).then((r) => r.data),

  retryExtractionFailure: (id: number) =>
    http.post(`/extraction-failures/${id}/retry`).then((r) => r.data),

  ignoreExtractionFailure: (id: number) =>
    http.post(`/extraction-failures/${id}/ignore`).then((r) => r.data),

  listPendingCandidates: (nsId: number) =>
    http.get<PendingCandidateGroup[]>(`/namespaces/${nsId}/schema-canonical/pending-candidates`).then((r) => r.data),

  listEvidenceOnlyFields: (nsId: number) =>
    http.get<EvidenceOnlyField[]>(`/namespaces/${nsId}/schema-canonical/evidence-only`).then((r) => r.data),

  getSchemaEvidence: (nsId: number, scoId: number, field: string) =>
    http.get<EvidenceResponse>(`/namespaces/${nsId}/schema-canonical/${scoId}/evidence`, { params: { field } }).then((r) => r.data),

  listSchemaAuditLog: (nsId: number, params?: { actions?: string[]; since?: string; until?: string; sco_id?: number; field_path?: string }) =>
    http.get<SchemaAuditLogEntry[]>(`/namespaces/${nsId}/schema-canonical/audit-log`, { params }).then((r) => r.data),

  deleteCanonical: (nsId: number, scoId: number) =>
    http.delete(`/namespaces/${nsId}/schema-canonical/${scoId}`).then((r) => r.data),
};

/* ════════════════════════════════════════════
 *  术语手动刷新 API
 * ════════════════════════════════════════════ */

export const terminologyApi = {
  refresh: (nsId: number) =>
    http.post<{ task_id: string; status: string }>(`/namespaces/${nsId}/terminology/refresh`).then((r) => r.data),

  getRefreshProgress: (nsId: number, taskId: string) =>
    http.get<{ status: string; progress: number; message: string; result?: { inserted: number; failed: number } }>(
      `/namespaces/${nsId}/terminology/refresh/${taskId}`,
    ).then((r) => r.data),
};

/* ════════════════════════════════════════════
 *  Phase 2 Enum Knowledge Binding API
 * ════════════════════════════════════════════ */

export interface EnumCandidateBody {
  namespace_id: number;
  db_type: import("@/types").DbType;
  enum_class_name: string;
  values: { name: string; db_value: number | string; description?: string | null }[];
  comment?: string;
}

export interface BindEnumBody {
  enum_dict_id: number;
  force?: boolean;
}

/* ════════════════════════════════════════════
 *  Stage 2 抓手 E — Agent Traces API
 * ════════════════════════════════════════════ */

export const listAgentTraces = (params: { namespace_id?: number; status?: string; page?: number; size?: number }) =>
  http.get("/agent-traces", { params }).then((r) => r.data);

export const getAgentTrace = (traceId: string) =>
  http.get(`/agent-traces/${traceId}`).then((r) => r.data);

export const refineAgentTraces = (trace_ids: string[]) =>
  http.post<{ proposed_count: number; proposed_ke_ids: number[] }>("/agent-traces/refine", { trace_ids }).then((r) => r.data);

export const enumApi = {
  /** POST /api/enum-dictionary — 创建手动枚举 */
  createEnumDictionary: (body: EnumCandidateBody) =>
    http.post<{ id: number; source: string }>("/enum-dictionary", body).then((r) => r.data),

  /** PUT /api/enum-dictionary/{id} — 编辑枚举 */
  updateEnumCanonical: (canonicalId: number, payload: Record<string, unknown>) =>
    http.put(`/enum-dictionary/${canonicalId}`, payload).then((r) => r.data),

  /** DELETE /api/enum-dictionary/{id} — 删除枚举 */
  deleteEnumCanonical: (canonicalId: number, opts: { dryRun?: boolean; confirmToken?: string } = {}) =>
    http.delete(`/enum-dictionary/${canonicalId}`, {
      params: {
        dry_run: opts.dryRun ?? true,
        ...(opts.confirmToken ? { confirm_token: opts.confirmToken } : {}),
      },
    }).then((r) => r.data),

  /** GET /api/enum-dictionary — 列表 */
  listEnumDictionaries: (params: { namespace_id: number; source?: string; name_like?: string }) =>
    http.get<{ items: EnumCanonical[]; total: number }>("/enum-dictionary", { params }).then((r) => r.data),

  /** POST /api/namespaces/{nsId}/schema-canonical/{collectionId}/fields/{fieldName}/bind_enum — 绑定 */
  bindFieldEnum: (nsId: number, collectionId: number, fieldName: string, body: BindEnumBody) =>
    http
      .post(
        `/namespaces/${nsId}/schema-canonical/${collectionId}/fields/${fieldName}/bind_enum`,
        body,
      )
      .then((r) => r.data),

  /** DELETE /api/namespaces/{nsId}/schema-canonical/{collectionId}/fields/{fieldName}/bind_enum — 解绑 */
  unbindFieldEnum: (nsId: number, collectionId: number, fieldName: string) =>
    http
      .delete(
        `/namespaces/${nsId}/schema-canonical/${collectionId}/fields/${fieldName}/bind_enum`,
      )
      .then((r) => r.data),

  /** GET /api/namespaces/{nsId}/schema-canonical/fields/pending_enum_binding — 待绑定列表 */
  listPendingEnumBindings: (namespaceId: number, page = 1, size = 50) =>
    http
      .get<{ items: PendingEnumBinding[]; total: number }>(
        `/namespaces/${namespaceId}/schema-canonical/fields/pending_enum_binding`,
        { params: { page, size } },
      )
      .then((r) => r.data),
};


// ════════════════════════════════════════════
//  Extractor Profile API (agentic-repo-extractor)
// ════════════════════════════════════════════

export interface ProfileOut {
  id: number;
  name: string;
  display_name: string;
  description: string;
  languages: string[];
  hint_text: string;
  is_builtin: boolean;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export const fetchProfiles = () =>
  http.get<ProfileOut[]>("/profiles").then((r) => r.data);

export const createProfile = (data: {
  name: string;
  display_name: string;
  description?: string;
  languages?: string[];
  hint_text?: string;
}) => http.post<ProfileOut>("/profiles", data).then((r) => r.data);

export const updateProfile = (id: number, data: Record<string, any>) =>
  http.patch<ProfileOut>(`/profiles/${id}`, data).then((r) => r.data);

export const deleteProfile = (id: number) =>
  http.delete(`/profiles/${id}`);

export const updateRepoProfile = (nsId: number, repoId: number, profileId: number | null) =>
  http.patch<GitRepo>(`/namespaces/${nsId}/repos/${repoId}`, { profile_id: profileId })
    .then((r) => r.data);

// ════════════════════════════════════════════
//  P0 对话体验 — Session / Readiness / Feedback
// ════════════════════════════════════════════

export const createSession = (namespaceId: number) =>
  http.post<Session>("/sessions", { namespace_id: namespaceId }).then((r) => r.data);

export const listSessions = (namespaceId: number) =>
  http.get<Session[]>("/sessions", { params: { namespace_id: namespaceId } }).then((r) => r.data);

export const renameSession = (id: string, title: string) =>
  http.patch<Session>(`/sessions/${id}`, { title }).then((r) => r.data);

export const deleteSession = (id: string) =>
  http.delete(`/sessions/${id}`);

export const getReadiness = (namespaceId: number) =>
  http.get<ReadinessResult>(`/namespaces/${namespaceId}/readiness`).then((r) => r.data);

export const submitFeedback = (historyId: number, rating: "like" | "dislike") =>
  http.post("/feedback", { history_id: historyId, rating });

/* ── 工作台首页汇总 ── */
export const getWorkbenchSummary = () =>
  http.get<WorkbenchSummary>("/workbench/summary").then((r) => r.data);
