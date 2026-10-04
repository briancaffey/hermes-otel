import { describe, it, expect } from "vitest";
import { severityCounts, timeBuckets, groupLogAttributes, codeLocation, contextWindow, attributionHint, severityOf } from "../src/logs-lib";
import { logParams, logFiltersFromNav, navFromLogFilters, DEFAULT_LOG_FILTERS } from "../src/params";
import { readNav, navSearch } from "../src/nav";

const rows = [
  { time_unix_nano: 1_000_000_000_000, level: "INFO", body: "a" },
  { time_unix_nano: 1_000_000_000_000 + 10e9, level: "WARN", body: "b", event_name: "hermes.tool.call" },
  { time_unix_nano: 1_000_000_000_000 + 20e9, level: "ERROR", body: "c" },
  { time_unix_nano: 1_000_000_000_000 + 29e9, level: "FATAL", body: "d" },
];

describe("severity summary", () => {
  it("maps spellings onto bands and counts events", () => {
    expect(severityOf("WARNING")).toBe("WARN");
    expect(severityOf("CRITICAL")).toBe("ERROR");
    expect(severityOf(undefined)).toBe("OTHER");
    const c = severityCounts(rows as any);
    expect(c.total).toBe(4);
    expect(c.events).toBe(1);
    expect(c.bySeverity).toEqual({ ERROR: 2, WARN: 1, INFO: 1, DEBUG: 0, OTHER: 0 });
  });
});

describe("time buckets", () => {
  it("spreads rows over equal buckets with the error share", () => {
    const b = timeBuckets(rows as any, 3);
    expect(b.map((x) => x.total)).toEqual([1, 1, 2]);
    expect(b.map((x) => x.errors)).toEqual([0, 0, 2]);
    expect(b.map((x) => x.warns)).toEqual([0, 1, 0]);
    expect(b[0].startNs).toBe(1_000_000_000_000);
  });
  it("collapses to one bucket when every row shares a timestamp", () => {
    const b = timeBuckets([rows[0], rows[0]] as any, 5);
    expect(b).toHaveLength(1);
    expect(b[0].total).toBe(2);
  });
  it("honours an explicit window", () => {
    const b = timeBuckets(rows as any, 2, 1_000_000_000_000, 1_000_000_000_000 + 40e9);
    expect(b.map((x) => x.total)).toEqual([2, 2]);
  });
});

describe("expanded row helpers", () => {
  const attrs = {
    "hermes.session_id": "s",
    "gen_ai.tool.name": "bash",
    "code.file.path": "/x/y/tools.py",
    "code.line.number": 12,
    "code.function.name": "run",
    "exception.type": "ValueError",
    "exception.stacktrace": "Traceback...",
    custom: 1,
    "hermes.log.attribution": "session_tag",
  };
  it("groups attributes and lifts the stack trace", () => {
    const g = groupLogAttributes(attrs);
    expect(g.stacktrace).toBe("Traceback...");
    expect(g.groups.map((x) => x.label)).toEqual(["Hermes", "GenAI", "Exception", "Code location", "Other"]);
    expect(g.groups[2].entries).toEqual([["exception.type", "ValueError"]]);
  });
  it("renders the code location and the attribution hint", () => {
    expect(codeLocation(attrs)).toBe("y/tools.py:12 (run)");
    expect(codeLocation({})).toBeNull();
    expect(attributionHint(attrs)?.text).toBe("session tag");
    expect(attributionHint({})).toBeNull();
  });
  it("builds a ±30 s window in whole seconds", () => {
    expect(contextWindow(1_700_000_000_500_000_000, 30)).toEqual({ startS: 1_699_999_970, endS: 1_700_000_031 });
  });
});

describe("event and context filters in the URL and the query", () => {
  it("event name wins over events-only and centre sets the window", () => {
    const f = { ...DEFAULT_LOG_FILTERS, eventsOnly: true, eventName: "hermes.tool.call", centerNs: "1700000000500000000", windowS: 30 };
    const p = Object.fromEntries(logParams(f, "live", 200));
    expect(p.event_name).toBe("hermes.tool.call");
    expect(p.events_only).toBeUndefined();
    expect(p.start_s).toBe("1699999970");
    expect(p.end_s).toBe("1700000031");
    const search = navSearch({ tab: "logs", ...navFromLogFilters(f) }, "");
    expect(search).toContain("event=hermes.tool.call");
    expect(search).toContain("center=1700000000500000000");
    expect(search).not.toContain("win=");
    expect(logFiltersFromNav(readNav(search))).toEqual(f);
  });
  it("a non-default window survives the URL and junk is ignored", () => {
    const f = { ...DEFAULT_LOG_FILTERS, centerNs: "42", windowS: 120 };
    const nav = readNav(navSearch({ tab: "logs", ...navFromLogFilters(f) }, ""));
    expect(logFiltersFromNav(nav).windowS).toBe(120);
    expect(logFiltersFromNav({ center: "abc", win: "-1" })).toEqual(DEFAULT_LOG_FILTERS);
  });
});
