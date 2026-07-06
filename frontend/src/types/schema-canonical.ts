/* ════════════════════════════════════════════
 *  Phase 3 Schema Canonical 类型定义
 * ════════════════════════════════════════════ */

export type ConfidenceStatus =
  | "confirmed_by_introspect"
  | "confirmed_by_code"
  | "confirmed_by_user"
  | "evidence_only"
  | "unverified";

export interface SchemaCanonicalEnumValue {
  name: string | null;
  db_value: number | string;
  description: string | null;
}

export interface SchemaCanonicalField {
  name: string;
  type: string;
  description?: string;
  description_confidence?: ConfidenceStatus;
  enum_values?: SchemaCanonicalEnumValue[];
  sub_fields?: SchemaCanonicalField[];
  user_locked?: boolean;
  nullable?: boolean;
  indexed?: boolean;
  /* Enum binding fields (Phase 2) */
  enum_ref_id?: number | null;
  enum_source?:
    | "code_hint"
    | "code_type"
    | "code_type_generic"
    | "name_heuristic"
    | "manual_binding"
    | null;
  enum_match_status?: "matched" | "pending" | "conflict" | null;
  enum_class_hint?: string;
  sample_values?: (number | string)[];
}

export interface SchemaCanonicalRelationship {
  from_target: string;
  from_field: string;
  to_db_type: string;
  to_database: string;
  to_target: string;
  to_field: string;
  relation_type: string;
}

export interface SchemaCanonicalObject {
  id: number;
  target: string;
  database: string;
  db_type: string;
  description: string;
  purpose_detail: string;
  fields: SchemaCanonicalField[];
  indexes: Array<{ name: string; columns?: string[]; unique?: boolean }>;
  relationships: SchemaCanonicalRelationship[];
  user_locked: boolean;
  sample_count: number;
  source: string;
}

export interface PendingCounts {
  pending_promote: number;
  evidence_only: number;
  conflicts: number;
  audit_today: number;
}

export interface SchemaConflict {
  id: number;
  db_type: string;
  database: string;
  target: string;
  field_path: string;
  candidate_kind: string;
  conflict_type: string;
  candidates_snapshot: Array<{
    candidate_id: number;
    value: Record<string, unknown>;
    evidence: unknown[];
    confidence_status: string;
    source?: string;
  }>;
  status: "open" | "resolved";
  resolution_choice: string | null;
  resolved_at: string | null;
  created_at: string;
}

export interface SchemaCandidate {
  id: number;
  field_path: string;
  candidate_kind: string;
  candidate_value: Record<string, unknown>;
  evidence_sources: unknown[];
  status: string;
  confidence_status: ConfidenceStatus;
  repo_id: number | null;
  datasource_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface SchemaAuditLogEntry {
  id: number;
  action: string;
  field_path: string | null;
  candidate_id: number | null;
  conflict_id: number | null;
  canonical_id: number | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  actor_id: number | null;
  extra: Record<string, unknown> | null;
  created_at: string;
}

export interface PromoteReport {
  promoted_count: number;
  conflicted_count: number;
  skipped_user_locked: number;
  skipped_in_conflict: number;
  candidates_processed: number;
  duration_seconds: number;
}

export interface ExtractionFailure {
  id: number;
  extraction_kind: string;
  failure_type: string;
  source_file: string | null;
  source_mapper: string | null;
  source_method: string | null;
  source_content: string | null;
  failure_message: string;
  retry_count: number;
  last_seen_at: string;
  created_at: string;
  failure_extra?: Record<string, unknown>;
}

/* ── PendingPromoteTab types ── */

export interface CandidateValue {
  description?: string;
  enum_values?: SchemaCanonicalEnumValue[];
}

export interface PendingCandidateGroup {
  id: number;
  target: string;
  field_path: string;
  candidate_kind: string;
  candidates: Array<{
    id: number;
    source: string;
    value: CandidateValue;
  }>;
}

/* ── EvidenceOnlyTab types ── */

export interface EvidenceOnlyField {
  sco_id: number;
  target: string;
  field_path: string;
  current_value: CandidateValue;
  evidence_summary: string;
}

export interface EvidenceResponse {
  field_path: string;
  candidates: Array<{
    id: number;
    candidate_value: CandidateValue;
    evidence_sources: Array<{
      source: string;
      file?: string;
      line?: number;
      repo_url?: string;
      extra?: Record<string, unknown>;
    }>;
    confidence_status: ConfidenceStatus;
    status: string;
  }>;
  canonical_value: CandidateValue | null;
}

/* ════════════════════════════════════════════
 *  Phase 2 Enum Knowledge Binding 类型
 * ════════════════════════════════════════════ */

export interface EnumValueItem {
  name: string;
  db_value: number | string;
  description?: string | null;
}

export interface EnumCanonical {
  id: number;
  enum_class_name: string;
  values: EnumValueItem[];
  source: "code" | "manual";
  status: "canonical" | "proposed" | "superseded";
  reference_count?: number;
}

export interface FieldEnumBinding {
  enum_ref_id: number | null;
  enum_source:
    | "code_hint"
    | "code_type"
    | "code_type_generic"
    | "name_heuristic"
    | "manual_binding"
    | null;
  enum_match_status: "matched" | "pending" | "conflict" | null;
  enum_class_hint?: string;
}

export interface PendingEnumBinding {
  collection_id: number;
  collection_name: string;
  field: string;
  // 后端 schema_canonical_v2.py:570-575 实际返回字段, 不要再叫 hint/samples,
  // 名字必须与 backend response key 严格一致 (api-contract-testing skill Layer 1).
  field_type: string | null;
  enum_class_hint?: string | null;
  sample_values?: (number | string)[] | null;
}
