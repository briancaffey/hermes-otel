// Settings tab helpers: description tokens, filtering, grouping, env grouping.
import { describe, expect, it } from "vitest";
import {
  DOCS_BASE,
  SettingField,
  EnvEntry,
  envCounts,
  filterFields,
  fmtSettingValue,
  groupEnv,
  groupFields,
  pathSourceLabel,
  renderDescription,
  sourceNote,
} from "../src/settings-lib";

const field = (over: Partial<SettingField>): SettingField => ({
  key: "k",
  group: "General",
  kind: "bool",
  description: "d",
  env_var: "HERMES_OTEL_K",
  source: "default",
  default: false,
  file_value: null,
  file_invalid: false,
  env_raw: null,
  env_invalid: false,
  value: false,
  changed: false,
  ...over,
});

describe("renderDescription", () => {
  it("splits code and links out of the text", () => {
    expect(renderDescription("Attach a handler; see [OTel logs](/configuration/logs) and `capture_previews`")).toEqual([
      { t: "text", v: "Attach a handler; see " },
      { t: "link", v: "OTel logs", href: `${DOCS_BASE}/configuration/logs` },
      { t: "text", v: " and " },
      { t: "code", v: "capture_previews" },
    ]);
  });
  it("passes plain text through", () => {
    expect(renderDescription("plain")).toEqual([{ t: "text", v: "plain" }]);
    expect(renderDescription("")).toEqual([]);
  });
});

describe("fmtSettingValue", () => {
  it("formats each kind", () => {
    expect(fmtSettingValue({ kind: "bool", value: true })).toBe("true");
    expect(fmtSettingValue({ kind: "int", value: null })).toBe("unset");
    expect(fmtSettingValue({ kind: "str", value: "INFO" })).toBe("INFO");
    expect(fmtSettingValue({ kind: "map", value: { a: 1, b: "x" } })).toBe("a=1, b=x");
    expect(fmtSettingValue({ kind: "backends", value: [{}, {}] })).toBe("2 backends");
    expect(fmtSettingValue({ kind: "backends", value: [{}] })).toBe("1 backend");
  });
});

describe("filterFields and groupFields", () => {
  const fields = [
    field({ key: "enabled", value: true, default: true }),
    field({ key: "capture_full_prompts", group: "Content capture", value: true, changed: true, source: "file", description: "Full-fidelity prompt capture" }),
    field({ key: "flush_interval_ms", group: "Export", kind: "int", value: 60000, env_invalid: true, env_raw: "soon" }),
  ];
  it("matches every word against key, description, value and source", () => {
    expect(filterFields(fields, { query: "prompt" }).map((f) => f.key)).toEqual(["capture_full_prompts"]);
    expect(filterFields(fields, { query: "file true" }).map((f) => f.key)).toEqual(["capture_full_prompts"]);
    expect(filterFields(fields, { query: "60000" }).map((f) => f.key)).toEqual(["flush_interval_ms"]);
    expect(filterFields(fields, { query: "" })).toHaveLength(3);
  });
  it("changedOnly keeps changed values and flagged invalid inputs", () => {
    expect(filterFields(fields, { changedOnly: true }).map((f) => f.key)).toEqual(["capture_full_prompts", "flush_interval_ms"]);
  });
  it("groups in the report's order and drops empty groups", () => {
    const g = groupFields(fields, ["Content capture", "General", "Logs", "Export"]);
    expect(g.map((x) => x.group)).toEqual(["Content capture", "General", "Export"]);
    expect(g[1].fields[0].key).toBe("enabled");
  });
});

describe("sourceNote", () => {
  it("explains overrides and rejected inputs", () => {
    expect(sourceNote(field({ source: "env", value: true, file_value: false }))).toBe("overrides the file's false");
    expect(sourceNote(field({ source: "env", value: true }))).toBe("from HERMES_OTEL_K");
    expect(sourceNote(field({ kind: "int", env_invalid: true, env_raw: "soon" }))).toBe("HERMES_OTEL_K=soon is not a valid int; ignored");
    expect(sourceNote(field({ kind: "int", file_invalid: true, file_value: "lots" }))).toBe('file value "lots" is not a valid int; ignored');
    expect(sourceNote(field({ source: "file", derived_from: ["content_capture"] }))).toBe("follows content_capture");
    expect(sourceNote(field({}))).toBeNull();
  });
});

describe("env grouping", () => {
  const env: EnvEntry[] = [
    { name: "HERMES_OTEL_ENABLED", group: "override", description: "", set: false, value: null, maps_to: "enabled" },
    { name: "HERMES_OTEL_CAPTURE_LOGS", group: "override", description: "", set: true, value: "true", maps_to: "capture_logs" },
    { name: "HERMES_HOME", group: "plugin", description: "", set: true, value: "/h", maps_to: null },
    { name: "OTEL_X", group: "other", description: "", set: true, value: "1", maps_to: null },
    { name: "LANGSMITH_API_KEY", group: "langsmith", description: "", set: false, value: null, maps_to: null },
  ];
  it("hides unset entries by default and keeps the fixed order", () => {
    const g = groupEnv(env, false);
    expect(g.map((x) => x.group)).toEqual(["override", "plugin", "other"]);
    expect(g[0].entries.map((e) => e.name)).toEqual(["HERMES_OTEL_CAPTURE_LOGS"]);
    expect(g[0].label).toContain("HERMES_OTEL_*");
  });
  it("shows unset entries on request", () => {
    const g = groupEnv(env, true);
    expect(g.map((x) => x.group)).toEqual(["override", "plugin", "langsmith", "other"]);
    expect(g[0].entries).toHaveLength(2);
  });
  it("counts", () => {
    expect(envCounts(env)).toEqual({ set: 3, known: 5 });
  });
});

describe("pathSourceLabel", () => {
  it("names each resolution rule", () => {
    expect(pathSourceLabel("env")).toBe("HERMES_OTEL_CONFIG");
    expect(pathSourceLabel("durable")).toBe("$HERMES_HOME/hermes_otel.yaml");
    expect(pathSourceLabel("legacy")).toContain("legacy");
    expect(pathSourceLabel("none")).toBe("no config file");
  });
});
