// Pure helpers for the Settings tab (tested in test/settings.test.ts).
// The shapes mirror hermes_otel/settings_report.py.

export type Source = "env" | "file" | "default";

export type SettingField = {
  key: string;
  group: string;
  kind: "bool" | "int" | "float" | "str" | "map" | "backends";
  description: string;
  env_var: string | null;
  source: Source;
  default: any;
  file_value: any;
  file_invalid: boolean;
  env_raw: string | null;
  env_invalid: boolean;
  value: any;
  changed: boolean;
};

export type EnvEntry = {
  name: string;
  group: string;
  description: string;
  set: boolean;
  value: string | null;
  maps_to: string | null;
};

export type BackendSummary = {
  type: string;
  name: string;
  signals: Record<string, "on" | "off" | "auto">;
  fields: Record<string, any>;
  headers: Record<string, any> | null;
  credentials: { field: string; set: boolean; source: string | null; value?: string }[];
};

export type SettingsReport = {
  resolved_at: number;
  reveal: boolean;
  config: {
    path: string | null;
    path_source: "env" | "durable" | "legacy" | "explicit" | "none";
    exists: boolean;
    parse_ok: boolean;
    mtime: number | null;
    raw: string | null;
    raw_error: string | null;
    durable_path: string;
    legacy_path: string;
    unknown_keys: { key: string; note: string | null }[];
  };
  counts: { env: number; file: number; default: number; changed: number };
  groups: string[];
  fields: SettingField[];
  effective_yaml: string;
  env: EnvEntry[];
  process: {
    role: string;
    pid: number;
    python: string;
    plugin_version: string | null;
    hermes_home: string;
    note: string;
  };
  capture_summary: { mode: "off" | "preview" | "full"; detail: string; conversation_history: boolean; logs: boolean; sender_id: boolean };
};

export const DOCS_BASE = "https://briancaffey.github.io/hermes-otel";

export type DescToken = { t: "text" | "code" | "link"; v: string; href?: string };

/** Split a FIELD_DOCS line into text, `code` and [link](/path) tokens. */
export function renderDescription(desc: string): DescToken[] {
  const out: DescToken[] = [];
  const re = /`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(desc))) {
    if (m.index > last) out.push({ t: "text", v: desc.slice(last, m.index) });
    if (m[1] != null) out.push({ t: "code", v: m[1] });
    else out.push({ t: "link", v: m[2], href: m[3].startsWith("/") ? DOCS_BASE + m[3] : m[3] });
    last = m.index + m[0].length;
  }
  if (last < desc.length) out.push({ t: "text", v: desc.slice(last) });
  return out;
}

/** A one-line, searchable rendering of a field's value. */
export function fmtSettingValue(f: Pick<SettingField, "kind" | "value">): string {
  const v = f.value;
  if (v == null) return "unset";
  if (f.kind === "bool") return v ? "true" : "false";
  if (f.kind === "backends") {
    const n = Array.isArray(v) ? v.length : 0;
    return n === 1 ? "1 backend" : `${n} backends`;
  }
  if (f.kind === "map") {
    return Object.entries(v as Record<string, any>)
      .map(([k, x]) => `${k}=${x}`)
      .join(", ");
  }
  return String(v);
}

export function filterFields(
  fields: SettingField[],
  opts: { query?: string; changedOnly?: boolean }
): SettingField[] {
  const q = (opts.query || "").trim().toLowerCase();
  return fields.filter((f) => {
    if (opts.changedOnly && !f.changed && !f.env_invalid && !f.file_invalid) return false;
    if (!q) return true;
    const hay = [f.key, f.group, f.description, fmtSettingValue(f), f.env_var || "", f.source].join(" ").toLowerCase();
    return q.split(/\s+/).every((w) => hay.includes(w));
  });
}

export function groupFields(fields: SettingField[], groups: string[]): { group: string; fields: SettingField[] }[] {
  const order = groups.length ? groups : Array.from(new Set(fields.map((f) => f.group)));
  return order
    .map((g) => ({ group: g, fields: fields.filter((f) => f.group === g) }))
    .filter((g) => g.fields.length > 0);
}

export function pathSourceLabel(ps: SettingsReport["config"]["path_source"]): string {
  switch (ps) {
    case "env":
      return "HERMES_OTEL_CONFIG";
    case "durable":
      return "$HERMES_HOME/hermes_otel.yaml";
    case "legacy":
      return "plugin directory (legacy location)";
    case "explicit":
      return "explicit path";
    default:
      return "no config file";
  }
}

export const ENV_GROUP_LABELS: Record<string, string> = {
  override: "Setting overrides (HERMES_OTEL_*)",
  plugin: "Plugin",
  hermes: "Hermes",
  backend: "Backends: single-backend mode and credential fallbacks",
  langsmith: "LangSmith",
  otel: "OpenTelemetry SDK",
  other: "Other OTEL_* / HERMES_OTEL_* variables set here",
};
const ENV_GROUP_ORDER = ["override", "plugin", "hermes", "backend", "langsmith", "otel", "other"];

export function groupEnv(env: EnvEntry[], showUnset: boolean): { group: string; label: string; entries: EnvEntry[] }[] {
  const seen = new Set<string>();
  const groups = [...ENV_GROUP_ORDER, ...env.map((e) => e.group).filter((g) => !ENV_GROUP_ORDER.includes(g))];
  return groups
    .filter((g) => (seen.has(g) ? false : (seen.add(g), true)))
    .map((g) => ({
      group: g,
      label: ENV_GROUP_LABELS[g] || g,
      entries: env.filter((e) => e.group === g && (showUnset || e.set)),
    }))
    .filter((g) => g.entries.length > 0);
}

/** "3 set · 41 known" for the Environment view header. */
export function envCounts(env: EnvEntry[]): { set: number; known: number } {
  return { set: env.filter((e) => e.set).length, known: env.length };
}

/** Short explanation of why a value is what it is, shown under the value. */
export function sourceNote(f: SettingField): string | null {
  if (f.env_invalid && f.env_var) return `${f.env_var}=${f.env_raw} is not a valid ${f.kind}; ignored`;
  if (f.file_invalid) return `file value "${f.file_value}" is not a valid ${f.kind}; ignored`;
  if (f.source === "env" && f.file_value != null) return `overrides the file's ${fmtSettingValue({ kind: f.kind, value: f.file_value })}`;
  if (f.source === "env" && f.env_var) return `from ${f.env_var}`;
  return null;
}
