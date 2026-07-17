/**
 * sourceLabels — Schema/Enum 相关 "来源(source)" 字段的中文标签映射
 *
 * 统一集中管理，避免各组件各自维护英文标识符直出的问题。
 */

/** 字段级 enum 绑定来源 (SchemaCanonicalField.enum_source) */
export const ENUM_FIELD_SOURCE_LABELS: Record<string, string> = {
  manual_binding: "手动绑定",
  code_hint: "代码提示",
  code_type: "代码类型",
  code_type_generic: "代码类型(通用)",
  name_heuristic: "名称推断",
};

export const ENUM_FIELD_SOURCE_COLORS: Record<string, string> = {
  manual_binding: "blue",
  code_hint: "green",
  code_type: "green",
  code_type_generic: "cyan",
  name_heuristic: "orange",
};

export function enumFieldSourceLabel(source: string | null | undefined): string {
  if (!source) return "";
  return ENUM_FIELD_SOURCE_LABELS[source] ?? source;
}

/** 枚举字典本身的来源 (EnumCanonical.source: "code" | "manual") */
export const ENUM_DICT_SOURCE_LABELS: Record<string, string> = {
  code: "代码",
  manual: "手动",
};

export function enumDictSourceLabel(source: string | null | undefined): string {
  if (!source) return "";
  return ENUM_DICT_SOURCE_LABELS[source] ?? source;
}
