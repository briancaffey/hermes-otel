// Logs tab URL state and keyset paging params (#186).
import { describe, expect, it } from "vitest";
import { DEFAULT_LOG_FILTERS, logParams, logFiltersFromNav, navFromLogFilters, logPageSizeFromNav } from "../src/params";
import { readNav, navSearch } from "../src/nav";

const F = { minLevel: "30", logger: "hermes_otel", session: "s1", traceId: "t1", text: "final", lookback: 24 };

describe("logParams", () => {
  it("maps filters, page size and the cursor onto /logs/search", () => {
    expect(Object.fromEntries(logParams(F, "signoz", 500, "1790433574196187904"))).toEqual({
      lookback_hours: "24", limit: "500", backend: "signoz", min_level: "30", logger: "hermes_otel", session: "s1", trace_id: "t1", text: "final", before_ns: "1790433574196187904",
    });
  });
  it("omits the cursor on the newest page and rejects a non-numeric one", () => {
    const p = Object.fromEntries(logParams(DEFAULT_LOG_FILTERS, "live", 200, null));
    expect(p).toEqual({ lookback_hours: "1", limit: "200" });
    expect(logParams(DEFAULT_LOG_FILTERS, "live", 200, "abc").has("before_ns")).toBe(false);
  });
});

describe("URL round trip", () => {
  it("filters survive navSearch → readNav → logFiltersFromNav", () => {
    const search = navSearch({ tab: "logs", ...navFromLogFilters(F), size: "500", before: "42" }, "");
    expect(search).toBe("?tab=logs&trace=t1&session=s1&level=30&logger=hermes_otel&text=final&lookback=24&size=500&before=42");
    const nav = readNav(search);
    expect(logFiltersFromNav(nav)).toEqual(F);
    expect(logPageSizeFromNav(nav.size)).toBe(500);
    expect(nav.before).toBe("42");
  });
  it("defaults clear their keys instead of writing them", () => {
    expect(navFromLogFilters(DEFAULT_LOG_FILTERS)).toEqual({ level: "", logger: "", session: "", trace: "", text: "", lookback: "" });
    expect(navSearch({ tab: "logs", ...navFromLogFilters(DEFAULT_LOG_FILTERS) }, "?tab=logs&level=40&text=x")).toBe("?tab=logs");
  });
  it("ignores junk in the URL", () => {
    expect(logFiltersFromNav({ level: "high", lookback: "-3" })).toEqual(DEFAULT_LOG_FILTERS);
    expect(logPageSizeFromNav("7")).toBe(200);
  });
});
