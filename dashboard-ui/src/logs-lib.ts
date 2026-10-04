// Pure helpers for the Logs tab (#268): severity summary, time buckets for the
// sparkline, attribute grouping for the expanded row, and the context window.
import type { LogRec } from "./logs";

export type Severity = "ERROR" | "WARN" | "INFO" | "DEBUG" | "OTHER";

const SEVERITY_OF: Record<string, Severity> = {
  FATAL: "ERROR",
  CRITICAL: "ERROR",
  ERROR: "ERROR",
  WARN: "WARN",
  WARNING: "WARN",
  INFO: "INFO",
  DEBUG: "DEBUG",
  TRACE: "DEBUG",
};

export function severityOf(level: string | null | undefined): Severity {
  return SEVERITY_OF[String(level || "").toUpperCase()] || "OTHER";
}

/** Counts per severity band plus the number of structured events, for the rows shown. */
export function severityCounts(rows: LogRec[]): { total: number; events: number; bySeverity: Record<Severity, number> } {
  const bySeverity: Record<Severity, number> = { ERROR: 0, WARN: 0, INFO: 0, DEBUG: 0, OTHER: 0 };
  let events = 0;
  for (const r of rows) {
    bySeverity[severityOf(r.level)] += 1;
    if (r.event_name) events += 1;
  }
  return { total: rows.length, events, bySeverity };
}

export type Bucket = { startNs: number; total: number; errors: number; warns: number };

/** `n` equal-width time buckets over [startNs, endNs] (or the rows' own span when
 *  not given), newest last, counting rows and the error/warn share of each. */
export function timeBuckets(rows: LogRec[], n: number, startNs?: number, endNs?: number): Bucket[] {
  const times = rows.map((r) => Number(r.time_unix_nano || 0)).filter((t) => t > 0);
  if (!n || n < 1) return [];
  const lo = startNs ?? (times.length ? Math.min(...times) : 0);
  const hi = endNs ?? (times.length ? Math.max(...times) : 0);
  if (!(hi > lo)) {
    const one: Bucket = { startNs: lo, total: 0, errors: 0, warns: 0 };
    for (const r of rows) {
      one.total += 1;
      const s = severityOf(r.level);
      if (s === "ERROR") one.errors += 1;
      else if (s === "WARN") one.warns += 1;
    }
    return [one];
  }
  const width = (hi - lo) / n;
  const out: Bucket[] = Array.from({ length: n }, (_, i) => ({ startNs: Math.round(lo + i * width), total: 0, errors: 0, warns: 0 }));
  for (const r of rows) {
    const t = Number(r.time_unix_nano || 0);
    if (!t) continue;
    let i = Math.floor((t - lo) / width);
    if (i >= n) i = n - 1;
    if (i < 0) i = 0;
    out[i].total += 1;
    const s = severityOf(r.level);
    if (s === "ERROR") out[i].errors += 1;
    else if (s === "WARN") out[i].warns += 1;
  }
  return out;
}

export type AttrGroup = { label: string; entries: [string, any][] };

const GROUP_ORDER = ["event", "hermes", "gen_ai", "exception", "code", "other"] as const;
const GROUP_LABEL: Record<(typeof GROUP_ORDER)[number], string> = {
  event: "Event",
  hermes: "Hermes",
  gen_ai: "GenAI",
  exception: "Exception",
  code: "Code location",
  other: "Other",
};

/** The expanded row's attribute table: grouped by prefix, stable order, the
 *  exception stack trace kept out (it gets its own block). */
export function groupLogAttributes(attrs: Record<string, any> | null | undefined): { groups: AttrGroup[]; stacktrace: string | null } {
  const buckets: Record<string, [string, any][]> = { event: [], hermes: [], gen_ai: [], exception: [], code: [], other: [] };
  let stacktrace: string | null = null;
  for (const [k, v] of Object.entries(attrs || {}).sort(([a], [b]) => a.localeCompare(b))) {
    if (k === "exception.stacktrace") {
      stacktrace = String(v);
      continue;
    }
    if (k.startsWith("hermes.")) buckets.hermes.push([k, v]);
    else if (k.startsWith("gen_ai.")) buckets.gen_ai.push([k, v]);
    else if (k.startsWith("exception.")) buckets.exception.push([k, v]);
    else if (k.startsWith("code.")) buckets.code.push([k, v]);
    else if (k === "event.name" || k === "event_name") buckets.event.push([k, v]);
    else buckets.other.push([k, v]);
  }
  return {
    groups: GROUP_ORDER.filter((g) => buckets[g].length).map((g) => ({ label: GROUP_LABEL[g], entries: buckets[g] })),
    stacktrace,
  };
}

/** `code.file.path:code.line.number` (and the function) when the record carries them. */
export function codeLocation(attrs: Record<string, any> | null | undefined): string | null {
  const a = attrs || {};
  const file = a["code.file.path"] || a["code.filepath"];
  if (!file) return null;
  const line = a["code.line.number"] ?? a["code.lineno"];
  const fn = a["code.function.name"] || a["code.function"];
  return `${String(file).split("/").slice(-2).join("/")}${line != null ? `:${line}` : ""}${fn ? ` (${fn})` : ""}`;
}

/** The ±`windowS` seconds window around a record, as whole-second unix bounds for the API. */
export function contextWindow(timeUnixNano: number, windowS: number): { startS: number; endS: number } {
  const centerS = Math.floor(Number(timeUnixNano || 0) / 1e9);
  return { startS: Math.max(0, centerS - windowS), endS: centerS + windowS + 1 };
}

/** How the record was attributed to its turn, as a short hint for the row. */
export function attributionHint(attrs: Record<string, any> | null | undefined): { text: string; title: string } | null {
  const tier = (attrs || {})["hermes.log.attribution"];
  if (!tier) return null;
  const titles: Record<string, string> = {
    context: "a span was current on the logging thread",
    session_tag: "Hermes's own session tag on the record",
    single_session: "the one session with a turn in flight",
  };
  return { text: String(tier).replace("_", " "), title: titles[String(tier)] || String(tier) };
}
