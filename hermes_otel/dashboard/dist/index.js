/* hermes-otel dashboard — built from dashboard-ui/src (esbuild). Edit the TSX, not this file. */
"use strict";
(() => {
  // src/sdk.ts
  var SDK = window.__HERMES_PLUGIN_SDK__ || {};
  var PLUGINS = window.__HERMES_PLUGINS__ || {};
  var React = SDK.React;
  var hooks = SDK.hooks || {};
  var useState = hooks.useState;
  var useEffect = hooks.useEffect;
  var useCallback = hooks.useCallback;
  var useMemo = hooks.useMemo;
  var useRef = hooks.useRef;
  var useContext = hooks.useContext || SDK.React && SDK.React.useContext;
  var createContext = hooks.createContext || SDK.React && SDK.React.createContext;
  var useTheme = SDK.useTheme;
  var C = SDK.components || {};
  var { Card, CardHeader, CardContent, Badge, Button, Input, Label, SelectOption, Checkbox } = C;
  var HostSelect = C.Select;
  function Select({ "aria-label": ariaLabel, ...props }) {
    const el = React.createElement(HostSelect, props);
    if (!ariaLabel) return el;
    return React.createElement("label", { className: "otel-select-label" }, React.createElement("span", { className: "otel-sr-only" }, ariaLabel), el);
  }
  var API = "/api/plugins/hermes_otel";
  function pageProfile() {
    try {
      return new URLSearchParams(window.location.search).get("profile") || "";
    } catch {
      return "";
    }
  }
  function withProfile(url) {
    const p = pageProfile();
    if (!p || /[?&]profile=/.test(url)) return url;
    return `${url}${url.includes("?") ? "&" : "?"}profile=${encodeURIComponent(p)}`;
  }
  var hostFetch = SDK.fetchJSON;
  var fetchJSON = hostFetch ? (url, opts) => hostFetch(withProfile(url), opts) : async () => {
    throw new Error("0: dashboard SDK unavailable");
  };
  function api(path, params) {
    let q = "";
    if (params instanceof URLSearchParams) q = params.toString();
    else if (params) {
      const sp = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) if (v != null && v !== "") sp.set(k, String(v));
      q = sp.toString();
    }
    return fetchJSON(`${API}${path}${q ? `?${q}` : ""}`);
  }
  var cn = SDK.utils && SDK.utils.cn || ((...a) => a.filter(Boolean).join(" "));
  function register(name, component) {
    if (PLUGINS && typeof PLUGINS.register === "function") {
      PLUGINS.register(name, component);
    } else {
      console.error("[hermes_otel] dashboard plugin registry unavailable");
    }
  }
  var sdkOk = Boolean(SDK && SDK.React && PLUGINS && PLUGINS.register);

  // src/lib.ts
  function decodeAttrValue(v) {
    if (v == null) return null;
    if (typeof v !== "object") return v;
    if ("stringValue" in v) return v.stringValue;
    if ("intValue" in v) return Number(v.intValue);
    if ("doubleValue" in v) return v.doubleValue;
    if ("boolValue" in v) return v.boolValue;
    if ("arrayValue" in v) return (v.arrayValue.values || []).map(decodeAttrValue);
    if ("kvlistValue" in v) return attrsObject(v.kvlistValue.values);
    return JSON.stringify(v);
  }
  function attrsObject(attrs) {
    const out = {};
    for (const a of attrs || []) if (a && a.key) out[a.key] = decodeAttrValue(a.value);
    return out;
  }
  function fmtDurationMs(ms) {
    if (ms == null) return "\u2014";
    if (ms < 1) return `${(ms * 1e3).toFixed(0)}\xB5s`;
    if (ms < 1e3) return `${ms.toFixed(ms < 10 ? 1 : 0)}ms`;
    if (ms < 6e4) return `${(ms / 1e3).toFixed(2)}s`;
    const m = Math.floor(ms / 6e4);
    return `${m}m ${Math.round(ms % 6e4 / 1e3)}s`;
  }
  var TIME_FMT = {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  };
  var CLOCK_FMT = { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false };
  function fmtAbsTime(unixNano) {
    if (!unixNano) return "";
    try {
      const d = new Date(unixNano / 1e6);
      const p = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    } catch {
      return "";
    }
  }
  function fmtClock(unixNano) {
    if (!unixNano) return "";
    try {
      return new Date(unixNano / 1e6).toLocaleTimeString(void 0, CLOCK_FMT);
    } catch {
      return "";
    }
  }
  function localTimezone() {
    try {
      return Intl.DateTimeFormat(void 0, TIME_FMT).resolvedOptions().timeZone || "";
    } catch {
      return "";
    }
  }
  function fmtTimeAgo(unixNano) {
    if (!unixNano) return "";
    const diff = Date.now() - unixNano / 1e6;
    if (diff < 1500) return "just now";
    if (diff < 6e4) return `${Math.round(diff / 1e3)}s ago`;
    if (diff < 36e5) return `${Math.round(diff / 6e4)}m ago`;
    if (diff < 864e5) return `${Math.round(diff / 36e5)}h ago`;
    return `${Math.round(diff / 864e5)}d ago`;
  }
  function fmtTokens(n) {
    if (n == null || isNaN(Number(n))) return null;
    const num = Number(n);
    if (num >= 1e4) return `${(num / 1e3).toFixed(num >= 1e5 ? 0 : 1)}k`;
    return num.toLocaleString("en-US");
  }
  function fmtCost(usd) {
    if (usd == null || isNaN(Number(usd))) return "\u2014";
    if (usd === 0) return "$0.00";
    if (usd < 0.01) return `$${usd.toFixed(4)}`;
    return `$${usd.toFixed(2)}`;
  }
  function fmtCostExact(usd) {
    if (usd == null || isNaN(Number(usd))) return "\u2014";
    return `$${Number(usd).toFixed(4)}`;
  }
  function fmtInt(n) {
    return n == null ? "\u2014" : n.toLocaleString("en-US");
  }
  function clip(s, max) {
    if (s == null) return null;
    const str = (typeof s === "string" ? s : String(s)).replace(/\s+/g, " ").trim();
    if (!str) return null;
    return str.length <= max ? str : str.slice(0, max - 1) + "\u2026";
  }
  function kindOf(name, attrs) {
    const a = attrs || {};
    if (a["hermes.span_kind"] === "skill") return "skill";
    if (a["hermes.span_kind"] === "approval") return "approval";
    const n = (name || "").toLowerCase();
    if (n === "agent" || n.startsWith("agent.")) return "agent";
    if (n === "cron" || n.startsWith("cron")) return "cron";
    if (n.startsWith("session")) return "session";
    if (n.startsWith("skill.")) return "skill";
    if (n.startsWith("approval")) return "approval";
    if (n.startsWith("subagent")) return "subagent";
    if (n.startsWith("llm.")) return "llm";
    if (n.startsWith("api.")) return "api";
    if (n.startsWith("tool.")) return "tool";
    return "other";
  }
  var KIND_COLOR = {
    agent: "var(--otel-kind-agent)",
    llm: "var(--otel-kind-llm)",
    api: "var(--otel-kind-api)",
    tool: "var(--otel-kind-tool)",
    skill: "var(--otel-kind-skill)",
    approval: "var(--otel-kind-approval)",
    subagent: "var(--otel-kind-subagent)",
    session: "var(--otel-kind-agent)",
    cron: "var(--otel-kind-cron)",
    other: "var(--otel-kind-other)"
  };
  function traceAttrs(trace) {
    const out = {};
    const spanSets = trace.spanSets || (trace.spanSet ? [trace.spanSet] : []);
    if (!spanSets.length) return out;
    const spans = spanSets[0].spans || [];
    const priority = (s) => {
      const n = (s.name || "").toLowerCase();
      if (n.startsWith("api.")) return 0;
      if (n.startsWith("tool.")) return 1;
      if (n.startsWith("llm.")) return 2;
      return 3;
    };
    for (const sp of spans.slice().sort((a, b) => priority(a) - priority(b))) {
      for (const a of sp.attributes || []) {
        if (!a.key || out[a.key] != null) continue;
        const d = decodeAttrValue(a.value);
        if (d !== null && d !== void 0 && d !== "") out[a.key] = d;
      }
    }
    const root = spans.find((s) => priority(s) === 3);
    if (root && spans.length > 1) {
      for (const a of root.attributes || []) {
        if (!a.key || !TURN_TOTAL_KEYS.has(a.key)) continue;
        const d = decodeAttrValue(a.value);
        if (d !== null && d !== void 0 && d !== "") out[a.key] = d;
      }
    }
    return out;
  }
  var TURN_TOTAL_KEYS = /* @__PURE__ */ new Set([
    "gen_ai.usage.total_tokens",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "llm.token_count.total",
    "llm.token_count.prompt",
    "llm.token_count.completion",
    "hermes.cost.usage"
  ]);
  function traceSpanCount(trace) {
    if (typeof trace.spanCount === "number" && trace.spanCount > 0) return trace.spanCount;
    if (typeof trace.span_count === "number" && trace.span_count > 0) return trace.span_count;
    if (trace.serviceStats) {
      let total = 0;
      for (const k in trace.serviceStats) total += trace.serviceStats[k].spanCount || 0;
      if (total) return total;
    }
    return null;
  }
  function extractInputPreview(attrs) {
    const raw = attrs["input.value"];
    if (raw == null) return null;
    if (typeof raw === "string") {
      const t = raw.trim();
      if (t[0] === "[" || t[0] === "{") {
        try {
          const parsed = JSON.parse(t);
          if (Array.isArray(parsed)) {
            for (let i = parsed.length - 1; i >= 0; i--) {
              const m = parsed[i];
              if (m && m.role === "user") {
                const c = m.content;
                if (typeof c === "string") return c;
                if (Array.isArray(c)) {
                  const parts = [];
                  for (const p of c) {
                    if (typeof p === "string") parts.push(p);
                    else if (p && typeof p.text === "string") parts.push(p.text);
                  }
                  if (parts.length) return parts.join(" ");
                }
                if (c != null) return JSON.stringify(c);
              }
            }
            for (const m of parsed) if (m && typeof m.content === "string") return m.content;
          }
          return t;
        } catch {
          return raw;
        }
      }
      return raw;
    }
    return String(raw);
  }
  function extractOutputPreview(attrs) {
    return attrs["llm.output.content"] || attrs["output.value"] || null;
  }
  function buildSpanTree(batches) {
    if (!batches || !batches.length) return { roots: [], all: [] };
    const all = [];
    for (const b of batches) {
      const scopeSpans = b.scopeSpans || b.scope_spans || b.instrumentationLibrarySpans || [];
      for (const ss of scopeSpans) {
        for (const span of ss.spans || []) {
          const startNs = Number(span.startTimeUnixNano || span.start_time_unix_nano || 0);
          const endNs = Number(span.endTimeUnixNano || span.end_time_unix_nano || 0);
          all.push({
            spanId: span.spanId || span.span_id,
            parentSpanId: span.parentSpanId || span.parent_span_id || null,
            name: span.name,
            startNs,
            endNs,
            durationMs: endNs && startNs ? (endNs - startNs) / 1e6 : 0,
            status: span.status || null,
            _attrs: attrsObject(span.attributes),
            children: []
          });
        }
      }
    }
    return linkTree(all);
  }
  function linkTree(all) {
    const byId = {};
    all.forEach((s) => byId[s.spanId] = s);
    const roots = [];
    all.forEach((s) => {
      if (s.parentSpanId && byId[s.parentSpanId]) byId[s.parentSpanId].children.push(s);
      else roots.push(s);
    });
    const sortRec = (list) => {
      list.sort((a, b) => a.startNs - b.startNs);
      list.forEach((n) => sortRec(n.children));
    };
    sortRec(roots);
    return { roots, all };
  }
  function flatten(roots) {
    const out = [];
    const walk = (n, depth) => {
      out.push({ span: n, depth });
      n.children.forEach((c) => walk(c, depth + 1));
    };
    roots.forEach((r) => walk(r, 0));
    return out;
  }
  function statusCode(status) {
    var _a;
    if (!status) return null;
    const code = (_a = status.code) != null ? _a : status.statusCode;
    if (code === 2 || code === "STATUS_CODE_ERROR" || status === "ERROR") return "error";
    if (code === 1 || code === "STATUS_CODE_OK" || status === "OK") return "ok";
    return null;
  }
  function attrNum(a, ...keys) {
    for (const k of keys) {
      const v = a[k];
      if (typeof v === "number") return v;
      if (typeof v === "string" && v.trim() && !isNaN(Number(v))) return Number(v);
    }
    return null;
  }
  function findRoot(spans) {
    const ids = new Set(spans.map((s) => {
      var _a;
      return (_a = s.span_id) != null ? _a : s.spanId;
    }));
    return spans.find((s) => {
      var _a, _b;
      return !((_a = s.parent_span_id) != null ? _a : s.parentSpanId) || !ids.has((_b = s.parent_span_id) != null ? _b : s.parentSpanId);
    }) || null;
  }
  function rowFromLive(t) {
    var _a;
    return {
      traceId: String(t.traceId),
      rootName: t.rootName,
      rootKind: t.rootKind || kindOf(t.rootName),
      service: t.service || null,
      startNs: t.startNs,
      endNs: t.endNs || t.startNs,
      durationMs: t.durationMs,
      spanCount: (_a = t.spanCount) != null ? _a : null,
      model: t.model,
      tokens: t.tokens,
      cost: t.cost,
      error: !!t.error,
      session: t.session,
      partial: !!t.partial,
      toolName: null,
      inPreview: null,
      outPreview: null,
      raw: t
    };
  }
  function rowFromBackend(t) {
    const attrs = traceAttrs(t);
    const startNs = t.startTimeUnixNano ? Number(t.startTimeUnixNano) : 0;
    const durationMs = Number(t.durationMs || 0);
    const name = t.rootTraceName || "";
    return {
      traceId: String(t.traceID || t.traceId || ""),
      rootName: name || "\u2014",
      rootKind: kindOf(name, attrs),
      service: t.rootServiceName || null,
      startNs,
      endNs: startNs + durationMs * 1e6,
      durationMs,
      spanCount: traceSpanCount(t),
      model: attrs["gen_ai.request.model"] || attrs["llm.model_name"] || attrs["gen_ai.response.model"] || null,
      tokens: attrNum(attrs, "gen_ai.usage.total_tokens", "llm.token_count.total"),
      cost: attrNum(attrs, "hermes.cost.usage"),
      error: attrs["status"] === "error" || !!attrs["error.type"],
      session: sessionOfCard(t),
      partial: false,
      toolName: attrs["tool.name"] ? String(attrs["tool.name"]) : null,
      inPreview: clip(extractInputPreview(attrs), 140),
      outPreview: clip(extractOutputPreview(attrs), 140),
      raw: t
    };
  }
  function sessionOfCard(trace) {
    const a = traceAttrs(trace);
    return a["hermes.session_id"] || a["langfuse.sessionId"] || a["session.id"] || a["session_id"] || null;
  }
  function groupBySession(traces) {
    const by = {};
    for (const t of traces) {
      const sid = sessionOfCard(t);
      if (!sid) continue;
      const a = traceAttrs(t);
      const start = Number(t.startTimeUnixNano || 0);
      const end = start + Number(t.durationMs || 0) * 1e6;
      const tok = attrNum(a, "gen_ai.usage.total_tokens", "llm.token_count.total");
      const cost = attrNum(a, "hermes.cost.usage");
      const spanCount = traceSpanCount(t);
      const row = by[sid] || (by[sid] = {
        session: sid,
        turns: 0,
        spans: 0,
        errors: 0,
        tokens: null,
        cost: null,
        toolCalls: null,
        startNs: start,
        endNs: end,
        model: a["llm.model_name"] || a["gen_ai.request.model"] || null,
        platform: a["hermes.platform"] || null,
        traceIds: []
      });
      row.turns += 1;
      row.spans = spanCount == null || row.spans == null ? null : row.spans + spanCount;
      if (a["status"] === "error" || a["error.type"]) row.errors += 1;
      if (tok != null) row.tokens = (row.tokens || 0) + tok;
      if (cost != null) row.cost = (row.cost || 0) + cost;
      row.startNs = Math.min(row.startNs, start);
      row.endNs = Math.max(row.endNs, end);
      row.traceIds.push(t.traceID || t.traceId);
    }
    return Object.values(by).sort((x, y) => y.endNs - x.endNs);
  }
  function sumApiSpans(spans, ...keys) {
    let sum = 0;
    let seen = false;
    for (const s of spans) {
      if (!(s.name || "").startsWith("api.")) continue;
      const v = attrNum(s.attributes || {}, ...keys);
      if (v != null) {
        sum += v;
        seen = true;
      }
    }
    return seen ? sum : null;
  }
  var MCP_PING_NAME = "MCP send ping";
  function isMcpKeepalivePing(rootName, error) {
    return rootName === MCP_PING_NAME && !error;
  }
  function liveTreeFromSpans(spans) {
    const all = spans.map((s) => ({
      spanId: s.span_id,
      parentSpanId: s.parent_span_id || null,
      name: s.name,
      startNs: s.start_time_unix_nano || 0,
      endNs: s.end_time_unix_nano || s.start_time_unix_nano || 0,
      durationMs: s.duration_ms || 0,
      status: s.status === "ERROR" ? { code: 2 } : null,
      _attrs: s.attributes || {},
      children: []
    }));
    return linkTree(all);
  }
  var DUPLICATE_GROUPS = [
    ["gen_ai.request.model", "llm.model_name"],
    ["gen_ai.usage.input_tokens", "llm.token_count.prompt"],
    ["gen_ai.usage.output_tokens", "llm.token_count.completion"],
    ["gen_ai.usage.total_tokens", "llm.token_count.total"],
    ["gen_ai.usage.reasoning.output_tokens", "llm.token_count.completion_details.reasoning"],
    ["gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_read_input_tokens", "llm.token_count.prompt_details.cache_read"],
    ["gen_ai.provider.name", "gen_ai.system", "llm.provider"],
    ["hermes.session_id", "session.id", "session_id", "gen_ai.conversation.id", "wandb.thread_id"],
    ["openinference.span.kind", "traceloop.span.kind"]
  ];
  function groupAttrs(attrs) {
    const folded = /* @__PURE__ */ new Set();
    const alias = {};
    for (const grp of DUPLICATE_GROUPS) {
      const present = grp.filter((k) => attrs[k] !== void 0 && attrs[k] !== null && attrs[k] !== "");
      if (present.length > 1) {
        alias[present[0]] = present.slice(1);
        present.slice(1).forEach((k) => folded.add(k));
      }
    }
    const groups = {};
    for (const key of Object.keys(attrs).sort()) {
      if (folded.has(key)) continue;
      const prefix = key.includes(".") ? key.split(".")[0] : "other";
      (groups[prefix] || (groups[prefix] = { prefix, entries: [] })).entries.push({ key, value: attrs[key], aliases: alias[key] || [] });
    }
    const order = ["hermes", "gen_ai", "llm", "tool", "input", "output", "session", "openinference"];
    return Object.values(groups).sort((a, b) => {
      const ia = order.indexOf(a.prefix);
      const ib = order.indexOf(b.prefix);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.prefix.localeCompare(b.prefix);
    });
  }
  function headerFacts(root, spans = []) {
    var _a;
    const a = root || {};
    const num = (...keys) => attrNum(a, ...keys);
    let totalTokens = num("gen_ai.usage.total_tokens", "llm.token_count.total");
    let inputTokens = num("gen_ai.usage.input_tokens", "llm.token_count.prompt");
    let outputTokens = num("gen_ai.usage.output_tokens", "llm.token_count.completion");
    if (totalTokens == null) {
      const t = sumApiSpans(spans, "gen_ai.usage.total_tokens", "llm.token_count.total");
      if (t != null) {
        totalTokens = t;
        inputTokens = inputTokens != null ? inputTokens : sumApiSpans(spans, "gen_ai.usage.input_tokens", "llm.token_count.prompt");
        outputTokens = outputTokens != null ? outputTokens : sumApiSpans(spans, "gen_ai.usage.output_tokens", "llm.token_count.completion");
      }
    }
    const costUnknown = a["hermes.cost.status"] != null && num("hermes.cost.usage") == null;
    const cost = costUnknown ? null : (_a = num("hermes.cost.usage")) != null ? _a : sumApiSpans(spans, "hermes.cost.usage");
    const toolsRaw = a["hermes.turn.tools"];
    let tools = [];
    if (Array.isArray(toolsRaw)) tools = toolsRaw.map(String);
    else if (typeof toolsRaw === "string" && toolsRaw.trim()) {
      try {
        const parsed = JSON.parse(toolsRaw);
        tools = Array.isArray(parsed) ? parsed.map(String) : toolsRaw.split(",").map((s) => s.trim()).filter(Boolean);
      } catch {
        tools = toolsRaw.split(",").map((s) => s.trim()).filter(Boolean);
      }
    }
    const requestModel = a["gen_ai.request.model"] || a["llm.model_name"] || null;
    const responseModel = a["gen_ai.response.model"] || null;
    return {
      requestModel,
      responseModel: responseModel && responseModel !== requestModel ? responseModel : null,
      inputTokens,
      outputTokens,
      reasoningTokens: num("gen_ai.usage.reasoning.output_tokens", "llm.token_count.completion_details.reasoning"),
      cacheReadTokens: num("gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_read_input_tokens", "llm.token_count.prompt_details.cache_read"),
      totalTokens,
      cost,
      costUnknown,
      tools,
      exitReason: a["hermes.turn.exit_reason"] || null,
      finalStatus: a["hermes.turn.final_status"] || null,
      session: a["hermes.session_id"] || a["session.id"] || a["session_id"] || null,
      turn: num("hermes.turn.number"),
      platform: a["hermes.platform"] || null
    };
  }
  function metricOtlpName(n) {
    if (n.includes(".")) return n;
    const base = n.replace(/_(sum|count|bucket|total)$/, "");
    if (base.startsWith("hermes_prompt_cache_")) return "hermes.prompt_cache." + base.slice("hermes_prompt_cache_".length);
    return base.replace(/_/g, ".");
  }

  // src/errors.ts
  var KIND_TEXT = {
    not_found: "Not found",
    auth: "The backend rejected the credentials",
    config: "The backend entry is not configured for this",
    backend: "The backend did not answer",
    validation: "The request was rejected",
    network: "The dashboard server is unreachable",
    unknown: "Request failed"
  };
  function describeError(e) {
    var _a, _b;
    const msg = String((_b = (_a = e == null ? void 0 : e.message) != null ? _a : e) != null ? _b : "").trim();
    const m = /^(\d{3}):\s*([\s\S]*)$/.exec(msg);
    const status = m ? Number(m[1]) : null;
    let detail = m ? m[2].trim() : msg;
    let kind = "unknown";
    if (m) {
      try {
        const body = JSON.parse(detail);
        if (body && typeof body === "object") {
          if (typeof body.detail === "string") detail = body.detail;
          else if (Array.isArray(body.detail)) detail = body.detail.map((d) => (d == null ? void 0 : d.msg) || JSON.stringify(d)).join("; ");
          else if (body.detail != null) detail = JSON.stringify(body.detail);
          if (typeof body.kind === "string") kind = body.kind;
        }
      } catch {
      }
      if (kind === "unknown") {
        if (status === 404) kind = "not_found";
        else if (status === 401 || status === 403) kind = "auth";
        else if (status === 422 || status === 400) kind = "validation";
        else if (status === 503) kind = "config";
        else if (status === 502 || status === 504) kind = "backend";
      }
    } else if (/unreachable|failed to fetch|network|refused|offline/i.test(msg)) {
      kind = "network";
    }
    if (status === 0) kind = "network";
    const hint = kind === "auth" ? "Check the credential on the backend entry in the Settings tab." : kind === "config" ? "Check the backend entry in the Settings tab." : kind === "backend" ? "The Live source needs no backend and always works." : kind === "network" ? "Is the Hermes dashboard still running?" : null;
    return { status, kind, detail, text: KIND_TEXT[kind], hint };
  }

  // src/icons.tsx
  function svg(size, className, children) {
    return /* @__PURE__ */ React.createElement(
      "svg",
      {
        width: size || 16,
        height: size || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
        className: className || "",
        "aria-hidden": true
      },
      children
    );
  }
  var IconZap = (p) => svg(p.size, p.className, /* @__PURE__ */ React.createElement("polygon", { points: "13 2 3 14 12 14 11 22 21 10 12 10 13 2" }));
  var IconWrench = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement("path", { d: "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" })
  );
  var IconTerminal = (p) => svg(p.size, p.className, [/* @__PURE__ */ React.createElement("polyline", { key: "a", points: "4 17 10 11 4 5" }), /* @__PURE__ */ React.createElement("line", { key: "b", x1: 12, x2: 20, y1: 19, y2: 19 })]);
  var IconClock = (p) => svg(p.size, p.className, [/* @__PURE__ */ React.createElement("circle", { key: "a", cx: 12, cy: 12, r: 10 }), /* @__PURE__ */ React.createElement("polyline", { key: "b", points: "12 6 12 12 16 14" })]);
  var IconActivity = (p) => svg(p.size, p.className, /* @__PURE__ */ React.createElement("polyline", { points: "22 12 18 12 15 21 9 3 6 12 2 12" }));
  var IconChevronRight = (p) => svg(p.size, p.className, /* @__PURE__ */ React.createElement("path", { d: "m9 18 6-6-6-6" }));
  var IconSparkles = (p) => svg(p.size, p.className, /* @__PURE__ */ React.createElement("path", { d: "M9.94 14.06 7 21l-2.94-6.94L-.94 12 7 9.06 9.94 3l2.94 6.06L19.94 12zM18 5l1 2.5L21.5 8 19 9l-1 2.5L17 9l-2.5-1L17 7z" }));
  var IconShield = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement("path", { d: "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" })
  );
  var IconUsers = (p) => svg(p.size, p.className, [
    /* @__PURE__ */ React.createElement("path", { key: "a", d: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" }),
    /* @__PURE__ */ React.createElement("circle", { key: "b", cx: 9, cy: 7, r: 4 }),
    /* @__PURE__ */ React.createElement("path", { key: "c", d: "M22 21v-2a4 4 0 0 0-3-3.87" }),
    /* @__PURE__ */ React.createElement("path", { key: "d", d: "M16 3.13a4 4 0 0 1 0 7.75" })
  ]);
  var IconChart = (p) => svg(p.size, p.className, [/* @__PURE__ */ React.createElement("path", { key: "a", d: "M3 3v16a2 2 0 0 0 2 2h16" }), /* @__PURE__ */ React.createElement("path", { key: "b", d: "m19 9-5 5-4-4-3 3" })]);
  var IconList = (p) => svg(p.size, p.className, [
    /* @__PURE__ */ React.createElement("line", { key: "a", x1: 8, x2: 21, y1: 6, y2: 6 }),
    /* @__PURE__ */ React.createElement("line", { key: "b", x1: 8, x2: 21, y1: 12, y2: 12 }),
    /* @__PURE__ */ React.createElement("line", { key: "c", x1: 8, x2: 21, y1: 18, y2: 18 }),
    /* @__PURE__ */ React.createElement("line", { key: "d", x1: 3, x2: 3.01, y1: 6, y2: 6 }),
    /* @__PURE__ */ React.createElement("line", { key: "e", x1: 3, x2: 3.01, y1: 12, y2: 12 }),
    /* @__PURE__ */ React.createElement("line", { key: "f", x1: 3, x2: 3.01, y1: 18, y2: 18 })
  ]);
  var CATEGORY = {
    llm: { Icon: IconZap, color: "otel-c-llm", label: "llm" },
    tool: { Icon: IconWrench, color: "otel-c-tool", label: "tool" },
    agent: { Icon: IconTerminal, color: "otel-c-agent", label: "agent" },
    cron: { Icon: IconClock, color: "otel-c-cron", label: "cron" },
    skill: { Icon: IconSparkles, color: "otel-c-skill", label: "skill" },
    approval: { Icon: IconShield, color: "otel-c-approval", label: "approval" },
    subagent: { Icon: IconUsers, color: "otel-c-subagent", label: "subagent" },
    other: { Icon: IconActivity, color: "text-muted-foreground", label: null }
  };
  function kindIcon(kind) {
    return (CATEGORY[kind] || CATEGORY.other).Icon;
  }
  var IconSettings = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("path", { d: "M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" }), /* @__PURE__ */ React.createElement("circle", { cx: "12", cy: "12", r: "3" }))
  );
  var IconRefresh = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("path", { d: "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" }), /* @__PURE__ */ React.createElement("path", { d: "M21 3v5h-5" }), /* @__PURE__ */ React.createElement("path", { d: "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" }), /* @__PURE__ */ React.createElement("path", { d: "M8 16H3v5" }))
  );
  var IconCopy = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("rect", { width: "14", height: "14", x: "8", y: "8", rx: "2", ry: "2" }), /* @__PURE__ */ React.createElement("path", { d: "M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" }))
  );
  var IconCheck = (p) => svg(p.size, p.className, /* @__PURE__ */ React.createElement("path", { d: "M20 6 9 17l-5-5" }));
  var IconExternal = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("path", { d: "M15 3h6v6" }), /* @__PURE__ */ React.createElement("path", { d: "M10 14 21 3" }), /* @__PURE__ */ React.createElement("path", { d: "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" }))
  );
  var IconDatabase = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("ellipse", { cx: "12", cy: "5", rx: "9", ry: "3" }), /* @__PURE__ */ React.createElement("path", { d: "M3 5v14a9 3 0 0 0 18 0V5" }), /* @__PURE__ */ React.createElement("path", { d: "M3 12a9 3 0 0 0 18 0" }))
  );
  var IconPause = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("rect", { x: "6", y: "4", width: "4", height: "16" }), /* @__PURE__ */ React.createElement("rect", { x: "14", y: "4", width: "4", height: "16" }))
  );
  var IconPlay = (p) => svg(p.size, p.className, /* @__PURE__ */ React.createElement("polygon", { points: "6 3 20 12 6 21 6 3" }));
  var IconSkipBack = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("polygon", { points: "19 20 9 12 19 4 19 20" }), /* @__PURE__ */ React.createElement("line", { x1: "5", x2: "5", y1: "19", y2: "5" }))
  );
  var IconArrowLeft = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("path", { d: "m12 19-7-7 7-7" }), /* @__PURE__ */ React.createElement("path", { d: "M19 12H5" }))
  );
  var IconArrowRight = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("path", { d: "M5 12h14" }), /* @__PURE__ */ React.createElement("path", { d: "m12 5 7 7-7 7" }))
  );
  var IconAlert = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("path", { d: "m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" }), /* @__PURE__ */ React.createElement("path", { d: "M12 9v4" }), /* @__PURE__ */ React.createElement("path", { d: "M12 17h.01" }))
  );
  var IconScrollText = (p) => svg(
    p.size,
    p.className,
    /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("path", { d: "M15 12h-5" }), /* @__PURE__ */ React.createElement("path", { d: "M15 8h-5" }), /* @__PURE__ */ React.createElement("path", { d: "M19 17V5a2 2 0 0 0-2-2H4" }), /* @__PURE__ */ React.createElement("path", { d: "M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3" }))
  );

  // src/atoms.tsx
  function MiniLabel(props) {
    return /* @__PURE__ */ React.createElement("span", { className: "text-[11px] font-medium uppercase tracking-wide text-muted-foreground" }, props.children);
  }
  function ErrorBanner({ error, prefix }) {
    const e = typeof error === "object" && error && "kind" in error && "detail" in error ? error : describeError(error);
    return /* @__PURE__ */ React.createElement("div", { role: "alert", className: "otel-error-banner" }, /* @__PURE__ */ React.createElement(IconAlert, { size: 14, className: "shrink-0" }), /* @__PURE__ */ React.createElement("div", { className: "min-w-0" }, /* @__PURE__ */ React.createElement("div", null, prefix ? `${prefix}: ` : "", e.text, e.status ? /* @__PURE__ */ React.createElement("span", { className: "otel-error-status" }, " ", e.status) : null), e.detail && e.detail !== e.text ? /* @__PURE__ */ React.createElement("div", { className: "otel-error-detail" }, e.detail) : null, e.hint ? /* @__PURE__ */ React.createElement("div", { className: "otel-error-hint" }, e.hint) : null));
  }
  function Clickable({
    onActivate,
    className,
    children,
    label,
    as = "div",
    toggle,
    open,
    ...rest
  }) {
    const Tag = as;
    if (toggle) {
      const { "aria-expanded": _ignored, ...plain } = rest;
      return /* @__PURE__ */ React.createElement(Tag, { className, onClick: onActivate, ...plain }, /* @__PURE__ */ React.createElement(
        "button",
        {
          type: "button",
          className: "otel-row-toggle",
          "aria-expanded": !!open,
          "aria-label": label,
          onClick: (e) => {
            e.stopPropagation();
            onActivate();
          }
        },
        open ? "\u25BE" : "\u25B8"
      ), children);
    }
    return /* @__PURE__ */ React.createElement(
      Tag,
      {
        role: "button",
        tabIndex: 0,
        "aria-label": label,
        className,
        onClick: onActivate,
        onKeyDown: (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onActivate();
          }
        },
        ...rest
      },
      children
    );
  }
  function CopyButton({ text, label, className }) {
    const [done, setDone] = useState(false);
    return /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        className: cn("otel-link inline-flex items-center gap-1 text-[10px] text-muted-foreground", className),
        title: `copy ${label || "to clipboard"}`,
        onClick: (e) => {
          var _a;
          e.stopPropagation();
          const p = (_a = navigator.clipboard) == null ? void 0 : _a.writeText(text);
          if (p && typeof p.then === "function") p.catch(() => void 0);
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        }
      },
      done ? /* @__PURE__ */ React.createElement(IconCheck, { size: 11 }) : /* @__PURE__ */ React.createElement(IconCopy, { size: 11 }),
      done ? "copied" : label || "copy"
    );
  }
  function Stat({
    label,
    value,
    sub,
    accent,
    unknownText
  }) {
    const unknown = value == null;
    const valColor = unknown ? "text-muted-foreground" : accent === "cost" ? "otel-c-cost" : accent === "error" ? "text-destructive" : "text-foreground";
    return /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg border border-border px-3 py-2.5" }, /* @__PURE__ */ React.createElement("div", { className: cn("text-xl font-semibold tabular-nums tracking-tight", valColor) }, unknown ? "\u2014" : value), /* @__PURE__ */ React.createElement("div", { className: "text-[11px] uppercase tracking-wide text-muted-foreground" }, label), unknown && unknownText ? /* @__PURE__ */ React.createElement("div", { className: "mt-0.5 text-[11px] text-muted-foreground" }, unknownText) : sub != null ? /* @__PURE__ */ React.createElement("div", { className: "mt-0.5 text-[11px] text-muted-foreground" }, sub) : null);
  }
  function Pulse({ active }) {
    return /* @__PURE__ */ React.createElement("span", { className: cn("inline-block h-2.5 w-2.5 rounded-full", active ? "otel-pulse-dot otel-pulse" : "bg-muted-foreground/40"), "aria-hidden": true });
  }
  function Sparkline({ values, height = 30, color, label }) {
    const max = Math.max(1, ...values);
    const w = values.length || 1;
    const bw = 100 / w;
    const fill = color || "var(--otel-kind-agent)";
    return /* @__PURE__ */ React.createElement("svg", { viewBox: `0 0 100 ${height}`, preserveAspectRatio: "none", style: { width: "100%", height }, role: "img", "aria-label": label || "activity over time" }, values.map((v, i) => {
      const h = v / max * (height - 2);
      return /* @__PURE__ */ React.createElement("rect", { key: i, x: i * bw + 0.25, y: height - h, width: Math.max(0.5, bw - 0.5), height: h || 0.5, fill, opacity: 0.3 + 0.7 * (i / w) });
    }));
  }
  function yTicks(max, n = 3) {
    if (!(max > 0)) return [0];
    const raw = max / n;
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) || raw;
    const out = [];
    for (let v = 0; ; v += step) {
      out.push(Number(v.toFixed(10)));
      if (v >= max - 1e-9) break;
    }
    return out;
  }
  function LineChart({
    series,
    height = 120,
    labels,
    fmt,
    bucketLabels
  }) {
    var _a;
    const [hover, setHover] = useState(null);
    const box = useRef(null);
    const n = Math.max(1, ...series.map((s) => s.points.length));
    const vals = series.flatMap((s) => s.points.filter((v) => v != null));
    const rawMax = vals.length ? Math.max(...vals, 0) : 0;
    const ticks = yTicks(rawMax);
    const max = Math.max(ticks[ticks.length - 1] || 0, rawMax) || 1;
    const fmtY = (v) => fmt ? fmt(v) : v >= 1e3 ? `${(v / 1e3).toFixed(v >= 1e4 ? 0 : 1)}k` : v.toFixed(v < 10 && v !== Math.round(v) ? 2 : 0);
    const W = 100;
    const PAD = 3;
    const x = (i) => i / Math.max(1, n - 1) * W;
    const y = (v) => height - v / max * (height - 2 * PAD) - PAD;
    const path = (pts) => {
      let d = "";
      let pen = false;
      pts.forEach((v, i) => {
        if (v == null) {
          pen = false;
          return;
        }
        d += `${pen ? "L" : "M"} ${x(i).toFixed(2)} ${y(v).toFixed(2)} `;
        pen = true;
      });
      return d;
    };
    const onMove = (e) => {
      var _a2, _b;
      const r = (_b = (_a2 = box.current) == null ? void 0 : _a2.getBoundingClientRect) == null ? void 0 : _b.call(_a2);
      if (!r || !r.width) return;
      const i = Math.round((e.clientX - r.left) / r.width * (n - 1));
      setHover(Math.max(0, Math.min(n - 1, i)));
    };
    return /* @__PURE__ */ React.createElement("div", { className: "otel-chart" }, /* @__PURE__ */ React.createElement("div", { className: "otel-chart-y", "aria-hidden": true }, ticks.slice().reverse().map((t) => /* @__PURE__ */ React.createElement("span", { key: t, style: { top: `${(max - t) / max * 100}%` } }, fmtY(t)))), /* @__PURE__ */ React.createElement("div", { className: "otel-chart-plot", ref: box, onMouseMove: onMove, onMouseLeave: () => setHover(null) }, /* @__PURE__ */ React.createElement(
      "svg",
      {
        viewBox: `0 0 ${W} ${height}`,
        preserveAspectRatio: "none",
        style: { width: "100%", height },
        role: "img",
        "aria-label": `${series.map((s) => s.label).join(", ")} over time`
      },
      ticks.map((t) => /* @__PURE__ */ React.createElement("line", { key: t, x1: 0, x2: W, y1: y(t), y2: y(t), stroke: "var(--color-border)", strokeWidth: 0.3, vectorEffect: "non-scaling-stroke" })),
      series.map((s) => /* @__PURE__ */ React.createElement("path", { key: s.label, d: path(s.points), fill: "none", stroke: s.color, strokeWidth: 1.2, vectorEffect: "non-scaling-stroke" })),
      series.map(
        (s) => s.points.map(
          (v, i) => v != null && s.points[i - 1] == null && s.points[i + 1] == null ? /* @__PURE__ */ React.createElement("circle", { key: `${s.label}-${i}`, cx: x(i), cy: y(v), r: 1.2, fill: s.color }) : null
        )
      ),
      hover != null ? /* @__PURE__ */ React.createElement(
        "line",
        {
          x1: x(hover),
          x2: x(hover),
          y1: 0,
          y2: height,
          stroke: "var(--color-foreground)",
          strokeWidth: 0.6,
          opacity: 0.5,
          vectorEffect: "non-scaling-stroke"
        }
      ) : null,
      hover != null ? series.map(
        (s) => s.points[hover] != null ? /* @__PURE__ */ React.createElement("circle", { key: s.label, cx: x(hover), cy: y(s.points[hover]), r: 1.4, fill: s.color }) : null
      ) : null
    ), hover != null ? /* @__PURE__ */ React.createElement("div", { className: "otel-chart-tip", style: { left: `${hover / Math.max(1, n - 1) * 100}%` } }, /* @__PURE__ */ React.createElement("div", { className: "text-muted-foreground" }, (_a = bucketLabels == null ? void 0 : bucketLabels[hover]) != null ? _a : `bucket ${hover + 1}`), series.map((s) => /* @__PURE__ */ React.createElement("div", { key: s.label, className: "flex items-center gap-1.5" }, /* @__PURE__ */ React.createElement("span", { className: "otel-w-2 inline-block h-2 rounded-full", style: { background: s.color } }), /* @__PURE__ */ React.createElement("span", { className: "truncate" }, s.label), /* @__PURE__ */ React.createElement("span", { className: "ml-auto tabular-nums" }, s.points[hover] == null ? "no data" : fmtY(s.points[hover]))))) : null), /* @__PURE__ */ React.createElement("div", { className: "otel-chart-x" }, labels && labels.length ? labels.map((l, i) => /* @__PURE__ */ React.createElement("span", { key: i }, l)) : null), /* @__PURE__ */ React.createElement("div", { className: "mt-1 flex flex-wrap items-center gap-3" }, series.map((s) => /* @__PURE__ */ React.createElement("span", { key: s.label, className: "inline-flex items-center gap-1.5 text-[11px] text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", { className: "otel-w-2 inline-block h-2 rounded-full", style: { background: s.color } }), s.label))));
  }
  function Pager({
    page,
    hasMore,
    onNewest,
    onNewer,
    onOlder,
    range
  }) {
    const first2 = page <= 1;
    return /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", null, range || ""), /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, "page ", page), /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-btn", onClick: onNewest, disabled: first2, title: "newest page" }, /* @__PURE__ */ React.createElement(IconSkipBack, { size: 12 }), " Newest"), /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-btn", onClick: onNewer, disabled: first2, title: "newer page" }, /* @__PURE__ */ React.createElement(IconArrowLeft, { size: 12 }), " Newer"), /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-btn", onClick: onOlder, disabled: !hasMore, title: hasMore ? "older page" : "no older rows" }, "Older ", /* @__PURE__ */ React.createElement(IconArrowRight, { size: 12 }))));
  }
  function Segmented({
    value,
    options,
    onChange,
    label
  }) {
    return /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg inline-flex border border-border p-0.5", role: "group", "aria-label": label }, options.map((o) => /* @__PURE__ */ React.createElement(
      "button",
      {
        key: o.id,
        type: "button",
        "aria-pressed": value === o.id,
        onClick: () => onChange(o.id),
        className: cn(
          "otel-toggle px-3 py-1 text-xs font-medium transition-colors",
          value === o.id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground"
        )
      },
      o.label
    )));
  }
  function Empty({ title, children, className }) {
    return /* @__PURE__ */ React.createElement("div", { className: cn("border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground", className) }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, title), children);
  }
  function Toggle({
    checked,
    onChange,
    label,
    title,
    Switch
  }) {
    const id = useRef(`otel-sw-${Math.random().toString(36).slice(2, 8)}`);
    return /* @__PURE__ */ React.createElement("span", { className: "inline-flex items-center gap-1.5 text-[11px] text-muted-foreground", title }, Switch ? /* @__PURE__ */ React.createElement(Switch, { checked, onCheckedChange: onChange, id: id.current }) : /* @__PURE__ */ React.createElement("input", { id: id.current, type: "checkbox", checked, onChange: (e) => onChange(e.target.checked) }), /* @__PURE__ */ React.createElement("label", { htmlFor: id.current, className: "cursor-pointer" }, label));
  }

  // src/md.ts
  var SAFE_HREF = /^(https?:\/\/|mailto:|#|\/)/i;
  function parseInline(src) {
    const out = [];
    let buf = "";
    const flush = () => {
      if (buf) out.push({ t: "text", v: buf });
      buf = "";
    };
    let i = 0;
    while (i < src.length) {
      const ch = src[i];
      if (ch === "`") {
        const end = src.indexOf("`", i + 1);
        if (end > i) {
          flush();
          out.push({ t: "code", v: src.slice(i + 1, end) });
          i = end + 1;
          continue;
        }
      }
      if (ch === "*" && src[i + 1] === "*") {
        const end = src.indexOf("**", i + 2);
        if (end > i + 2) {
          flush();
          out.push({ t: "strong", children: parseInline(src.slice(i + 2, end)) });
          i = end + 2;
          continue;
        }
      }
      if ((ch === "*" || ch === "_") && src[i + 1] !== ch && src[i + 1] !== " ") {
        const end = src.indexOf(ch, i + 1);
        if (end > i + 1 && src[end - 1] !== " ") {
          flush();
          out.push({ t: "em", children: parseInline(src.slice(i + 1, end)) });
          i = end + 1;
          continue;
        }
      }
      if (ch === "[") {
        const close = src.indexOf("](", i + 1);
        const end = close > 0 ? src.indexOf(")", close + 2) : -1;
        if (close > i && end > close) {
          const href = src.slice(close + 2, end).trim();
          if (SAFE_HREF.test(href)) {
            flush();
            out.push({ t: "link", href, children: parseInline(src.slice(i + 1, close)) });
            i = end + 1;
            continue;
          }
        }
      }
      if (ch === "h" && /^https?:\/\/\S+/.test(src.slice(i))) {
        const m = /^https?:\/\/[^\s<>)]+/.exec(src.slice(i));
        flush();
        out.push({ t: "link", href: m[0], children: [{ t: "text", v: m[0] }] });
        i += m[0].length;
        continue;
      }
      buf += ch;
      i++;
    }
    flush();
    return out;
  }
  function splitRow(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  }
  var isSeparatorRow = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
  function parseBlocks(src) {
    const lines = src.replace(/\r\n?/g, "\n").split("\n");
    const blocks = [];
    let para = [];
    const flushPara = () => {
      if (para.length) blocks.push({ t: "paragraph", children: parseInline(para.join("\n")) });
      para = [];
    };
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const fence = /^\s*```\s*(\S*)\s*$/.exec(line);
      if (fence) {
        flushPara();
        const buf = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) buf.push(lines[i++]);
        i++;
        blocks.push({ t: "code", lang: fence[1] || "", text: buf.join("\n") });
        continue;
      }
      const heading = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
      if (heading) {
        flushPara();
        blocks.push({ t: "heading", level: heading[1].length, children: parseInline(heading[2]) });
        i++;
        continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
        flushPara();
        blocks.push({ t: "rule" });
        i++;
        continue;
      }
      const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
      const number = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (bullet || number) {
        flushPara();
        const ordered = !!number;
        const items = [];
        const re = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/;
        while (i < lines.length) {
          const m = re.exec(lines[i]);
          if (m) {
            items.push(parseInline(m[1]));
            i++;
          } else if (/^\s{2,}\S/.test(lines[i]) && items.length) {
            const last = items[items.length - 1];
            last.push({ t: "text", v: " " }, ...parseInline(lines[i].trim()));
            i++;
          } else break;
        }
        blocks.push({ t: "list", ordered, items });
        continue;
      }
      if (/^\s*>\s?/.test(line)) {
        flushPara();
        const buf = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
        blocks.push({ t: "quote", children: parseInline(buf.join("\n")) });
        continue;
      }
      if (line.includes("|") && i + 1 < lines.length && isSeparatorRow(lines[i + 1])) {
        flushPara();
        const header = splitRow(line).map(parseInline);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(splitRow(lines[i++]).map(parseInline));
        blocks.push({ t: "table", header, rows });
        continue;
      }
      if (!line.trim()) {
        flushPara();
        i++;
        continue;
      }
      para.push(line);
      i++;
    }
    flushPara();
    return blocks;
  }

  // src/markdown.tsx
  function Inlines({ nodes }) {
    return /* @__PURE__ */ React.createElement(React.Fragment, null, nodes.map((n, i) => {
      if (n.t === "text") return /* @__PURE__ */ React.createElement(React.Fragment, { key: i }, n.v);
      if (n.t === "code")
        return /* @__PURE__ */ React.createElement("code", { key: i, className: "otel-code" }, n.v);
      if (n.t === "strong")
        return /* @__PURE__ */ React.createElement("strong", { key: i }, /* @__PURE__ */ React.createElement(Inlines, { nodes: n.children }));
      if (n.t === "em")
        return /* @__PURE__ */ React.createElement("em", { key: i }, /* @__PURE__ */ React.createElement(Inlines, { nodes: n.children }));
      return /* @__PURE__ */ React.createElement("a", { key: i, className: "otel-link", href: n.href, target: "_blank", rel: "noreferrer noopener" }, /* @__PURE__ */ React.createElement(Inlines, { nodes: n.children }));
    }));
  }
  function BlockView({ b }) {
    switch (b.t) {
      case "heading": {
        const Tag = `h${Math.min(6, b.level)}`;
        return /* @__PURE__ */ React.createElement(Tag, { className: `otel-md-h otel-md-h${Math.min(4, b.level)}` }, /* @__PURE__ */ React.createElement(Inlines, { nodes: b.children }));
      }
      case "paragraph":
        return /* @__PURE__ */ React.createElement("p", { className: "otel-md-p" }, /* @__PURE__ */ React.createElement(Inlines, { nodes: b.children }));
      case "code":
        return /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-md-code", "data-lang": b.lang || void 0 }, b.text);
      case "list": {
        const Tag = b.ordered ? "ol" : "ul";
        return /* @__PURE__ */ React.createElement(Tag, { className: b.ordered ? "otel-md-ol" : "otel-md-ul" }, b.items.map((it, i) => /* @__PURE__ */ React.createElement("li", { key: i }, /* @__PURE__ */ React.createElement(Inlines, { nodes: it }))));
      }
      case "quote":
        return /* @__PURE__ */ React.createElement("blockquote", { className: "otel-md-quote" }, /* @__PURE__ */ React.createElement(Inlines, { nodes: b.children }));
      case "table":
        return /* @__PURE__ */ React.createElement("div", { className: "otel-md-tablewrap" }, /* @__PURE__ */ React.createElement("table", { className: "otel-md-table" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", null, b.header.map((c, i) => /* @__PURE__ */ React.createElement("th", { key: i }, /* @__PURE__ */ React.createElement(Inlines, { nodes: c }))))), /* @__PURE__ */ React.createElement("tbody", null, b.rows.map((r, i) => /* @__PURE__ */ React.createElement("tr", { key: i }, r.map((c, j) => /* @__PURE__ */ React.createElement("td", { key: j }, /* @__PURE__ */ React.createElement(Inlines, { nodes: c }))))))));
      default:
        return /* @__PURE__ */ React.createElement("hr", { className: "otel-md-rule" });
    }
  }
  function Markdown({ text }) {
    const blocks = parseBlocks(text || "");
    return /* @__PURE__ */ React.createElement("div", { className: "otel-md" }, blocks.map((b, i) => /* @__PURE__ */ React.createElement(BlockView, { key: i, b })));
  }

  // src/values.ts
  function parseJsonish(raw) {
    if (typeof raw !== "string") return raw;
    const t = raw.trim();
    if (!(t.startsWith("[") || t.startsWith("{"))) return raw;
    try {
      return JSON.parse(t);
    } catch {
      return raw;
    }
  }
  function parsePyRepr(raw) {
    if (typeof raw !== "string") return raw;
    const t = raw.trim();
    if (!(t.startsWith("[") && t.endsWith("]") || t.startsWith("{") && t.endsWith("}"))) return raw;
    if (!t.includes("'")) return raw;
    try {
      return JSON.parse(
        t.replace(/'/g, '"').replace(/\bTrue\b/g, "true").replace(/\bFalse\b/g, "false").replace(/\bNone\b/g, "null")
      );
    } catch {
      return raw;
    }
  }
  function toolCallOf(tc) {
    var _a, _b, _c, _d, _e;
    if (!tc || typeof tc !== "object") return null;
    const fn = tc.function && typeof tc.function === "object" ? tc.function : tc;
    const name = (_a = fn.name) != null ? _a : tc.name;
    if (typeof name !== "string" || !name) return null;
    let args = (_e = (_d = (_c = (_b = fn.arguments) != null ? _b : tc.arguments) != null ? _c : tc.args) != null ? _d : tc.input) != null ? _e : null;
    if (typeof args === "string") args = parseJsonish(args);
    return { id: tc.id != null ? String(tc.id) : null, name, args };
  }
  function parseToolCalls(raw) {
    const v = parseJsonish(raw);
    if (!Array.isArray(v) || !v.length) return null;
    const calls = v.map(toolCallOf);
    return calls.every((c) => c !== null) ? calls : null;
  }
  function partsOfContent(c) {
    if (c == null || c === "") return [];
    if (typeof c === "string") return [{ type: "text", text: c }];
    if (Array.isArray(c)) {
      const out = [];
      for (const p of c) {
        if (typeof p === "string") out.push({ type: "text", text: p });
        else if (p && typeof p === "object" && typeof p.text === "string") out.push({ type: "text", text: p.text });
        else if (p && typeof p === "object" && (p.type === "tool_use" || p.type === "tool_call")) {
          const tc = toolCallOf(p);
          if (tc) out.push({ type: "tool_call", ...tc });
        } else out.push({ type: "other", label: p && p.type || "part", value: p });
      }
      return out;
    }
    return [{ type: "other", label: "content", value: c }];
  }
  function parseChat(raw) {
    var _a, _b, _c;
    const v = parseJsonish(raw);
    const list = Array.isArray(v) ? v : v && typeof v === "object" && (v.role || v["message.role"]) ? [v] : null;
    if (!list || !list.length) return null;
    const out = [];
    for (const m of list) {
      if (!m || typeof m !== "object") return null;
      const role = (_a = m.role) != null ? _a : m["message.role"];
      if (typeof role !== "string") return null;
      const parts = partsOfContent((_b = m.content) != null ? _b : m["message.content"]);
      const tcs = (_c = m.tool_calls) != null ? _c : m["message.tool_calls"];
      if (Array.isArray(tcs)) {
        for (const tc of tcs) {
          const call = toolCallOf(tc);
          if (call) parts.push({ type: "tool_call", ...call });
        }
      }
      out.push({
        role,
        parts,
        toolCallId: m.tool_call_id != null ? String(m.tool_call_id) : null,
        name: typeof m.name === "string" ? m.name : null
      });
    }
    return out;
  }
  var MD_CUES = /(^|\n)(#{1,6} |\s*[-*] |\s*\d+\. |```|> )|\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)/;
  function looksLikeCode(text) {
    const t = text.trim();
    return /^[[{]/.test(t) || /\\n.*\\n/.test(t);
  }
  function looksLikeMarkdown(text) {
    return typeof text === "string" && text.length > 0 && MD_CUES.test(text);
  }
  var COMMAND_KEYS = /* @__PURE__ */ new Set(["hermes.tool.command", "hermes.approval.command", "hermes.turn.tool_commands"]);
  var PATH_KEYS = /* @__PURE__ */ new Set(["hermes.skill.path", "hermes.tool.target", "hermes.turn.tool_targets", "code.file.path"]);
  var ID_KEYS = /* @__PURE__ */ new Set([
    "hermes.session_id",
    "session.id",
    "session_id",
    "gen_ai.conversation.id",
    "wandb.thread_id",
    "gen_ai.tool.call.id",
    "gen_ai.response.id",
    "hermes.session.previous_id",
    "hermes.subagent.child_session_id",
    "hermes.subagent.child_id",
    "hermes.subagent.parent_session_id",
    "hermes.subagent.parent_turn_id",
    "hermes.subagent.parent_id",
    "hermes.cron.job_id",
    "correlation.id"
  ]);
  var PROSE_KEYS = /* @__PURE__ */ new Set([
    "gen_ai.system_instructions",
    "hermes.subagent.goal",
    "hermes.subagent.summary",
    "hermes.approval.description",
    "llm.output.content",
    "error.message"
  ]);
  var LIST_KEYS = {
    "hermes.turn.tools": ",",
    "hermes.turn.tool_outcomes": ",",
    "hermes.turn.tool_targets": ",",
    "hermes.turn.skills": ",",
    "hermes.turn.tool_commands": "|",
    "hermes.approval.pattern_keys": ",",
    "gen_ai.request.stop_sequences": ","
  };
  function splitList(key, value) {
    if (typeof value !== "string") return Array.isArray(value) ? value.map(String) : null;
    const sep = LIST_KEYS[key];
    const py = parsePyRepr(value);
    if (Array.isArray(py)) return py.map(String);
    if (!sep) return null;
    return value.split(sep).map((s) => s.trim()).filter(Boolean);
  }
  function classify(key, raw) {
    if (raw == null || raw === "") return { kind: "empty", value: raw, text: "" };
    const text = typeof raw === "string" ? raw : JSON.stringify(raw);
    const k = key.toLowerCase();
    if (typeof raw === "boolean" || /^(true|false)$/i.test(text.trim())) {
      return { kind: "bool", value: /^true$/i.test(text.trim()) || raw === true, text };
    }
    if (COMMAND_KEYS.has(key) && key !== "hermes.turn.tool_commands") return { kind: "command", value: text, text };
    const list = splitList(key, raw);
    const isRepr = typeof raw === "string" && Array.isArray(parsePyRepr(raw));
    if (list && (list.length > 1 || key in LIST_KEYS || isRepr)) return { kind: "list", value: list, text };
    if (ID_KEYS.has(key)) return { kind: "id", value: text, text };
    if (PATH_KEYS.has(key)) return { kind: "path", value: text, text };
    if (/^https?:\/\/\S+$/.test(text.trim())) return { kind: "url", value: text.trim(), text };
    const num = typeof raw === "number" ? raw : /^-?\d+(\.\d+)?$/.test(text.trim()) ? Number(text) : null;
    if (num != null && Number.isFinite(num)) {
      if (/(_ms|\.ms|duration_ms|latency_ms)$/.test(k)) return { kind: "duration", value: num, text };
      if (/duration_s$/.test(k)) return { kind: "duration", value: num * 1e3, text };
      return { kind: "count", value: num, text };
    }
    const chat = parseChat(raw);
    if (chat) return { kind: "messages", value: chat, text };
    const calls = parseToolCalls(raw);
    if (calls) return { kind: "tool_calls", value: calls, text };
    const parsed = parseJsonish(raw);
    if (parsed && typeof parsed === "object") return { kind: "json", value: parsed, text };
    if (looksLikeCode(text)) return { kind: "code", value: text, text };
    if (PROSE_KEYS.has(key) || looksLikeMarkdown(text)) return { kind: "markdown", value: text, text };
    return { kind: "text", value: text, text };
  }
  function pretty(raw) {
    if (raw == null) return "";
    const v = parseJsonish(raw);
    return typeof v === "string" ? v : JSON.stringify(v, null, 2);
  }
  function fmtCount(n) {
    return Number.isInteger(n) ? n.toLocaleString("en-US") : String(n);
  }
  function splitToolResult(raw) {
    var _a, _b, _c, _d;
    const v = parseJsonish(raw);
    if (!v || typeof v !== "object" || Array.isArray(v)) return { output: typeof v === "string" ? v : null, rest: null, error: null };
    const out = (_d = (_c = (_b = (_a = v.output) != null ? _a : v.result) != null ? _b : v.content) != null ? _c : v.stdout) != null ? _d : null;
    const rest = {};
    for (const [k, val] of Object.entries(v)) {
      if (k === "output" || k === "result" || k === "content" || k === "stdout") continue;
      rest[k] = val;
    }
    const err = typeof v.error === "string" ? v.error : v.success === false ? "failed" : null;
    return {
      output: typeof out === "string" ? out : out == null ? null : JSON.stringify(out, null, 2),
      rest: Object.keys(rest).length ? rest : null,
      error: err
    };
  }
  function turnTools(a) {
    var _a, _b, _c, _d;
    const tools = splitList("hermes.turn.tools", a["hermes.turn.tools"]) || [];
    const outcomes = splitList("hermes.turn.tool_outcomes", a["hermes.turn.tool_outcomes"]) || [];
    const commands = splitList("hermes.turn.tool_commands", a["hermes.turn.tool_commands"]) || [];
    const targets = splitList("hermes.turn.tool_targets", a["hermes.turn.tool_targets"]) || [];
    const count = Number(a["hermes.turn.tool_count"]) || 0;
    const rows = Math.max(tools.length, outcomes.length, commands.length, targets.length, count);
    const out = [];
    for (let i = 0; i < rows; i++) {
      out.push({
        tool: (_a = tools[i]) != null ? _a : tools.length === 1 ? tools[0] : null,
        outcome: (_b = outcomes[i]) != null ? _b : outcomes.length === 1 ? outcomes[0] : null,
        command: (_c = commands[i]) != null ? _c : null,
        target: (_d = targets[i]) != null ? _d : null
      });
    }
    return out;
  }
  var MODE_KEY = "hermes_otel.attr_view";
  function readViewMode() {
    try {
      return localStorage.getItem(MODE_KEY) === "raw" ? "raw" : "structured";
    } catch {
      return "structured";
    }
  }
  function writeViewMode(m) {
    try {
      localStorage.setItem(MODE_KEY, m);
    } catch {
    }
  }

  // src/render.tsx
  var CLAMP_CHARS = 1600;
  var HUGE_CHARS = 2e5;
  var ViewModeContext = createContext ? createContext(null) : null;
  function useViewMode() {
    const ctx = ViewModeContext && useContext ? useContext(ViewModeContext) : null;
    const own = useState(readViewMode());
    if (ctx) return ctx;
    return [
      own[0],
      (m) => {
        writeViewMode(m);
        own[1](m);
      }
    ];
  }
  function LongText({ text, mono, markdown }) {
    const [open, setOpen] = useState(false);
    const long = text.length > CLAMP_CHARS;
    const huge = text.length > HUGE_CHARS;
    const shown = long && !open ? text.slice(0, CLAMP_CHARS) : text;
    return /* @__PURE__ */ React.createElement("div", { className: "otel-longtext" }, markdown ? /* @__PURE__ */ React.createElement(Markdown, { text: shown }) : /* @__PURE__ */ React.createElement("pre", { className: cn("otel-pre", mono ? "" : "otel-prose") }, shown), long ? /* @__PURE__ */ React.createElement("span", { className: "otel-more inline-flex items-center gap-2" }, /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-link", onClick: () => setOpen((o) => !o) }, open ? "show less" : `show all (${fmtCount(text.length)} chars${huge ? ", large" : ""})`)) : null);
  }
  function Scalar({ v }) {
    const mono = typeof v !== "string";
    return /* @__PURE__ */ React.createElement("span", { className: mono ? "font-mono" : "" }, String(v));
  }
  function KvTable({ value }) {
    const entries = Object.entries(value || {});
    if (!entries.length) return /* @__PURE__ */ React.createElement("span", { className: "otel-unset" }, "empty");
    return /* @__PURE__ */ React.createElement("dl", { className: "otel-attr-table otel-kv-table text-xs" }, entries.map(([k, v]) => /* @__PURE__ */ React.createElement(React.Fragment, { key: k }, /* @__PURE__ */ React.createElement("dt", { className: "text-muted-foreground" }, k), /* @__PURE__ */ React.createElement("dd", { className: "min-w-0 break-words" }, v && typeof v === "object" ? /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-pre-inline" }, JSON.stringify(v, null, 2)) : typeof v === "string" && v.includes("\n") ? /* @__PURE__ */ React.createElement(LongText, { text: v, mono: true }) : /* @__PURE__ */ React.createElement(Scalar, { v })))));
  }
  function Chips({ items, mono }) {
    return /* @__PURE__ */ React.createElement("span", { className: "otel-chips" }, items.map((s, i) => /* @__PURE__ */ React.createElement("span", { key: i, className: cn("otel-chip", mono ? "font-mono" : "") }, s)));
  }
  function ToolCallCard({ call }) {
    const args = call.args;
    return /* @__PURE__ */ React.createElement("div", { className: "otel-toolcall" }, /* @__PURE__ */ React.createElement("div", { className: "otel-toolcall-head" }, /* @__PURE__ */ React.createElement("span", { className: "otel-chip otel-chip-tool font-mono" }, call.name), call.id ? /* @__PURE__ */ React.createElement("span", { className: "font-mono text-[10px] text-muted-foreground", title: "tool call id" }, call.id) : null), args && typeof args === "object" && !Array.isArray(args) ? /* @__PURE__ */ React.createElement(KvTable, { value: args }) : args != null ? /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-pre-inline" }, typeof args === "string" ? args : JSON.stringify(args, null, 2)) : null);
  }
  function ToolCalls({ calls }) {
    return /* @__PURE__ */ React.createElement("div", { className: "otel-toolcalls" }, calls.map((c, i) => /* @__PURE__ */ React.createElement(ToolCallCard, { key: c.id || i, call: c })));
  }
  var ROLE_LABEL = { system: "system", user: "user", assistant: "assistant", tool: "tool result", developer: "developer" };
  function Chat({ messages }) {
    const [openSystem, setOpenSystem] = useState(false);
    return /* @__PURE__ */ React.createElement("div", { className: "otel-chat" }, messages.map((m, i) => {
      const role = m.role.toLowerCase();
      const text = m.parts.filter((p) => p.type === "text").map((p) => p.text).join("\n");
      const isSystem = role === "system" || role === "developer";
      const collapsed = isSystem && !openSystem && text.length > 400;
      return /* @__PURE__ */ React.createElement("div", { key: i, className: cn("otel-msg", `otel-msg-${role}`) }, /* @__PURE__ */ React.createElement("div", { className: "otel-msg-head" }, /* @__PURE__ */ React.createElement("span", { className: cn("otel-role", `otel-role-${role}`) }, ROLE_LABEL[role] || role), m.name ? /* @__PURE__ */ React.createElement("span", { className: "font-mono text-[10px] text-muted-foreground" }, m.name) : null, m.toolCallId ? /* @__PURE__ */ React.createElement("span", { className: "font-mono text-[10px] text-muted-foreground", title: "answers this tool call" }, "\u21B3 ", m.toolCallId) : null, collapsed ? /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-link text-[10px]", onClick: () => setOpenSystem(true) }, "show all (", fmtCount(text.length), " chars)") : null), m.parts.map((p, j) => {
        if (p.type === "text") {
          const body = collapsed ? p.text.slice(0, 400) + " \u2026" : p.text;
          if (role === "tool") return /* @__PURE__ */ React.createElement(ToolResultBody, { key: j, raw: body });
          return looksLikeMarkdown(body) || role === "assistant" ? /* @__PURE__ */ React.createElement(Markdown, { key: j, text: body }) : /* @__PURE__ */ React.createElement("pre", { key: j, className: "otel-pre otel-prose" }, body);
        }
        if (p.type === "tool_call") return /* @__PURE__ */ React.createElement(ToolCallCard, { key: j, call: p });
        return /* @__PURE__ */ React.createElement("div", { key: j, className: "text-[11px] text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", { className: "otel-chip" }, p.label), /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-pre-inline" }, JSON.stringify(p.value, null, 2)));
      }));
    }));
  }
  function ToolResultBody({ raw }) {
    const v = parseJsonish(raw);
    if (Array.isArray(v)) return /* @__PURE__ */ React.createElement("pre", { className: "otel-pre" }, JSON.stringify(v, null, 2));
    const { output, rest } = splitToolResult(raw);
    if (v && typeof v === "object") {
      return /* @__PURE__ */ React.createElement("div", { className: "otel-toolresult" }, output != null ? /* @__PURE__ */ React.createElement(LongText, { text: output, mono: !looksLikeMarkdown(output), markdown: looksLikeMarkdown(output) }) : null, rest ? /* @__PURE__ */ React.createElement(KvTable, { value: rest }) : null);
    }
    const text = String(v != null ? v : "");
    const md = looksLikeMarkdown(text) && !looksLikeCode(text);
    return /* @__PURE__ */ React.createElement(LongText, { text, mono: !md, markdown: md });
  }
  function ModeToggle({ mode, onChange }) {
    const Btn = ({ id, label }) => /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        "aria-pressed": mode === id,
        onClick: () => onChange(id),
        className: cn("otel-toggle otel-mode-btn", mode === id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground")
      },
      label
    );
    return /* @__PURE__ */ React.createElement("span", { className: "otel-mode", role: "group", "aria-label": "structured or raw" }, /* @__PURE__ */ React.createElement(Btn, { id: "structured", label: "structured" }), /* @__PURE__ */ React.createElement(Btn, { id: "raw", label: "raw" }));
  }
  function ValueView({ attrKey, value, label, onSessionClick }) {
    const [mode, change] = useViewMode();
    const c = classify(attrKey, value);
    const rich = c.kind === "messages" || c.kind === "tool_calls" || c.kind === "json" || c.kind === "markdown";
    const head = label || rich ? /* @__PURE__ */ React.createElement("div", { className: "otel-value-head" }, label ? /* @__PURE__ */ React.createElement("span", { className: "text-[11px] font-medium uppercase tracking-wide text-muted-foreground" }, label) : null, rich ? /* @__PURE__ */ React.createElement(ModeToggle, { mode, onChange: change }) : null) : null;
    let body;
    if (rich && mode === "raw") body = /* @__PURE__ */ React.createElement(LongText, { text: pretty(value), mono: true });
    else
      switch (c.kind) {
        case "empty":
          body = /* @__PURE__ */ React.createElement("span", { className: "otel-unset" }, "unset");
          break;
        case "messages":
          body = /* @__PURE__ */ React.createElement(Chat, { messages: c.value });
          break;
        case "tool_calls":
          body = /* @__PURE__ */ React.createElement(ToolCalls, { calls: c.value });
          break;
        case "json":
          body = Array.isArray(c.value) ? /* @__PURE__ */ React.createElement("pre", { className: "otel-pre" }, JSON.stringify(c.value, null, 2)) : /* @__PURE__ */ React.createElement(KvTable, { value: c.value });
          break;
        case "markdown":
          body = /* @__PURE__ */ React.createElement(LongText, { text: c.value, markdown: true });
          break;
        case "code":
          body = /* @__PURE__ */ React.createElement(LongText, { text: c.value, mono: true });
          break;
        case "command":
          body = /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-cmd" }, "$ ", c.value);
          break;
        case "list":
          body = /* @__PURE__ */ React.createElement(Chips, { items: c.value, mono: attrKey.includes("command") || attrKey.includes("target") });
          break;
        case "path":
          body = /* @__PURE__ */ React.createElement("span", { className: "font-mono break-all" }, c.value);
          break;
        case "url":
          body = /* @__PURE__ */ React.createElement("a", { className: "otel-link font-mono break-all", href: c.value, target: "_blank", rel: "noreferrer noopener" }, c.value);
          break;
        case "duration":
          body = /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtDurationMs(c.value));
          break;
        case "count":
          body = /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtCount(c.value));
          break;
        case "bool":
          body = /* @__PURE__ */ React.createElement("span", { className: cn("otel-chip", c.value ? "otel-chip-yes" : "otel-chip-no") }, c.value ? "yes" : "no");
          break;
        case "id":
          body = onSessionClick && /session|conversation|thread/.test(attrKey) ? /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-link font-mono", title: "show this session's turns", onClick: () => onSessionClick(c.value) }, c.value) : /* @__PURE__ */ React.createElement("span", { className: "font-mono break-all" }, c.value);
          break;
        default:
          body = c.text.length > 200 || c.text.includes("\n") ? /* @__PURE__ */ React.createElement(LongText, { text: c.text }) : /* @__PURE__ */ React.createElement("span", { className: "break-words" }, c.text);
      }
    const errorish = attrKey === "error.message";
    return /* @__PURE__ */ React.createElement("div", { className: cn("otel-value", errorish ? "otel-value-error" : "") }, head, body);
  }
  function Facts({ items }) {
    const shown = items.filter((f) => f.value != null && f.value !== "" && f.value !== false);
    if (!shown.length) return null;
    return /* @__PURE__ */ React.createElement("div", { className: "otel-factrow" }, shown.map((f, i) => /* @__PURE__ */ React.createElement("span", { key: i, className: "otel-fact" }, /* @__PURE__ */ React.createElement("span", { className: "otel-fact-label" }, f.label), /* @__PURE__ */ React.createElement("span", { className: cn("otel-fact-value", f.mono ? "font-mono" : "", f.tone ? `otel-tone-${f.tone}` : "") }, String(f.value)))));
  }
  function StatusBadge({ value }) {
    if (value == null || value === "") return null;
    const s = String(value).toLowerCase();
    const bad = /error|fail|denied|timed?_?out|cancel/.test(s);
    return /* @__PURE__ */ React.createElement(Badge, { variant: bad ? "destructive" : "secondary", className: "text-[10px]" }, String(value));
  }

  // src/logs-lib.ts
  var SEVERITY_OF = {
    FATAL: "ERROR",
    CRITICAL: "ERROR",
    ERROR: "ERROR",
    WARN: "WARN",
    WARNING: "WARN",
    INFO: "INFO",
    DEBUG: "DEBUG",
    TRACE: "DEBUG"
  };
  function severityOf(level) {
    return SEVERITY_OF[String(level || "").toUpperCase()] || "OTHER";
  }
  function severityCounts(rows) {
    const bySeverity = { ERROR: 0, WARN: 0, INFO: 0, DEBUG: 0, OTHER: 0 };
    let events = 0;
    for (const r of rows) {
      bySeverity[severityOf(r.level)] += 1;
      if (r.event_name) events += 1;
    }
    return { total: rows.length, events, bySeverity };
  }
  function timeBuckets(rows, n, startNs, endNs) {
    const times = rows.map((r) => Number(r.time_unix_nano || 0)).filter((t) => t > 0);
    if (!n || n < 1) return [];
    const lo = startNs != null ? startNs : times.length ? Math.min(...times) : 0;
    const hi = endNs != null ? endNs : times.length ? Math.max(...times) : 0;
    if (!(hi > lo)) {
      const one = { startNs: lo, total: 0, errors: 0, warns: 0 };
      for (const r of rows) {
        one.total += 1;
        const s = severityOf(r.level);
        if (s === "ERROR") one.errors += 1;
        else if (s === "WARN") one.warns += 1;
      }
      return [one];
    }
    const width = (hi - lo) / n;
    const out = Array.from({ length: n }, (_, i) => ({ startNs: Math.round(lo + i * width), total: 0, errors: 0, warns: 0 }));
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
  var GROUP_ORDER = ["event", "hermes", "gen_ai", "exception", "code", "other"];
  var GROUP_LABEL = {
    event: "Event",
    hermes: "Hermes",
    gen_ai: "GenAI",
    exception: "Exception",
    code: "Code location",
    other: "Other"
  };
  function groupLogAttributes(attrs) {
    const buckets = { event: [], hermes: [], gen_ai: [], exception: [], code: [], other: [] };
    let stacktrace = null;
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
      stacktrace
    };
  }
  function codeLocation(attrs) {
    var _a;
    const a = attrs || {};
    const file = a["code.file.path"] || a["code.filepath"];
    if (!file) return null;
    const line = (_a = a["code.line.number"]) != null ? _a : a["code.lineno"];
    const fn = a["code.function.name"] || a["code.function"];
    return `${String(file).split("/").slice(-2).join("/")}${line != null ? `:${line}` : ""}${fn ? ` (${fn})` : ""}`;
  }
  function contextWindow(timeUnixNano, windowS) {
    const centerS = Math.floor(Number(timeUnixNano || 0) / 1e9);
    return { startS: Math.max(0, centerS - windowS), endS: centerS + windowS + 1 };
  }
  function attributionHint(attrs) {
    const tier = (attrs || {})["hermes.log.attribution"];
    if (!tier) return null;
    const titles = {
      context: "a span was current on the logging thread",
      session_tag: "Hermes's own session tag on the record",
      single_session: "the one session with a turn in flight"
    };
    return { text: String(tier).replace("_", " "), title: titles[String(tier)] || String(tier) };
  }

  // src/params.ts
  var LIVE = "live";
  function withBackend(params, source) {
    if (source && source !== LIVE) params.set("backend", source);
    return params;
  }
  var DEFAULT_FILTERS = {
    lookback: 1,
    status: "",
    kind: "",
    tool: "",
    model: "",
    session: "",
    minDurationMs: "",
    text: "",
    traceId: "",
    q: "",
    service: "",
    rootsOnly: true
  };
  function isDefaultFilters(f) {
    return Object.keys(DEFAULT_FILTERS).every((k) => DEFAULT_FILTERS[k] === f[k]);
  }
  var LOOKBACKS = [
    { label: "15m", hours: 0.25 },
    { label: "1h", hours: 1 },
    { label: "6h", hours: 6 },
    { label: "24h", hours: 24 },
    { label: "3d", hours: 72 },
    { label: "7d", hours: 168 },
    { label: "30d", hours: 720 }
  ];
  function lookbackLabel(hours) {
    const l = LOOKBACKS.find((x) => x.hours === hours);
    if (l) return l.label;
    if (hours < 1) return `${Math.round(hours * 60)}m`;
    if (hours % 24 === 0) return `${hours / 24}d`;
    return `${hours}h`;
  }
  var KINDS = ["agent", "cron", "subagent", "tool", "llm", "api", "approval", "skill", "session", "other"];
  var KIND_PREFIX = {
    agent: "agent",
    cron: "cron",
    subagent: "subagent",
    tool: "tool.",
    llm: "llm.",
    api: "api.",
    approval: "approval",
    skill: "skill.",
    session: "session"
  };
  function liveParams(f, limit = 50, beforeNs) {
    const p = new URLSearchParams({ lookback_hours: String(f.lookback), limit: String(limit) });
    if (f.status) p.set("status", f.status);
    if (f.kind) p.set("kind", f.kind);
    if (f.tool.trim()) p.set("tool", f.tool.trim());
    if (f.model.trim()) p.set("model", f.model.trim());
    if (f.session.trim()) p.set("session", f.session.trim());
    if (Number(f.minDurationMs) > 0) p.set("min_duration_ms", String(Math.floor(Number(f.minDurationMs))));
    if (f.text.trim()) p.set("text", f.text.trim());
    if (f.traceId.trim()) p.set("trace_id", f.traceId.trim());
    if (beforeNs && /^\d+$/.test(beforeNs)) p.set("before_ns", beforeNs);
    return p;
  }
  function backendParams(f, source, limit = 50, beforeNs) {
    const p = withBackend(new URLSearchParams({ lookback_hours: String(f.lookback), limit: String(limit) }), source);
    const rootsOnly = f.rootsOnly && !f.kind && !f.tool.trim();
    p.set("roots_only", String(rootsOnly));
    if (f.status) p.set("status", f.status);
    if (f.kind && KIND_PREFIX[f.kind]) p.set("name_prefix", KIND_PREFIX[f.kind]);
    if (f.tool.trim()) p.set("tool", f.tool.trim());
    if (f.model.trim()) p.set("model", f.model.trim());
    if (f.session.trim()) p.set("session", f.session.trim());
    if (Number(f.minDurationMs) > 0) p.set("min_duration_ms", String(Math.floor(Number(f.minDurationMs))));
    if (f.text.trim()) p.set("free_text", f.text.trim());
    if (f.q.trim()) p.set("q", f.q.trim());
    if (f.service.trim()) p.set("service", f.service.trim());
    if (beforeNs && /^\d+$/.test(beforeNs)) p.set("before_ns", beforeNs);
    return p;
  }
  function traceFiltersFromNav(nav) {
    const lookback = Number(nav.lookback);
    const status = nav.status === "ok" || nav.status === "error" ? nav.status : "";
    return {
      lookback: lookback > 0 ? lookback : nav.session || nav.trace ? 168 : DEFAULT_FILTERS.lookback,
      status,
      kind: nav.kind && KINDS.includes(nav.kind) ? nav.kind : "",
      tool: nav.tool || "",
      model: nav.model || "",
      session: nav.session || "",
      minDurationMs: nav.mindur && /^\d+$/.test(nav.mindur) ? nav.mindur : "",
      text: nav.text || "",
      traceId: "",
      q: nav.q || "",
      service: nav.service || "",
      rootsOnly: nav.roots !== "0"
    };
  }
  function navFromTraceFilters(f) {
    return {
      status: f.status,
      kind: f.kind,
      tool: f.tool.trim(),
      model: f.model.trim(),
      session: f.session.trim(),
      mindur: Number(f.minDurationMs) > 0 ? String(Math.floor(Number(f.minDurationMs))) : "",
      text: f.text.trim(),
      q: f.q.trim(),
      service: f.service.trim(),
      roots: f.rootsOnly ? "" : "0",
      lookback: f.lookback !== DEFAULT_FILTERS.lookback ? String(f.lookback) : ""
    };
  }
  var FILTER_FIELD_OF = {
    lookback: "lookback",
    status: "status_error",
    kind: "name",
    tool: "tool",
    model: "model",
    session: "session",
    minDurationMs: "min_duration",
    text: "free_text",
    traceId: "trace_id",
    q: "raw",
    service: "service",
    rootsOnly: "roots_only"
  };
  function fieldSupport(support, field) {
    var _a;
    if (!support) return "unknown";
    const key = FILTER_FIELD_OF[field];
    const v = support[key];
    if (field === "status" && v === void 0) return (_a = support["status_ok"]) != null ? _a : "unknown";
    return v != null ? v : "unknown";
  }
  var DEFAULT_LOG_FILTERS = {
    minLevel: "0",
    logger: "",
    session: "",
    traceId: "",
    text: "",
    lookback: 1,
    eventsOnly: false,
    eventName: "",
    centerNs: "",
    windowS: 30
  };
  var LOG_PAGE_SIZES = [100, 200, 500, 1e3];
  var DEFAULT_LOG_PAGE = 200;
  var LOG_LEVELS = [
    { value: "0", label: "All levels", otel: 0 },
    { value: "10", label: "DEBUG+ (sev 5)", otel: 5 },
    { value: "20", label: "INFO+ (sev 9)", otel: 9 },
    { value: "30", label: "WARN+ (sev 13)", otel: 13 },
    { value: "40", label: "ERROR+ (sev 17)", otel: 17 }
  ];
  function logParams(f, source, limit, beforeNs) {
    const p = withBackend(new URLSearchParams({ lookback_hours: String(f.lookback), limit: String(limit) }), source);
    if (Number(f.minLevel) > 0) p.set("min_level", f.minLevel);
    if (f.logger.trim()) p.set("logger", f.logger.trim());
    if (f.session.trim()) p.set("session", f.session.trim());
    if (f.traceId.trim()) p.set("trace_id", f.traceId.trim());
    if (f.text.trim()) p.set("text", f.text.trim());
    if (f.eventName.trim()) p.set("event_name", f.eventName.trim());
    else if (f.eventsOnly) p.set("events_only", "1");
    if (f.centerNs && /^\d+$/.test(f.centerNs)) {
      const { startS, endS } = contextWindow(Number(f.centerNs), f.windowS);
      p.set("start_s", String(startS));
      p.set("end_s", String(endS));
    }
    if (beforeNs && /^\d+$/.test(beforeNs)) p.set("before_ns", beforeNs);
    return p;
  }
  function logFiltersFromNav(nav) {
    const lookback = Number(nav.lookback);
    return {
      minLevel: nav.level && /^\d+$/.test(nav.level) ? nav.level : DEFAULT_LOG_FILTERS.minLevel,
      logger: nav.logger || "",
      session: nav.session || "",
      traceId: nav.trace || "",
      text: nav.text || "",
      lookback: lookback > 0 ? lookback : DEFAULT_LOG_FILTERS.lookback,
      eventsOnly: nav.events === "1",
      eventName: nav.event || "",
      centerNs: nav.center && /^\d+$/.test(nav.center) ? nav.center : "",
      windowS: nav.win && /^\d+$/.test(nav.win) && Number(nav.win) > 0 ? Number(nav.win) : DEFAULT_LOG_FILTERS.windowS
    };
  }
  function navFromLogFilters(f) {
    return {
      level: Number(f.minLevel) > 0 ? f.minLevel : "",
      logger: f.logger.trim(),
      session: f.session.trim(),
      trace: f.traceId.trim(),
      text: f.text.trim(),
      lookback: f.lookback !== DEFAULT_LOG_FILTERS.lookback ? String(f.lookback) : "",
      events: f.eventsOnly ? "1" : "",
      event: f.eventName.trim(),
      center: f.centerNs || "",
      win: f.centerNs && f.windowS !== DEFAULT_LOG_FILTERS.windowS ? String(f.windowS) : ""
    };
  }
  function logPageSizeFromNav(size) {
    const n = Number(size);
    return LOG_PAGE_SIZES.includes(n) ? n : DEFAULT_LOG_PAGE;
  }
  function cursorsFromNav(before) {
    if (!before) return [];
    return before.split(",").filter((c) => /^\d+$/.test(c));
  }
  function navFromCursors(cursors) {
    return cursors.join(",");
  }
  var RANGES = [
    { label: "15m", hours: 0.25, bucket: 15 },
    { label: "1h", hours: 1, bucket: 60 },
    { label: "6h", hours: 6, bucket: 300 },
    { label: "24h", hours: 24, bucket: 900 },
    { label: "3d", hours: 72, bucket: 3600 },
    { label: "7d", hours: 168, bucket: 3600 * 3 },
    { label: "30d", hours: 720, bucket: 3600 * 12 }
  ];
  var AGGS = ["sum", "count", "avg", "max", "last"];
  function explorerFromNav(nav) {
    const range = RANGES.find((r) => String(r.hours) === nav.range) || RANGES[1];
    return { range, instrument: nav.inst || "", groupBy: nav.group || "", agg: nav.agg && AGGS.includes(nav.agg) ? nav.agg : "sum" };
  }
  function navFromExplorer(s) {
    return {
      range: s.range.hours !== RANGES[1].hours ? String(s.range.hours) : "",
      inst: s.instrument,
      group: s.groupBy,
      agg: s.agg !== "sum" ? s.agg : ""
    };
  }

  // src/nav.ts
  var SHARED_KEYS = ["tab", "source", "trace", "session"];
  var TAB_KEYS = {
    live: [],
    traces: ["view", "status", "kind", "tool", "model", "mindur", "text", "q", "service", "roots", "lookback", "before"],
    logs: ["level", "logger", "text", "lookback", "events", "event", "center", "win", "size", "before"],
    metrics: ["range", "inst", "group", "agg"],
    settings: []
  };
  var NAV_KEYS = Array.from(/* @__PURE__ */ new Set([...SHARED_KEYS, ...Object.values(TAB_KEYS).flat()]));
  var NAV_EVENT = "hermes_otel:navigate";
  function readNav(search) {
    const q = new URLSearchParams(search != null ? search : typeof window !== "undefined" ? window.location.search : "");
    const out = {};
    for (const k of NAV_KEYS) {
      const v = q.get(k);
      if (v) out[k] = v;
    }
    return out;
  }
  function navSearch(state, base) {
    const q = new URLSearchParams(base != null ? base : typeof window !== "undefined" ? window.location.search : "");
    for (const k of NAV_KEYS) {
      if (!(k in state)) continue;
      const v = state[k];
      if (v) q.set(k, v);
      else q.delete(k);
    }
    const s = q.toString();
    return s ? `?${s}` : "";
  }
  function clearOtherTabs(tab) {
    const out = {};
    for (const [t, keys] of Object.entries(TAB_KEYS)) {
      if (t === tab) continue;
      for (const k of keys) if (!(TAB_KEYS[tab] || []).includes(k)) out[k] = "";
    }
    return out;
  }
  function writeNav(patch) {
    var _a;
    if (typeof window === "undefined" || !((_a = window.history) == null ? void 0 : _a.replaceState)) return;
    try {
      const url = `${window.location.pathname}${navSearch(patch)}${window.location.hash}`;
      window.history.replaceState(window.history.state, "", url);
    } catch {
    }
  }
  function navigate(state) {
    const patch = state.tab ? { ...clearOtherTabs(state.tab), ...state } : state;
    writeNav(patch);
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(NAV_EVENT, { detail: patch }));
  }

  // src/source.ts
  var KEY = "hermes_otel.source";
  function backendUsable(b, need) {
    if (!b.supported) return false;
    if (need === "metrics") return b.metrics;
    if (need === "logs") return b.logs;
    return true;
  }
  function readSource(search) {
    const fromUrl = readNav(search).source;
    if (fromUrl) return fromUrl;
    try {
      return localStorage.getItem(KEY) || LIVE;
    } catch {
      return LIVE;
    }
  }
  function writeSource(v) {
    try {
      localStorage.setItem(KEY, v);
    } catch {
    }
    writeNav({ source: v === LIVE ? "" : v });
  }
  var LIVE_FILTERS = {
    service: "none",
    name: "server",
    model: "server",
    session: "server",
    tool: "server",
    min_duration: "server",
    status_error: "server",
    status_ok: "server",
    free_text: "server",
    trace_id: "server",
    raw: "none",
    roots_only: "none",
    lookback: "server"
  };
  var SourceContext = createContext ? createContext(null) : null;
  function useSourceState(enabled = true) {
    var _a;
    const [source, setSourceState] = useState(readSource());
    const [status, setStatus] = useState(null);
    const refresh = useCallback(() => {
      const p = withBackend(new URLSearchParams(), source);
      api("/status", p).then((st) => {
        setStatus(st);
        if (source !== LIVE && !(st.available || []).some((b) => b.name === source)) {
          setSourceState(LIVE);
          writeSource(LIVE);
        }
      }).catch(() => {
        setStatus({ configured: false, active: null, available: [], reason: "status unavailable" });
        if (source !== LIVE) {
          setSourceState(LIVE);
          writeSource(LIVE);
        }
      });
    }, [source]);
    useEffect(() => {
      if (enabled) refresh();
    }, [refresh, enabled]);
    const setSource = useCallback((s) => {
      writeSource(s);
      setSourceState(s);
    }, []);
    const isLive = source === LIVE;
    const entry = ((_a = status == null ? void 0 : status.available) == null ? void 0 : _a.find((b) => b.name === source)) || null;
    const filters = isLive ? LIVE_FILTERS : (entry == null ? void 0 : entry.filters) || (status == null ? void 0 : status.filters) || null;
    return { source, setSource, status, refresh, isLive, filters };
  }
  function SourceProvider({ children }) {
    const value = useSourceState();
    if (!SourceContext) return children;
    return React.createElement(SourceContext.Provider, { value }, children);
  }
  function useSource() {
    const ctx = SourceContext && useContext ? useContext(SourceContext) : null;
    const own = useSourceState(!ctx);
    return ctx || own;
  }

  // src/poll.ts
  function usePolling(fn, ms, enabled) {
    useEffect(() => {
      if (!enabled) return;
      const tick = () => {
        if (!document.hidden) fn();
      };
      const id = setInterval(tick, ms);
      const onVisible = () => {
        if (!document.hidden) fn();
      };
      document.addEventListener("visibilitychange", onVisible);
      return () => {
        clearInterval(id);
        document.removeEventListener("visibilitychange", onVisible);
      };
    }, [fn, ms, enabled]);
  }

  // src/sourceselect.tsx
  function unusableReason(b, need) {
    if (!b.supported) return `no dashboard adapter for type ${b.type}`;
    if (need === "metrics" && !b.metrics) return "does not serve metrics to the tab";
    if (need === "logs" && !b.logs) return "does not serve logs to the tab";
    return null;
  }
  function SourceSelect({
    source,
    onChange,
    status,
    need,
    className
  }) {
    const backends = (status == null ? void 0 : status.available) || [];
    const usable = backends.filter((b) => backendUsable(b, need));
    const unusable = backends.filter((b) => !backendUsable(b, need));
    const isLive = source === LIVE;
    return /* @__PURE__ */ React.createElement("div", { className: cn("inline-flex flex-wrap items-center gap-2", className) }, /* @__PURE__ */ React.createElement("label", { className: "inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground", htmlFor: "otel-source" }, isLive ? /* @__PURE__ */ React.createElement(IconZap, { size: 12, className: "otel-c-agent" }) : /* @__PURE__ */ React.createElement(IconDatabase, { size: 12 }), "source"), /* @__PURE__ */ React.createElement(Select, { id: "otel-source", value: source, onValueChange: onChange, className: "otel-w-56 h-8", "aria-label": "source" }, /* @__PURE__ */ React.createElement(SelectOption, { value: LIVE }, "Live (in-process)"), usable.map((b) => /* @__PURE__ */ React.createElement(SelectOption, { key: b.name, value: b.name }, b.type !== b.name ? `${b.name} (${b.type})` : b.name))), unusable.length ? /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-muted-foreground", title: unusable.map((b) => `${b.name}: ${unusableReason(b, need)}`).join("\n") }, unusable.length, " backend", unusable.length === 1 ? "" : "s", " cannot serve this tab") : null);
  }

  // src/logs.tsx
  var POLL_MS = 3e3;
  var LEVEL_CLASS = {
    ERROR: "otel-level-error",
    WARN: "otel-level-warn",
    INFO: "otel-level-info",
    DEBUG: "otel-level-debug",
    OTHER: "text-muted-foreground"
  };
  var RICH_KEY = /^(gen_ai\.(input|output)\.messages|gen_ai\.(prompt|completion)|gen_ai\.tool\.call\.(arguments|result)|input\.value|output\.value|hermes\.tool\.(command|output)|exception\.message)$/;
  function LogRow({
    l,
    absolute,
    wrap = true,
    expanded,
    onToggle,
    actions
  }) {
    const lvl = (l.level || "INFO").toUpperCase();
    const sev = severityOf(lvl);
    const ts = l.time_unix_nano || 0;
    const attrs = l.attributes || {};
    const hint = attributionHint(attrs);
    const edge = sev === "ERROR" ? "otel-row-error" : sev === "WARN" ? "otel-row-warn" : "";
    return /* @__PURE__ */ React.createElement("div", { className: cn("border-b border-border/60 last:border-b-0", expanded ? "bg-muted/30" : "otel-hoverable", edge) }, /* @__PURE__ */ React.createElement(
      Clickable,
      {
        onActivate: () => onToggle == null ? void 0 : onToggle(),
        toggle: true,
        open: !!expanded,
        label: `${expanded ? "collapse" : "expand"} log line`,
        className: "otel-row flex cursor-pointer items-start gap-2 px-3 py-1",
        title: expanded ? "collapse" : "expand attributes"
      },
      /* @__PURE__ */ React.createElement("span", { className: cn("shrink-0 text-muted-foreground/70", absolute ? "otel-w-40" : "otel-w-14"), title: ts ? fmtAbsTime(ts) : "" }, ts ? absolute ? fmtAbsTime(ts) : fmtTimeAgo(ts) : ""),
      /* @__PURE__ */ React.createElement("span", { className: cn("otel-w-12 shrink-0 font-semibold", LEVEL_CLASS[sev]) }, lvl),
      l.event_name ? /* @__PURE__ */ React.createElement(
        "button",
        {
          type: "button",
          className: "shrink-0 rounded border border-border px-1 font-mono text-[10px] text-muted-foreground",
          title: "structured event \u2014 click to filter to this event",
          onClick: (e) => {
            var _a;
            e.stopPropagation();
            (_a = actions == null ? void 0 : actions.onEvent) == null ? void 0 : _a.call(actions, String(l.event_name));
          }
        },
        l.event_name
      ) : null,
      l.logger && !l.event_name ? /* @__PURE__ */ React.createElement("span", { className: "otel-w-40 shrink-0 truncate text-muted-foreground", title: l.logger }, l.logger) : null,
      /* @__PURE__ */ React.createElement("span", { className: cn("min-w-0 flex-1 text-foreground/90", wrap ? "whitespace-pre-wrap break-words" : "truncate") }, l.body),
      hint ? /* @__PURE__ */ React.createElement("span", { className: "shrink-0 text-[10px] text-muted-foreground/70", title: `attributed by ${hint.title}` }, hint.text) : null,
      l.trace_id ? /* @__PURE__ */ React.createElement(
        "button",
        {
          type: "button",
          className: "otel-link shrink-0 font-mono text-[10px] text-muted-foreground/70",
          title: `open trace ${l.trace_id}`,
          onClick: (e) => {
            var _a;
            e.stopPropagation();
            (_a = actions == null ? void 0 : actions.onTrace) == null ? void 0 : _a.call(actions, String(l.trace_id));
          }
        },
        String(l.trace_id).slice(0, 8)
      ) : null
    ), expanded ? /* @__PURE__ */ React.createElement(LogDetail, { l, actions }) : null);
  }
  function LogDetail({ l, actions }) {
    const attrs = l.attributes || {};
    const { groups, stacktrace } = groupLogAttributes(attrs);
    const where = codeLocation(attrs);
    const ts = l.time_unix_nano || 0;
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-2 border-t border-border/60 px-3 py-2 font-mono text-[11px]" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", null, ts ? fmtAbsTime(ts) : ""), l.logger ? /* @__PURE__ */ React.createElement("span", { title: "logger / instrumentation scope" }, l.logger) : null, l.severity_number != null ? /* @__PURE__ */ React.createElement("span", { title: "OTel severity number" }, "sev ", l.severity_number) : null, where ? /* @__PURE__ */ React.createElement("span", { title: "code location" }, where) : null, l.session_id ? /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-link", title: "show this session's log lines", onClick: () => {
      var _a;
      return (_a = actions == null ? void 0 : actions.onSession) == null ? void 0 : _a.call(actions, String(l.session_id));
    } }, "session ", String(l.session_id)) : null, l.trace_id ? /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-link", title: "open the trace", onClick: () => {
      var _a;
      return (_a = actions == null ? void 0 : actions.onTrace) == null ? void 0 : _a.call(actions, String(l.trace_id));
    } }, "trace ", String(l.trace_id)) : null, l.span_id ? /* @__PURE__ */ React.createElement("span", { title: "span id" }, "span ", l.span_id) : null, /* @__PURE__ */ React.createElement("span", { className: "ml-auto flex items-center gap-2" }, ts && (actions == null ? void 0 : actions.onContext) ? /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-link", title: "show every line within 30 s of this one", onClick: () => {
      var _a;
      return (_a = actions.onContext) == null ? void 0 : _a.call(actions, l);
    } }, "\xB130 s around this line") : null, /* @__PURE__ */ React.createElement(CopyButton, { text: JSON.stringify(l, null, 2), label: "copy JSON" }))), l.body ? /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-raw whitespace-pre-wrap break-words" }, l.body) : null, groups.length ? /* @__PURE__ */ React.createElement("div", { className: "grid gap-x-4 gap-y-1 sm:grid-cols-2" }, groups.map((g) => /* @__PURE__ */ React.createElement("div", { key: g.label, className: "min-w-0" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-muted-foreground" }, g.label), /* @__PURE__ */ React.createElement("table", { className: "otel-kv-table w-full" }, /* @__PURE__ */ React.createElement("tbody", null, g.entries.map(([k, v]) => /* @__PURE__ */ React.createElement("tr", { key: k }, /* @__PURE__ */ React.createElement("td", { className: "otel-kv text-muted-foreground" }, k), /* @__PURE__ */ React.createElement("td", { className: "break-all text-foreground/90" }, RICH_KEY.test(k) || typeof v === "string" && v.length > 200 ? /* @__PURE__ */ React.createElement(ValueView, { attrKey: k, value: v }) : typeof v === "string" ? v : JSON.stringify(v))))))))) : /* @__PURE__ */ React.createElement("div", { className: "text-muted-foreground" }, "No attributes on this record."), stacktrace ? /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-raw max-h-80 overflow-auto whitespace-pre-wrap break-words" }, stacktrace) : null);
  }
  function SeveritySummary({ rows, buckets }) {
    const c = severityCounts(rows);
    const max = Math.max(1, ...buckets.map((b) => b.total));
    const w = 160;
    const h = 24;
    const bw = buckets.length ? w / buckets.length : w;
    return /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, c.total, " shown", c.events ? ` \xB7 ${c.events} event${c.events === 1 ? "" : "s"}` : ""), /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, /* @__PURE__ */ React.createElement("span", { className: c.bySeverity.ERROR ? "otel-level-error" : "" }, c.bySeverity.ERROR, " error"), " \xB7 ", /* @__PURE__ */ React.createElement("span", { className: c.bySeverity.WARN ? "otel-level-warn" : "" }, c.bySeverity.WARN, " warn"), " \xB7 ", c.bySeverity.INFO, " info", c.bySeverity.DEBUG ? ` \xB7 ${c.bySeverity.DEBUG} debug` : ""), buckets.length > 1 ? /* @__PURE__ */ React.createElement("svg", { width: w, height: h, role: "img", "aria-label": "lines per time bucket, oldest left", className: "shrink-0" }, /* @__PURE__ */ React.createElement("rect", { x: "0", y: h - 1, width: w, height: "1", className: "text-muted-foreground", fill: "currentColor", opacity: "0.3" }), buckets.map((b, i) => {
      const total = b.total / max * (h - 2);
      const bad = (b.errors + b.warns) / max * (h - 2);
      return /* @__PURE__ */ React.createElement("g", { key: i }, /* @__PURE__ */ React.createElement("title", null, `${b.total} line${b.total === 1 ? "" : "s"}${b.errors ? `, ${b.errors} error` : ""}${b.warns ? `, ${b.warns} warn` : ""}`), /* @__PURE__ */ React.createElement(
        "rect",
        {
          x: i * bw + 0.5,
          y: h - 1 - total,
          width: Math.max(1, bw - 1),
          height: total,
          className: "text-muted-foreground",
          fill: "currentColor",
          opacity: "0.35"
        }
      ), bad > 0 ? /* @__PURE__ */ React.createElement(
        "rect",
        {
          x: i * bw + 0.5,
          y: h - 1 - bad,
          width: Math.max(1, bw - 1),
          height: bad,
          fill: b.errors ? "var(--otel-level-error)" : "var(--otel-level-warn)"
        }
      ) : null);
    })) : null);
  }
  function LogsPage() {
    const { source, setSource, status, isLive } = useSource();
    const active = useActive();
    const initialNav = readNav();
    const [filters, setFilters] = useState(() => logFiltersFromNav(initialNav));
    const [applied, setApplied] = useState(() => logFiltersFromNav(initialNav));
    const [pageSize, setPageSize] = useState(() => logPageSizeFromNav(initialNav.size));
    const [cursors, setCursors] = useState(() => cursorsFromNav(initialNav.before));
    const [logs, setLogs] = useState([]);
    const [nextBefore, setNextBefore] = useState(null);
    const [hasMore, setHasMore] = useState(false);
    const [loggers, setLoggers] = useState([]);
    const [absolute, setAbsolute] = useState(false);
    const [paused, setPaused] = useState(false);
    const [follow, setFollow] = useState(false);
    const [wrap, setWrap] = useState(true);
    const [expanded, setExpanded] = useState(null);
    const listEnd = useRef(null);
    const listBox = useRef(null);
    const [tailing, setTailing] = useState(true);
    const [error, setError] = useState(null);
    const [loaded, setLoaded] = useState(false);
    const [live, setLive] = useState(null);
    const inflight = useRef(false);
    const before = cursors.length ? cursors[cursors.length - 1] : null;
    const onNewestPage = cursors.length === 0;
    const base = isLive ? "/live" : "";
    const entry = ((status == null ? void 0 : status.available) || []).find((b) => b.name === source) || null;
    const canQuery = isLive || !!((entry == null ? void 0 : entry.logs) || (status == null ? void 0 : status.active) === source && (status == null ? void 0 : status.logs));
    useEffect(() => {
      if (active) writeNav({ ...navFromLogFilters(applied), size: pageSize !== 200 ? String(pageSize) : "", before: navFromCursors(cursors) });
    }, [applied, pageSize, cursors, active]);
    useEffect(() => {
      const onNav = (e) => {
        const d = e.detail || {};
        if (d.tab !== "logs") return;
        if (d.source) setSource(d.source);
        const f = logFiltersFromNav({ ...readNav(), ...d });
        setFilters(f);
        setApplied(f);
        setCursors([]);
      };
      window.addEventListener("hermes_otel:navigate", onNav);
      return () => window.removeEventListener("hermes_otel:navigate", onNav);
    }, [setSource]);
    const load = useCallback(async () => {
      if (!canQuery || inflight.current) return;
      inflight.current = true;
      try {
        if (isLive) {
          const st = await api("/live/status");
          setLive(st && st.live !== false);
          if (!st || st.live === false) return;
        }
        const r = await api(`${base}/logs/search`, logParams(applied, source, pageSize, before));
        setLogs(dedupe(r.logs || []));
        setNextBefore(r.next_before_ns != null ? String(r.next_before_ns) : null);
        setHasMore(!!r.has_more);
        setError(null);
      } catch (e) {
        setError(e);
      } finally {
        inflight.current = false;
        setLoaded(true);
      }
    }, [applied, base, before, canQuery, isLive, pageSize, source]);
    useEffect(() => {
      load();
    }, [load]);
    usePolling(load, POLL_MS, active && !paused && canQuery && onNewestPage);
    useEffect(() => {
      if (!canQuery) return;
      const p = withBackend(new URLSearchParams({ lookback_hours: String(applied.lookback) }), source);
      api(`${base}/loggers`, p).then((r) => setLoggers(r.loggers || [])).catch(() => setLoggers([]));
    }, [base, canQuery, source, applied.lookback]);
    const set = (k, v) => setFilters({ ...filters, [k]: v });
    const apply = (f) => {
      setApplied(f);
      setCursors([]);
    };
    const older = () => {
      if (nextBefore) setCursors((c) => [...c, nextBefore]);
    };
    const newer = () => setCursors((c) => c.slice(0, -1));
    const newest = () => setCursors([]);
    const openTrace = (id) => navigate({ tab: "traces", source, trace: id, view: "turns" });
    const showSession = (id) => {
      const f = { ...DEFAULT_LOG_FILTERS, session: id, lookback: applied.lookback };
      setFilters(f);
      apply(f);
    };
    const showContext = (l) => {
      const f = { ...DEFAULT_LOG_FILTERS, centerNs: String(l.time_unix_nano || ""), windowS: 30, lookback: applied.lookback };
      setFilters(f);
      apply(f);
    };
    const showEvent = (name) => {
      const f = { ...filters, eventName: name, eventsOnly: true };
      setFilters(f);
      apply(f);
    };
    const actions = { onTrace: openTrace, onSession: showSession, onContext: showContext, onEvent: showEvent };
    const rowKey = (l, i) => {
      var _a, _b;
      return String((_b = (_a = l.id) != null ? _a : l.seq) != null ? _b : `${l.time_unix_nano || 0}:${l.logger || ""}:${i}`);
    };
    const ordered = useMemo(() => follow ? [...logs].reverse() : logs, [logs, follow]);
    const buckets = useMemo(() => timeBuckets(logs, 24), [logs]);
    useEffect(() => {
      var _a, _b;
      if (follow && onNewestPage && !paused && tailing) (_b = (_a = listEnd.current) == null ? void 0 : _a.scrollIntoView) == null ? void 0 : _b.call(_a, { block: "nearest" });
    }, [logs, follow, onNewestPage, paused, tailing]);
    useEffect(() => {
      if (!follow) return;
      const onScroll = () => {
        const el = document.scrollingElement || document.documentElement;
        const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        setTailing(atEnd);
      };
      window.addEventListener("scroll", onScroll, { passive: true });
      return () => window.removeEventListener("scroll", onScroll);
    }, [follow]);
    const permalink = typeof window !== "undefined" ? window.location.href : "";
    const oldestShown = logs.length ? logs[logs.length - 1].time_unix_nano || 0 : 0;
    const newestShown = logs.length ? logs[0].time_unix_nano || 0 : 0;
    const header = /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement(SourceSelect, { source, onChange: setSource, status, need: "logs" }), /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, logs.length, " line", logs.length === 1 ? "" : "s", cursors.length ? ` \xB7 page ${cursors.length + 1}` : "", onNewestPage ? paused ? " \xB7 paused" : " \xB7 following" : " \xB7 older page, not following")), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement(Toggle, { checked: absolute, onChange: setAbsolute, label: "absolute times", Switch: Checkbox }), /* @__PURE__ */ React.createElement(
      Toggle,
      {
        checked: follow,
        onChange: setFollow,
        label: "follow",
        title: "oldest first, newest at the bottom; scrolls with new lines until you scroll up",
        Switch: Checkbox
      }
    ), /* @__PURE__ */ React.createElement(Toggle, { checked: wrap, onChange: setWrap, label: "wrap", title: "wrap long lines", Switch: Checkbox }), /* @__PURE__ */ React.createElement(CopyButton, { text: permalink, label: "copy link" }), /* @__PURE__ */ React.createElement(
      Select,
      {
        value: String(pageSize),
        onValueChange: (v) => {
          setPageSize(Number(v));
          setCursors([]);
        },
        className: "h-8",
        "aria-label": "page size"
      },
      LOG_PAGE_SIZES.map((n) => /* @__PURE__ */ React.createElement(SelectOption, { key: n, value: String(n) }, `${n} / page`))
    ), /* @__PURE__ */ React.createElement(
      Button,
      {
        variant: "outline",
        size: "sm",
        onClick: () => setPaused((p) => !p),
        disabled: !onNewestPage,
        title: onNewestPage ? paused ? "resume following" : "stop following" : "an older page does not follow"
      },
      paused ? /* @__PURE__ */ React.createElement(IconPlay, { size: 12 }) : /* @__PURE__ */ React.createElement(IconPause, { size: 12 }),
      /* @__PURE__ */ React.createElement("span", { className: "ml-1" }, paused ? "Resume" : "Pause")
    )));
    const availableSources = ((status == null ? void 0 : status.available) || []).filter((b) => b.logs).map((b) => b.name);
    if (!canQuery)
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, header, /* @__PURE__ */ React.createElement(Empty, { title: "This source does not serve logs" }, "Pick the Live source", availableSources.length ? ` or one of: ${availableSources.join(", ")}` : ", or configure a backend whose adapter serves logs", "."));
    const pager = /* @__PURE__ */ React.createElement(
      Pager,
      {
        page: cursors.length + 1,
        hasMore: !!hasMore && !!nextBefore,
        onNewest: newest,
        onNewer: newer,
        onOlder: older,
        range: logs.length ? `${fmtAbsTime(oldestShown)} \u2192 ${fmtAbsTime(newestShown)}` : ""
      }
    );
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, header, /* @__PURE__ */ React.createElement(
      "form",
      {
        className: "otel-search-grid",
        onSubmit: (e) => {
          e.preventDefault();
          apply(filters);
        }
      },
      /* @__PURE__ */ React.createElement(Select, { value: filters.minLevel, onValueChange: (v) => set("minLevel", v), className: "h-8", "aria-label": "minimum level" }, LOG_LEVELS.map((l) => /* @__PURE__ */ React.createElement(SelectOption, { key: l.value, value: l.value }, l.label))),
      /* @__PURE__ */ React.createElement(Select, { value: filters.logger, onValueChange: (v) => set("logger", v), className: "h-8", "aria-label": "logger" }, /* @__PURE__ */ React.createElement(SelectOption, { value: "" }, "Any logger"), filters.logger && !loggers.some((l) => l.logger === filters.logger) ? /* @__PURE__ */ React.createElement(SelectOption, { value: filters.logger }, filters.logger) : null, loggers.map((l) => /* @__PURE__ */ React.createElement(SelectOption, { key: l.logger, value: l.logger }, `${l.logger} (${l.count})`))),
      /* @__PURE__ */ React.createElement(Input, { className: "h-8", placeholder: "session id", value: filters.session, onChange: (e) => set("session", e.target.value), "aria-label": "session id" }),
      /* @__PURE__ */ React.createElement(Input, { className: "h-8", placeholder: "trace id", value: filters.traceId, onChange: (e) => set("traceId", e.target.value), "aria-label": "trace id" }),
      /* @__PURE__ */ React.createElement(Input, { className: "h-8", placeholder: "text\u2026", value: filters.text, onChange: (e) => set("text", e.target.value), "aria-label": "text" }),
      /* @__PURE__ */ React.createElement(
        "label",
        {
          className: "inline-flex h-8 cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground",
          title: "only hermes.* / GenAI events (logs.events.enabled)"
        },
        /* @__PURE__ */ React.createElement(
          "input",
          {
            type: "checkbox",
            checked: filters.eventsOnly || !!filters.eventName,
            onChange: (e) => setFilters({ ...filters, eventsOnly: e.target.checked, eventName: e.target.checked ? filters.eventName : "" })
          }
        ),
        "events only"
      ),
      /* @__PURE__ */ React.createElement(
        Input,
        {
          className: "h-8 font-mono",
          placeholder: "event name\u2026",
          value: filters.eventName,
          onChange: (e) => set("eventName", e.target.value),
          title: "one structured event, e.g. hermes.tool.call",
          "aria-label": "event name"
        }
      ),
      /* @__PURE__ */ React.createElement(Select, { value: String(filters.lookback), onValueChange: (v) => set("lookback", Number(v)), className: "h-8", "aria-label": "lookback" }, LOOKBACKS.map((l) => /* @__PURE__ */ React.createElement(SelectOption, { key: l.hours, value: String(l.hours) }, l.label))),
      /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement(Button, { type: "submit", size: "sm" }, "Search"), /* @__PURE__ */ React.createElement(
        Button,
        {
          type: "button",
          variant: "outline",
          size: "sm",
          onClick: () => {
            setFilters(DEFAULT_LOG_FILTERS);
            apply(DEFAULT_LOG_FILTERS);
          }
        },
        "Clear"
      ))
    ), error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error, prefix: "Logs" }) : null, isLive && live === false ? /* @__PURE__ */ React.createElement(Empty, { title: "Live store unavailable" }, "Set ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "dashboard_live: true"), " and ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "logs.capture: true"), " (or", " ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "logs.events.enabled: true"), "), then run a turn.") : logs.length === 0 && loaded && !error ? /* @__PURE__ */ React.createElement(Empty, { title: onNewestPage ? "No log lines" : "No older lines" }, !onNewestPage ? /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: newer }, "\u2190 Back to the newer page") : isLive ? /* @__PURE__ */ React.createElement(React.Fragment, null, "Set ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "logs.capture: true"), " or ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "logs.events.enabled: true"), " in the plugin config and run a turn \u2014 the agent's log lines and events stream here. Lines written while a turn is in flight carry its trace and session id; click a line for its attributes.") : "Nothing matched in this window.") : logs.length === 0 && error ? null : /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement(SeveritySummary, { rows: logs, buckets }), applied.centerNs ? /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, "\xB1", applied.windowS, " s around ", fmtAbsTime(Number(applied.centerNs)), " ", /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        className: "otel-link",
        onClick: () => {
          const f = { ...applied, centerNs: "" };
          setFilters(f);
          apply(f);
        }
      },
      "clear"
    )) : null), pager, /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg overflow-hidden border border-border font-mono text-xs", ref: listBox }, ordered.map((l, i) => {
      const k = rowKey(l, i);
      return /* @__PURE__ */ React.createElement(
        LogRow,
        {
          key: k,
          l,
          absolute,
          wrap,
          expanded: expanded === k,
          onToggle: () => setExpanded(expanded === k ? null : k),
          actions
        }
      );
    }), /* @__PURE__ */ React.createElement("div", { ref: listEnd })), follow && !tailing && onNewestPage ? /* @__PURE__ */ React.createElement("div", { className: "flex justify-center" }, /* @__PURE__ */ React.createElement(
      Button,
      {
        variant: "outline",
        size: "sm",
        onClick: () => {
          var _a, _b;
          setTailing(true);
          (_b = (_a = listEnd.current) == null ? void 0 : _a.scrollIntoView) == null ? void 0 : _b.call(_a, { block: "nearest" });
        }
      },
      "\u2193 Jump to newest"
    )) : null, pager));
  }
  function dedupe(rows) {
    const seen = /* @__PURE__ */ new Set();
    const out = [];
    for (const r of rows) {
      const k = r.id != null ? `id:${r.id}` : r.seq != null ? `seq:${r.seq}` : `${r.time_unix_nano || 0}|${r.logger || ""}|${r.body || ""}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(r);
    }
    return out;
  }

  // src/detail.tsx
  function Fact({ label, children }) {
    if (children == null || children === "" || children === false) return null;
    return /* @__PURE__ */ React.createElement("div", { className: "min-w-0" }, /* @__PURE__ */ React.createElement("div", { className: "text-[10px] uppercase tracking-wide text-muted-foreground" }, label), /* @__PURE__ */ React.createElement("div", { className: "truncate text-sm text-foreground" }, children));
  }
  function TraceHeader({
    title,
    traceId,
    service,
    durationMs,
    rootAttrs,
    spans,
    error,
    partial,
    truncated,
    spanCount,
    uiUrl,
    uiLabel,
    source,
    onBack
  }) {
    var _a, _b;
    const f = headerFacts(rootAttrs, spans || []);
    const tokens = f.totalTokens != null ? `${fmtTokens(f.totalTokens)}${f.inputTokens != null || f.outputTokens != null ? ` (in ${fmtTokens((_a = f.inputTokens) != null ? _a : 0)} \xB7 out ${fmtTokens((_b = f.outputTokens) != null ? _b : 0)}${f.reasoningTokens ? ` \xB7 reasoning ${fmtTokens(f.reasoningTokens)}` : ""}${f.cacheReadTokens ? ` \xB7 cache read ${fmtTokens(f.cacheReadTokens)}` : ""})` : ""}` : null;
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-start justify-between gap-3" }, /* @__PURE__ */ React.createElement("div", { className: "min-w-0 space-y-1" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: "text-lg font-semibold uppercase tracking-tight" }, title), error ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "text-[10px]" }, "error") : null, partial ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]", title: "the root span has not finished yet; totals are provisional" }, "in progress") : null, truncated ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]", title: `the backend returned the first spans only${spanCount ? ` of ${spanCount}` : ""}` }, "truncated") : null, f.platform ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]" }, f.platform) : null, f.turn != null ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]" }, "turn ", f.turn) : null), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2 text-xs text-muted-foreground" }, service ? /* @__PURE__ */ React.createElement("span", null, service) : null, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, traceId), /* @__PURE__ */ React.createElement(CopyButton, { text: traceId, label: "copy id" }), uiUrl ? /* @__PURE__ */ React.createElement("a", { className: "otel-link", href: uiUrl, target: "_blank", rel: "noreferrer" }, "open in ", uiLabel || "backend", " \u2197") : null)), /* @__PURE__ */ React.createElement(Button, { variant: "ghost", size: "sm", onClick: onBack }, "\u2190 Back")), /* @__PURE__ */ React.createElement("div", { className: "otel-facts-grid" }, /* @__PURE__ */ React.createElement(Fact, { label: "duration" }, fmtDurationMs(durationMs)), /* @__PURE__ */ React.createElement(Fact, { label: "model" }, f.requestModel ? /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, f.requestModel) : null, f.responseModel ? /* @__PURE__ */ React.createElement("span", { className: "ml-1 text-xs text-muted-foreground", title: "the model named in the response, when it differs from the request" }, "(served: ", f.responseModel, ")") : null), /* @__PURE__ */ React.createElement(Fact, { label: "tokens" }, tokens), /* @__PURE__ */ React.createElement(Fact, { label: "cost" }, f.cost != null ? /* @__PURE__ */ React.createElement("span", { className: "otel-c-cost" }, fmtCostExact(f.cost)) : /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, "no pricing data")), /* @__PURE__ */ React.createElement(Fact, { label: "tools" }, f.tools.length ? f.tools.join(", ") : null), /* @__PURE__ */ React.createElement(Fact, { label: "outcome" }, f.finalStatus || f.exitReason ? `${f.finalStatus || ""}${f.finalStatus && f.exitReason ? " \xB7 " : ""}${f.exitReason || ""}` : null), /* @__PURE__ */ React.createElement(Fact, { label: "session" }, f.session ? /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        className: "otel-link font-mono",
        title: "show this session's turns",
        onClick: () => navigate({ tab: "traces", source, view: "sessions", session: String(f.session), trace: "" })
      },
      f.session
    ) : null)));
  }
  function Block2({ label, children }) {
    if (children == null) return null;
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-1" }, /* @__PURE__ */ React.createElement(MiniLabel, null, label), children);
  }
  var first = (a, ...keys) => {
    for (const k of keys) if (a[k] != null && a[k] !== "") return a[k];
    return null;
  };
  function Attr({ a, label, keys, source }) {
    const key = keys.find((k) => a[k] != null && a[k] !== "");
    if (!key) return null;
    return /* @__PURE__ */ React.createElement(
      ValueView,
      {
        attrKey: key,
        value: a[key],
        label,
        onSessionClick: source ? (id) => navigate({ tab: "traces", source, view: "sessions", session: id, trace: "" }) : void 0
      }
    );
  }
  function SpanSummary({ span, source }) {
    var _a, _b, _c, _d, _e, _f;
    const a = span._attrs || {};
    const kind = kindOf(span.name, a);
    const err = (_a = a["error.message"]) != null ? _a : a["exception.message"];
    const errorBlock = err ? /* @__PURE__ */ React.createElement(ValueView, { attrKey: "error.message", value: err, label: a["error.type"] ? `error \xB7 ${a["error.type"]}` : "error" }) : null;
    if (kind === "tool") {
      const truncated = String(a["hermes.preview.output.truncated"]) === "true";
      const origChars = Number(a["hermes.preview.output.original_chars"]);
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2 text-xs" }, first(a, "tool.name", "gen_ai.tool.name") ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "font-mono text-[10px]" }, String(first(a, "tool.name", "gen_ai.tool.name"))) : null, /* @__PURE__ */ React.createElement(StatusBadge, { value: a["hermes.tool.outcome"] || a["status"] }), a["hermes.tool.blocked_by"] ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "text-[10px]" }, "blocked by ", String(a["hermes.tool.blocked_by"])) : null, a["hermes.tool.decided_by"] ? /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, "decided by ", String(a["hermes.tool.decided_by"])) : null), /* @__PURE__ */ React.createElement(
        Facts,
        {
          items: [
            { label: "target", value: a["hermes.tool.target"], mono: true },
            { label: "call id", value: a["gen_ai.tool.call.id"], mono: true },
            {
              label: "cpu avg / peak",
              value: a["hermes.tool.cpu.utilization.avg"] != null ? `${a["hermes.tool.cpu.utilization.avg"]} / ${(_b = a["hermes.tool.cpu.utilization.peak"]) != null ? _b : "?"}` : null
            },
            {
              label: "gpu avg / peak",
              value: a["hermes.tool.gpu.utilization.avg"] != null ? `${a["hermes.tool.gpu.utilization.avg"]} / ${(_c = a["hermes.tool.gpu.utilization.peak"]) != null ? _c : "?"}` : null
            }
          ]
        }
      ), errorBlock, /* @__PURE__ */ React.createElement(Attr, { a, label: "command", keys: ["hermes.tool.command"] }), /* @__PURE__ */ React.createElement(Attr, { a, label: "arguments", keys: ["input.value", "gen_ai.tool.call.arguments"] }), /* @__PURE__ */ React.createElement(
        Attr,
        {
          a,
          label: truncated ? `result \xB7 preview of ${Number.isFinite(origChars) ? fmtCount(origChars) : "?"} chars` : "result",
          keys: ["output.value", "gen_ai.tool.call.result"]
        }
      ));
    }
    if (kind === "llm" || kind === "api") {
      const f = headerFacts(a);
      const http = first(a, "http.response.status_code", "gen_ai.response.status_code");
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement(
        Facts,
        {
          items: [
            { label: "model", value: f.requestModel, mono: true },
            { label: "served by", value: f.responseModel, mono: true },
            {
              label: "tokens",
              value: f.totalTokens != null ? `${fmtTokens(f.totalTokens)}${f.inputTokens != null ? ` (in ${fmtTokens(f.inputTokens)} \xB7 out ${fmtTokens((_d = f.outputTokens) != null ? _d : 0)}${f.reasoningTokens ? ` \xB7 reasoning ${fmtTokens(f.reasoningTokens)}` : ""}${f.cacheReadTokens ? ` \xB7 cache ${fmtTokens(f.cacheReadTokens)}` : ""})` : ""}` : null
            },
            { label: "cost", value: f.cost != null ? fmtCostExact(f.cost) : null },
            { label: "finish", value: first(a, "llm.response.finish_reason", "gen_ai.response.finish_reasons") },
            { label: "latency", value: a["llm.response.duration_ms"] != null ? fmtDurationMs(Number(a["llm.response.duration_ms"])) : null },
            { label: "messages", value: a["llm.request.message_count"] },
            { label: "mode", value: a["llm.api_mode"] },
            { label: "tool calls", value: a["llm.response.tool_calls"] },
            { label: "http", value: http, tone: http != null && Number(http) >= 400 ? "bad" : void 0 },
            {
              label: "retries",
              value: a["hermes.retry.count"] != null ? `${a["hermes.retry.count"]}${a["hermes.max_retries"] != null ? ` of ${a["hermes.max_retries"]}` : ""}` : null
            }
          ]
        }
      ), errorBlock, /* @__PURE__ */ React.createElement(Attr, { a, label: kind === "llm" ? "input" : "prompt", keys: ["llm.input_messages", "input.value", "gen_ai.input.messages"] }), /* @__PURE__ */ React.createElement(Attr, { a, label: "response", keys: ["llm.output.content", "output.value", "gen_ai.output.messages"] }), a["gen_ai.system_instructions"] && !a["input.value"] ? /* @__PURE__ */ React.createElement(Attr, { a, label: "system instructions", keys: ["gen_ai.system_instructions"] }) : null);
    }
    if (kind === "agent" || kind === "session") {
      const f = headerFacts(a);
      const tools = turnTools(a);
      const skills = splitList("hermes.turn.skills", a["hermes.turn.skills"]);
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2 text-xs" }, /* @__PURE__ */ React.createElement(StatusBadge, { value: a["hermes.turn.final_status"] }), a["hermes.session.kind"] ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]" }, String(a["hermes.session.kind"])) : null, String(a["hermes.session.is_subagent"]) === "true" ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]" }, "sub-agent") : null, String(a["hermes.session.interrupted"]) === "true" ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "text-[10px]" }, "interrupted") : null, String(a["hermes.session.failed"]) === "true" ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "text-[10px]" }, "failed") : null, String(a["hermes.session.synthesized"]) === "true" ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]", title: "root recreated by the plugin after a restart" }, "synthesized") : null), /* @__PURE__ */ React.createElement(
        Facts,
        {
          items: [
            { label: "exit", value: a["hermes.turn.exit_reason"] },
            { label: "api calls", value: a["hermes.turn.api_call_count"] },
            { label: "tokens", value: f.totalTokens != null ? fmtTokens(f.totalTokens) : null },
            { label: "cost", value: f.cost != null ? fmtCostExact(f.cost) : f.costUnknown ? "no pricing data" : null },
            { label: "platform", value: a["hermes.platform"] },
            { label: "profile", value: a["hermes.profile"] },
            { label: "turn", value: a["hermes.turn.number"] },
            { label: "previous session", value: a["hermes.session.previous_id"], mono: true }
          ]
        }
      ), errorBlock, /* @__PURE__ */ React.createElement(Attr, { a, label: "user message", keys: ["input.value", "gen_ai.input.messages"], source }), /* @__PURE__ */ React.createElement(Attr, { a, label: "final response", keys: ["output.value", "gen_ai.output.messages"] }), tools.length ? /* @__PURE__ */ React.createElement(Block2, { label: `tools \xB7 ${tools.length}` }, /* @__PURE__ */ React.createElement("dl", { className: "otel-attr-table otel-kv-table text-xs" }, tools.map((t, i) => /* @__PURE__ */ React.createElement(React.Fragment, { key: i }, /* @__PURE__ */ React.createElement("dt", { className: "font-mono" }, t.tool || "\xB7"), /* @__PURE__ */ React.createElement("dd", { className: "min-w-0 break-words" }, /* @__PURE__ */ React.createElement(StatusBadge, { value: t.outcome }), t.command ? /* @__PURE__ */ React.createElement("code", { className: "otel-code ml-1" }, t.command) : null, t.target ? /* @__PURE__ */ React.createElement("span", { className: "ml-1 font-mono text-muted-foreground" }, t.target) : null))))) : null, skills && skills.length ? /* @__PURE__ */ React.createElement(Block2, { label: "skills" }, /* @__PURE__ */ React.createElement(Chips, { items: skills, mono: true })) : null);
    }
    if (kind === "skill") {
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2 text-xs" }, first(a, "hermes.skill.name", "gen_ai.skill.name") ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "font-mono text-[10px]" }, String(first(a, "hermes.skill.name", "gen_ai.skill.name"))) : null, /* @__PURE__ */ React.createElement(StatusBadge, { value: a["hermes.skill.result_status"] }), a["hermes.skill.source"] ? /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, "loaded via ", String(a["hermes.skill.source"])) : null), /* @__PURE__ */ React.createElement(Facts, { items: [{ label: "path", value: a["hermes.skill.path"], mono: true }] }), errorBlock);
    }
    if (kind === "approval") {
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2 text-xs" }, a["hermes.approval.choice"] ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]" }, "choice: ", String(a["hermes.approval.choice"])) : null, a["hermes.approval.granted"] != null ? /* @__PURE__ */ React.createElement(Badge, { variant: String(a["hermes.approval.granted"]) === "true" ? "secondary" : "destructive", className: "text-[10px]" }, String(a["hermes.approval.granted"]) === "true" ? "granted" : "denied") : null, String(a["hermes.approval.timed_out"]) === "true" ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "text-[10px]" }, "timed out") : null), /* @__PURE__ */ React.createElement(
        Facts,
        {
          items: [
            { label: "decided by", value: a["hermes.approval.decided_by"] },
            { label: "surface", value: a["hermes.approval.surface"] },
            { label: "waited", value: a["hermes.approval.duration_ms"] != null ? fmtDurationMs(Number(a["hermes.approval.duration_ms"])) : null },
            { label: "pattern", value: first(a, "hermes.approval.pattern_key", "hermes.approval.pattern_keys"), mono: true }
          ]
        }
      ), /* @__PURE__ */ React.createElement(Attr, { a, label: "command", keys: ["hermes.approval.command"] }), /* @__PURE__ */ React.createElement(Attr, { a, label: "description", keys: ["hermes.approval.description"] }));
    }
    if (kind === "subagent") {
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2 text-xs" }, a["hermes.subagent.role"] ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]" }, String(a["hermes.subagent.role"])) : null, /* @__PURE__ */ React.createElement(StatusBadge, { value: a["hermes.subagent.status"] })), /* @__PURE__ */ React.createElement(
        Facts,
        {
          items: [
            { label: "duration", value: a["hermes.subagent.duration_ms"] != null ? fmtDurationMs(Number(a["hermes.subagent.duration_ms"])) : null },
            { label: "child session", value: first(a, "hermes.subagent.child_session_id", "hermes.subagent.child_id"), mono: true },
            { label: "parent session", value: first(a, "hermes.subagent.parent_session_id", "hermes.subagent.parent_id"), mono: true }
          ]
        }
      ), errorBlock, /* @__PURE__ */ React.createElement(Attr, { a, label: "goal", keys: ["hermes.subagent.goal", "input.value"] }), /* @__PURE__ */ React.createElement(Attr, { a, label: "summary", keys: ["hermes.subagent.summary", "output.value"] }));
    }
    if (kind === "cron") {
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement(Facts, { items: [{ label: "job", value: a["hermes.cron.job_id"], mono: true }] }), errorBlock, /* @__PURE__ */ React.createElement(Attr, { a, label: "input", keys: ["input.value"] }), /* @__PURE__ */ React.createElement(Attr, { a, label: "output", keys: ["output.value"] }));
    }
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement(
      Facts,
      {
        items: [
          { label: "span", value: span.name, mono: true },
          { label: "duration", value: fmtDurationMs(span.durationMs) },
          { label: "status", value: ((_e = span.status) == null ? void 0 : _e.code) === 2 ? "error" : ((_f = span.status) == null ? void 0 : _f.code) === 1 ? "ok" : null },
          { label: "kind", value: a["openinference.span.kind"] || a["hermes.span_kind"] || null }
        ]
      }
    ), errorBlock, /* @__PURE__ */ React.createElement(Attr, { a, label: "input", keys: ["input.value"] }), /* @__PURE__ */ React.createElement(Attr, { a, label: "output", keys: ["output.value"] }));
  }
  var CONTENT_KEYS = /* @__PURE__ */ new Set([
    "input.value",
    "output.value",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "llm.input_messages",
    "llm.output.content",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "gen_ai.system_instructions",
    "hermes.conversation.history"
  ]);
  function AttrGroups({ attrs, source }) {
    const groups = groupAttrs(attrs || {});
    if (!groups.length) return /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "(no attributes)");
    const onSession = source ? (id) => navigate({ tab: "traces", source, view: "sessions", session: id, trace: "" }) : void 0;
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, groups.map((g) => /* @__PURE__ */ React.createElement("div", { key: g.prefix }, /* @__PURE__ */ React.createElement(MiniLabel, null, g.prefix), /* @__PURE__ */ React.createElement("dl", { className: "otel-attr-table text-xs" }, g.entries.map((e) => /* @__PURE__ */ React.createElement(React.Fragment, { key: e.key }, /* @__PURE__ */ React.createElement("dt", { className: "text-muted-foreground", title: e.aliases.length ? `also: ${e.aliases.join(", ")}` : "" }, e.key, e.aliases.length ? /* @__PURE__ */ React.createElement("span", { className: "ml-1 text-[10px] text-muted-foreground/60" }, "+", e.aliases.length) : null), /* @__PURE__ */ React.createElement("dd", { className: "min-w-0 break-words text-foreground" }, CONTENT_KEYS.has(e.key) ? /* @__PURE__ */ React.createElement("details", { className: "otel-details" }, /* @__PURE__ */ React.createElement("summary", { className: "cursor-pointer text-[11px] text-muted-foreground" }, fmtCount(String(e.value).length), " chars"), /* @__PURE__ */ React.createElement("div", { className: "mt-1" }, /* @__PURE__ */ React.createElement(ValueView, { attrKey: e.key, value: e.value }))) : /* @__PURE__ */ React.createElement(ValueView, { attrKey: e.key, value: e.value, onSessionClick: onSession }))))))));
  }
  function TraceTabs({
    traceId,
    source,
    logsAvailable,
    spans,
    raw,
    windowNs
  }) {
    const [tab, setTab] = useState("spans");
    const [logs, setLogs] = useState(null);
    const [openLog, setOpenLog] = useState(null);
    const [error, setError] = useState(null);
    useEffect(() => {
      if (tab !== "logs" || logs !== null) return;
      const p = withBackend(new URLSearchParams({ trace_id: traceId, limit: "500" }), source);
      const [startNs, endNs] = windowNs || [0, 0];
      if (startNs > 0 && endNs > 0) {
        p.set("start_s", String(Math.max(0, Math.floor(startNs / 1e9) - 300)));
        p.set("end_s", String(Math.ceil(endNs / 1e9) + 300));
      } else {
        p.set("lookback_hours", "720");
      }
      api(source === "live" ? "/live/logs/search" : "/logs/search", p).then((r) => setLogs(r.logs || [])).catch((e) => {
        setError(e);
        setLogs([]);
      });
    }, [tab, logs, source, traceId, windowNs]);
    const rawText = useMemo(() => tab === "raw" ? JSON.stringify(raw, null, 2) : "", [raw, tab]);
    const actions = {
      onTrace: () => void 0,
      onSession: (id) => navigate({ tab: "logs", source, session: id, trace: "", lookback: "168" }),
      onEvent: (name) => navigate({ tab: "logs", source, trace: traceId, session: "", event: name, events: "1", lookback: "720" }),
      onContext: (l) => navigate({ tab: "logs", source, trace: "", session: "", center: String(l.time_unix_nano || ""), lookback: "720" })
    };
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement(
      Segmented,
      {
        value: tab,
        onChange: setTab,
        label: "trace detail view",
        options: [{ id: "spans", label: "Spans" }, ...logsAvailable ? [{ id: "logs", label: "Logs" }] : [], { id: "raw", label: "Raw" }]
      }
    ), tab === "spans" ? spans : null, tab === "logs" ? error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error, prefix: "Logs" }) : logs === null ? /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "Loading\u2026") : logs.length === 0 ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground" }, "No log lines carry this trace id.") : /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", null, logs.length, " line", logs.length === 1 ? "" : "s", " carry this trace id \xB7 click a line for its attributes"), /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        className: "otel-link",
        title: "open the Logs tab filtered to this trace",
        onClick: () => navigate({ tab: "logs", source, trace: traceId, session: "", lookback: "720" })
      },
      "open in Logs tab \u2192"
    )), /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg overflow-hidden border border-border font-mono text-xs" }, logs.map((l, i) => {
      var _a;
      const k = String((_a = l.seq) != null ? _a : i);
      return /* @__PURE__ */ React.createElement(LogRow, { key: k, l, absolute: true, expanded: openLog === k, onToggle: () => setOpenLog(openLog === k ? null : k), actions });
    }))) : null, tab === "raw" ? /* @__PURE__ */ React.createElement("div", { className: "space-y-1" }, /* @__PURE__ */ React.createElement("div", { className: "flex justify-end" }, /* @__PURE__ */ React.createElement(CopyButton, { text: rawText, label: "copy JSON" })), /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-raw" }, rawText)) : null);
  }

  // src/spantree.tsx
  function SpanSection({
    span,
    depth,
    open,
    onToggle,
    startMs,
    offsetPct,
    durPct,
    hasKids,
    source
  }) {
    const kind = kindOf(span.name, span._attrs);
    const hex = KIND_COLOR[kind];
    const isErr = statusCode(span.status) === "error";
    const cost = span._attrs["hermes.cost.usage"];
    const tokens = span._attrs["gen_ai.usage.total_tokens"] || span._attrs["llm.token_count.total"];
    const approval = span._attrs["hermes.approval.choice"];
    const width = Math.min(100 - offsetPct, Math.max(0.8, durPct));
    return /* @__PURE__ */ React.createElement("div", { className: cn("otel-card-bg otel-hoverable overflow-hidden border transition-colors", isErr ? "border-destructive/40" : "border-border") }, /* @__PURE__ */ React.createElement("div", { className: "otel-track relative h-1.5 w-full", title: `+${fmtDurationMs(startMs)} \xB7 ${fmtDurationMs(span.durationMs)}` }, /* @__PURE__ */ React.createElement("div", { className: "absolute inset-y-0", style: { left: `${offsetPct}%`, width: `${width}%`, minWidth: 2, background: hex } })), /* @__PURE__ */ React.createElement(
      Clickable,
      {
        onActivate: onToggle,
        label: `${open ? "collapse" : "expand"} span ${span.name}`,
        "aria-expanded": open,
        className: "otel-row flex items-center gap-2 px-3 py-2",
        style: { paddingLeft: 12 + depth * 20 }
      },
      /* @__PURE__ */ React.createElement("span", { className: "w-3 shrink-0 text-xs text-muted-foreground", "aria-hidden": true }, open ? "\u25BE" : "\u25B8"),
      /* @__PURE__ */ React.createElement("span", { className: "otel-w-2 inline-block h-2 shrink-0 rounded-full", style: { background: hex }, "aria-hidden": true }),
      /* @__PURE__ */ React.createElement("span", { className: "truncate font-mono text-sm", title: span.name }, span.name),
      hasKids ? /* @__PURE__ */ React.createElement("span", { className: "text-[10px] text-muted-foreground", title: "direct child spans" }, span.children.length, " child", span.children.length === 1 ? "" : "ren") : null,
      isErr ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "shrink-0 text-[10px]" }, "error") : null,
      approval ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "shrink-0 text-[10px]" }, "approval: ", approval) : null,
      /* @__PURE__ */ React.createElement("div", { className: "ml-auto flex shrink-0 items-center gap-3 text-[11px] text-muted-foreground" }, tokens ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtTokens(tokens), " tok") : null, cost != null ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums otel-c-cost" }, fmtCostExact(Number(cost))) : null, startMs > 0.5 ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums", title: "start offset from trace begin" }, "+", fmtDurationMs(startMs)) : null, /* @__PURE__ */ React.createElement("span", { className: "otel-w-14 text-right font-medium tabular-nums text-foreground" }, fmtDurationMs(span.durationMs)))
    ), open ? /* @__PURE__ */ React.createElement("div", { className: "space-y-3 border-t border-border/60 bg-muted/20 px-3 py-3" }, /* @__PURE__ */ React.createElement(SpanSummary, { span, source }), /* @__PURE__ */ React.createElement("details", { className: "otel-details" }, /* @__PURE__ */ React.createElement("summary", { className: "cursor-pointer text-[11px] font-medium uppercase tracking-wide text-muted-foreground" }, "all attributes"), /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(AttrGroups, { attrs: span._attrs, source })))) : null);
  }
  function axisTicks(totalMs, n = 5) {
    if (!(totalMs > 0)) return [];
    const out = [];
    for (let i = 0; i <= n; i++) out.push({ pct: i / n * 100, label: fmtDurationMs(totalMs * i / n) });
    return out;
  }
  function SpanTreeView({ roots, defaultOpen, source }) {
    const flat = useMemo(() => flatten(roots), [roots]);
    const [openIds, setOpenIds] = useState(() => defaultOpen ? Object.fromEntries(flat.map((n) => [n.span.spanId, true])) : {});
    const [allOpen, setAllOpen] = useState(!!defaultOpen);
    useEffect(() => {
      if (allOpen) setOpenIds((p) => ({ ...Object.fromEntries(flat.map((n) => [n.span.spanId, true])), ...p }));
    }, [flat, allOpen]);
    if (!flat.length) return /* @__PURE__ */ React.createElement("div", { className: "py-6 text-center text-sm text-muted-foreground" }, "No spans.");
    const t0 = Math.min(...flat.map((n) => n.span.startNs));
    const total = Math.max(...flat.map((n) => n.span.endNs)) - t0 || 1;
    const toggle = (id) => setOpenIds((p) => ({ ...p, [id]: !p[id] }));
    const expandAll = () => {
      setAllOpen(true);
      setOpenIds(Object.fromEntries(flat.map((n) => [n.span.spanId, true])));
    };
    const collapseAll = () => {
      setAllOpen(false);
      setOpenIds({});
    };
    const ticks = axisTicks(total / 1e6);
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between" }, /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-muted-foreground" }, flat.length, " span", flat.length === 1 ? "" : "s", " \xB7 ", fmtDurationMs(total / 1e6), " total \xB7 ", roots.length > 1 ? `${roots.length} roots` : "1 root"), /* @__PURE__ */ React.createElement("div", { className: "flex gap-2" }, /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: expandAll }, "Expand all"), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: collapseAll }, "Collapse"))), /* @__PURE__ */ React.createElement("div", { className: "otel-axis", "aria-hidden": true }, ticks.map((t) => /* @__PURE__ */ React.createElement(React.Fragment, { key: t.pct }, /* @__PURE__ */ React.createElement("i", { style: { left: `${t.pct}%` } }), /* @__PURE__ */ React.createElement("span", { style: { left: `${t.pct}%` } }, t.label)))), /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-1.5" }, flat.map((n) => /* @__PURE__ */ React.createElement(
      SpanSection,
      {
        key: n.span.spanId,
        span: n.span,
        depth: n.depth,
        open: !!openIds[n.span.spanId],
        onToggle: () => toggle(n.span.spanId),
        startMs: (n.span.startNs - t0) / 1e6,
        offsetPct: (n.span.startNs - t0) / total * 100,
        durPct: n.span.durationMs * 1e6 * 100 / total,
        hasKids: n.span.children.length > 0,
        source
      }
    ))));
  }
  function TraceCard({ row, onSelect }) {
    const Icon = kindIcon(row.rootKind);
    return /* @__PURE__ */ React.createElement(
      Clickable,
      {
        onActivate: () => onSelect(row),
        label: `open trace ${row.rootName}`,
        className: cn(
          "otel-card-bg otel-hover-parent flex cursor-pointer items-start gap-3 border p-3 transition-colors hover:bg-secondary/30",
          row.error ? "otel-error-bg border-destructive/30" : "border-border"
        ),
        title: row.traceId
      },
      /* @__PURE__ */ React.createElement("div", { className: "shrink-0 pt-0.5", style: { color: KIND_COLOR[row.rootKind] }, "aria-hidden": true }, /* @__PURE__ */ React.createElement(Icon, { size: 16 })),
      /* @__PURE__ */ React.createElement("div", { className: "min-w-0 flex-1 space-y-1" }, /* @__PURE__ */ React.createElement("div", { className: "flex min-w-0 items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: "truncate font-mono text-sm" }, row.rootName), row.rootKind !== "other" ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "shrink-0 text-[10px]" }, row.rootKind) : null, row.error ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "shrink-0 text-[10px]" }, "error") : null, row.partial ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "shrink-0 text-[10px]", title: "the root span has not finished: name, kind and totals are provisional" }, "in progress") : null, row.toolName ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "shrink-0 font-mono text-[10px]" }, row.toolName) : null), row.inPreview || row.outPreview ? /* @__PURE__ */ React.createElement("div", { className: "otel-pl-2 space-y-0.5 border-l-2 border-border/60 text-xs" }, row.inPreview ? /* @__PURE__ */ React.createElement("div", { className: "truncate text-foreground/80" }, /* @__PURE__ */ React.createElement("span", { className: "otel-mr-2 text-[10px] text-muted-foreground" }, "in"), row.inPreview) : null, row.outPreview ? /* @__PURE__ */ React.createElement("div", { className: "truncate text-foreground/80" }, /* @__PURE__ */ React.createElement("span", { className: "otel-mr-2 text-[10px] text-muted-foreground" }, "out"), row.outPreview) : null) : null, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground" }, row.model ? /* @__PURE__ */ React.createElement("span", { className: "font-mono text-foreground/80" }, row.model) : null, row.service && row.service !== "hermes" ? /* @__PURE__ */ React.createElement("span", null, row.service) : null, /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, row.spanCount != null ? `${row.spanCount} span${row.spanCount === 1 ? "" : "s"}` : "spans ?"), /* @__PURE__ */ React.createElement("span", { className: "text-border", "aria-hidden": true }, "\xB7"), /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtDurationMs(row.durationMs)), row.tokens != null ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("span", { className: "text-border", "aria-hidden": true }, "\xB7"), /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtTokens(row.tokens), " tok")) : null, row.cost != null ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums otel-c-cost" }, fmtCostExact(row.cost)) : null, /* @__PURE__ */ React.createElement("span", { className: "text-border", "aria-hidden": true }, "\xB7"), /* @__PURE__ */ React.createElement("span", { title: fmtAbsTime(row.startNs) }, fmtTimeAgo(row.endNs || row.startNs)))),
      /* @__PURE__ */ React.createElement("div", { className: "otel-self-center otel-reveal shrink-0 text-muted-foreground transition-opacity", "aria-hidden": true }, /* @__PURE__ */ React.createElement(IconChevronRight, { size: 16 }))
    );
  }
  function LiveTraceDetail({
    trace,
    roots,
    onBack,
    source = "live",
    loading
  }) {
    const spans = trace.spans || [];
    const root = findRoot(spans) || spans[0];
    const startNs = spans.length ? Math.min(...spans.map((s) => s.start_time_unix_nano || 0)) : trace.startNs;
    const endNs = spans.length ? Math.max(...spans.map((s) => s.end_time_unix_nano || s.start_time_unix_nano || 0)) : trace.endNs;
    return /* @__PURE__ */ React.createElement(Card, null, /* @__PURE__ */ React.createElement(CardHeader, { className: "otel-space-y-0" }, /* @__PURE__ */ React.createElement(
      TraceHeader,
      {
        title: trace.rootName,
        traceId: String(trace.traceId),
        service: trace.service,
        durationMs: trace.durationMs,
        rootAttrs: (root == null ? void 0 : root.attributes) || {},
        spans: spans.map((s) => ({ name: s.name, attributes: s.attributes || {} })),
        error: trace.error,
        partial: !!trace.partial || spans.length > 0 && !findRoot(spans),
        source,
        onBack
      }
    )), /* @__PURE__ */ React.createElement(CardContent, null, loading ? /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "Loading spans\u2026") : null, /* @__PURE__ */ React.createElement(
      TraceTabs,
      {
        traceId: String(trace.traceId),
        source,
        logsAvailable: true,
        spans: /* @__PURE__ */ React.createElement(SpanTreeView, { roots, source }),
        raw: spans,
        windowNs: [startNs, endNs]
      }
    )));
  }

  // src/live.tsx
  var POLL_MS2 = 2e3;
  var WINDOW_H = 1;
  var PAGE = 50;
  var BUCKETS = 50;
  var BUCKET_S = 2;
  function deriveStats(rows) {
    let cost = null;
    let tokens = null;
    let errors = 0;
    let spans = 0;
    let spansKnown = true;
    const byKind = {};
    for (const t of rows) {
      if (t.cost != null) cost = (cost || 0) + t.cost;
      if (t.tokens != null) tokens = (tokens || 0) + t.tokens;
      if (t.error) errors++;
      if (t.spanCount == null) spansKnown = false;
      else spans += t.spanCount;
      byKind[t.rootKind] = (byKind[t.rootKind] || 0) + 1;
    }
    return { cost, tokens, errors, turns: rows.length, spans: spansKnown ? spans : null, byKind };
  }
  function activityBuckets(rows, now = Date.now()) {
    const buckets = new Array(BUCKETS).fill(0);
    for (const t of rows) {
      const endMs = (t.endNs || t.startNs) / 1e6;
      const idx = BUCKETS - 1 - Math.floor((now - endMs) / (BUCKET_S * 1e3));
      if (idx >= 0 && idx < BUCKETS) buckets[idx] += t.spanCount || 1;
    }
    return buckets;
  }
  function LivePage() {
    const active = useActive();
    const [rows, setRows] = useState([]);
    const [status, setStatus] = useState(null);
    const [error, setError] = useState(null);
    const [paused, setPaused] = useState(false);
    const [selected, setSelected] = useState(null);
    const [detailSpans, setDetailSpans] = useState(null);
    const [showPings, setShowPings] = useState(false);
    const [loaded, setLoaded] = useState(false);
    const inflight = useRef(false);
    const poll = useCallback(async () => {
      if (inflight.current) return;
      inflight.current = true;
      try {
        const st = await api("/live/status");
        setStatus(st);
        if (!st || st.live === false) return;
        const r = await api("/live/traces", { lookback_hours: WINDOW_H, limit: PAGE });
        setRows((r.traces || []).map(rowFromLive));
        setError(null);
      } catch (e) {
        setError(e);
      } finally {
        inflight.current = false;
        setLoaded(true);
      }
    }, []);
    useEffect(() => {
      poll();
    }, [poll]);
    usePolling(poll, POLL_MS2, active && !paused && !selected);
    const loadDetail = useCallback(async (id) => api(`/live/traces/${encodeURIComponent(id)}`), []);
    useEffect(() => {
      if (!selected) return;
      setDetailSpans(null);
      loadDetail(selected.traceId).then((r) => {
        setDetailSpans(r.spans || []);
        if (r.trace) setSelected((s) => s && s.traceId === selected.traceId ? { ...s, ...r.trace } : s);
      }).catch(() => setDetailSpans([]));
    }, [selected == null ? void 0 : selected.traceId, loadDetail]);
    const refreshDetail = useCallback(() => {
      if (!selected) return;
      loadDetail(selected.traceId).then((r) => setDetailSpans(r.spans || [])).catch(() => void 0);
    }, [selected == null ? void 0 : selected.traceId, loadDetail]);
    usePolling(refreshDetail, POLL_MS2, active && !!selected && (!!selected.partial || detailSpans != null && !findRoot(detailSpans)));
    useEffect(() => {
      const t = readNav().trace;
      if (!t || readNav().tab !== "live") return;
      loadDetail(t).then((r) => {
        if (r.trace) setSelected({ ...r.trace, spans: r.spans });
      }).catch(() => void 0);
    }, [loadDetail]);
    useEffect(() => {
      if (!active) return;
      writeNav({ trace: selected ? String(selected.traceId) : "" });
    }, [selected, active]);
    const allRows = rows;
    const shown = showPings ? allRows : allRows.filter((t) => !isMcpKeepalivePing(t.rootName, t.error));
    const hiddenPings = allRows.length - shown.length;
    const stats = deriveStats(shown);
    const buckets = activityBuckets(shown);
    const lastSession = shown.length ? shown[0].session : null;
    if (status && status.live === false) {
      return /* @__PURE__ */ React.createElement(Empty, { title: "Live store unavailable" }, status.reason || "Set dashboard_live: true in the plugin config (it's on by default), then run a turn.", status.path ? /* @__PURE__ */ React.createElement("div", { className: "mt-1 font-mono text-xs" }, status.path) : null);
    }
    if (selected) {
      const spans = detailSpans || [];
      const { roots } = liveTreeFromSpans(spans);
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement(LiveTraceDetail, { trace: { ...selected, spans }, roots, loading: detailSpans === null, onBack: () => setSelected(null) }));
    }
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2.5" }, /* @__PURE__ */ React.createElement(Pulse, { active: !paused && ((status == null ? void 0 : status.spans) || 0) > 0 }), /* @__PURE__ */ React.createElement("span", { className: "text-base font-semibold tracking-tight" }, "Live agent activity"), /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, fmtInt(status == null ? void 0 : status.spans), " spans buffered", lastSession ? /* @__PURE__ */ React.createElement(React.Fragment, null, " ", "\xB7 session ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, String(lastSession).slice(0, 12))) : null)), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: () => setPaused((p) => !p), title: paused ? "resume following" : "stop following" }, paused ? /* @__PURE__ */ React.createElement(IconPlay, { size: 12 }) : /* @__PURE__ */ React.createElement(IconPause, { size: 12 }), /* @__PURE__ */ React.createElement("span", { className: "ml-1" }, paused ? "Resume" : "Pause"))), error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error }) : null, (status == null ? void 0 : status.write_error) ? /* @__PURE__ */ React.createElement(ErrorBanner, { error: `0: ${status.write_error}`, prefix: "Live store write failed" }) : null, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement(MiniLabel, null, "last ", WINDOW_H, "h \xB7 up to ", PAGE, " newest turns")), /* @__PURE__ */ React.createElement("div", { className: "otel-kpi-grid" }, /* @__PURE__ */ React.createElement(Stat, { label: "Cost", value: stats.cost != null ? fmtCost(stats.cost) : null, accent: "cost", unknownText: "no pricing data" }), /* @__PURE__ */ React.createElement(Stat, { label: "Tokens", value: stats.tokens != null ? fmtInt(stats.tokens) : null, unknownText: "not recorded" }), /* @__PURE__ */ React.createElement(Stat, { label: "Turns", value: fmtInt(stats.turns) }), /* @__PURE__ */ React.createElement(Stat, { label: "Spans", value: stats.spans != null ? fmtInt(stats.spans) : null, unknownText: "unknown" }), /* @__PURE__ */ React.createElement(Stat, { label: "Errors", value: fmtInt(stats.errors), accent: stats.errors ? "error" : void 0 })), /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg flex items-center gap-4 border border-border px-3 py-2" }, /* @__PURE__ */ React.createElement(MiniLabel, null, "activity \xB7 spans per ", BUCKET_S, " s \xB7 last ", BUCKETS * BUCKET_S, " s"), /* @__PURE__ */ React.createElement("div", { className: "otel-w-44" }, /* @__PURE__ */ React.createElement(Sparkline, { values: buckets, label: `spans finished per ${BUCKET_S} seconds over the last ${BUCKETS * BUCKET_S} seconds` })), /* @__PURE__ */ React.createElement("div", { className: "ml-auto flex flex-wrap gap-3" }, Object.keys(stats.byKind).sort((a, b) => (stats.byKind[b] || 0) - (stats.byKind[a] || 0)).slice(0, 7).map((k) => /* @__PURE__ */ React.createElement(
      "span",
      {
        key: k,
        className: "inline-flex items-center gap-1.5 text-[11px] text-muted-foreground",
        title: `${stats.byKind[k]} turn${stats.byKind[k] === 1 ? "" : "s"} rooted in a ${k} span`
      },
      /* @__PURE__ */ React.createElement("span", { className: "otel-w-2 inline-block h-2 rounded-full", style: { background: KIND_COLOR[k] }, "aria-hidden": true }),
      k,
      " ",
      stats.byKind[k]
    )))), /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between pt-1" }, /* @__PURE__ */ React.createElement(MiniLabel, null, "recent turns"), /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-3 text-[11px] text-muted-foreground" }, /* @__PURE__ */ React.createElement(
      Toggle,
      {
        checked: showPings,
        onChange: setShowPings,
        label: `show MCP keepalive pings${hiddenPings ? ` (${hiddenPings} hidden)` : ""}`,
        Switch: Checkbox
      }
    ), /* @__PURE__ */ React.createElement("span", null, "open a turn to see its span waterfall"))), /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-2" }, shown.length === 0 && loaded ? /* @__PURE__ */ React.createElement(Empty, { title: "Waiting for activity\u2026" }, "Run a Hermes turn (CLI, Telegram, anything). Each turn appears here as a card \u2014 open it to see every span, timing and attribute. No backend required.") : shown.map((t) => /* @__PURE__ */ React.createElement(TraceCard, { key: t.traceId, row: t, onSelect: (r) => setSelected(r.raw) }))));
  }

  // src/filters.tsx
  var SUPPORT_NOTE = {
    client: "applied after the backend answers: a page can come back short",
    none: "this source ignores this field"
  };
  function Field({
    label,
    children,
    className,
    support
  }) {
    const note = support && SUPPORT_NOTE[support];
    const off = support === "none";
    return /* @__PURE__ */ React.createElement("div", { className: cn("space-y-1", off ? "otel-field-off" : "", className), title: note || void 0 }, /* @__PURE__ */ React.createElement(Label, { className: "text-[10px] uppercase tracking-wide text-muted-foreground" }, label, support === "client" ? /* @__PURE__ */ React.createElement("span", { className: "otel-field-note" }, " \xB7 after fetch") : support === "none" ? /* @__PURE__ */ React.createElement("span", { className: "otel-field-note" }, " \xB7 ignored") : null), children);
  }
  function FilterBar({
    filters,
    onChange,
    onSubmit,
    backend,
    status,
    busy,
    support,
    hide
  }) {
    const set = (k, v) => onChange({ ...filters, [k]: v });
    const sup = (k) => fieldSupport(support, k);
    const show = (k) => !hide || !hide.includes(k);
    const input = (k, placeholder, type = "text") => {
      var _a;
      return /* @__PURE__ */ React.createElement(
        Input,
        {
          className: "h-8",
          type,
          placeholder,
          value: String((_a = filters[k]) != null ? _a : ""),
          onChange: (e) => set(k, e.target.value),
          "aria-label": String(k),
          min: type === "number" ? 0 : void 0
        }
      );
    };
    const lang = (status == null ? void 0 : status.query_lang_label) || "";
    const rawLabel = !lang ? "native query" : /filter$/i.test(lang.trim()) ? lang : `${lang} query`;
    return /* @__PURE__ */ React.createElement(
      "form",
      {
        className: "space-y-2",
        onSubmit: (e) => {
          e.preventDefault();
          onSubmit();
        }
      },
      /* @__PURE__ */ React.createElement("div", { className: "otel-search-grid" }, show("status") ? /* @__PURE__ */ React.createElement(Field, { label: "status", support: sup("status") }, /* @__PURE__ */ React.createElement(Select, { value: filters.status, onValueChange: (v) => set("status", v), className: "h-8", "aria-label": "status" }, /* @__PURE__ */ React.createElement(SelectOption, { value: "" }, "any"), /* @__PURE__ */ React.createElement(SelectOption, { value: "ok" }, "ok"), /* @__PURE__ */ React.createElement(SelectOption, { value: "error" }, "error"))) : null, show("kind") ? /* @__PURE__ */ React.createElement(Field, { label: "kind", support: sup("kind") }, /* @__PURE__ */ React.createElement(Select, { value: filters.kind, onValueChange: (v) => set("kind", v), className: "h-8", "aria-label": "kind" }, /* @__PURE__ */ React.createElement(SelectOption, { value: "" }, "any"), KINDS.map((k) => /* @__PURE__ */ React.createElement(SelectOption, { key: k, value: k }, k)))) : null, show("tool") ? /* @__PURE__ */ React.createElement(Field, { label: "tool", support: sup("tool") }, input("tool", "terminal")) : null, show("model") ? /* @__PURE__ */ React.createElement(Field, { label: "model", support: sup("model") }, input("model", backend ? "exact model name" : "substring")) : null, show("session") ? /* @__PURE__ */ React.createElement(Field, { label: "session id", support: sup("session") }, input("session", "20260920_0814\u2026")) : null, show("minDurationMs") ? /* @__PURE__ */ React.createElement(Field, { label: "min duration (ms)", support: sup("minDurationMs") }, input("minDurationMs", "0", "number")) : null, show("text") ? /* @__PURE__ */ React.createElement(Field, { label: "text", support: sup("text") }, input("text", backend ? "in the prompt" : "anywhere in attributes")) : null, show("traceId") ? /* @__PURE__ */ React.createElement(Field, { label: "trace id", support: sup("traceId") }, input("traceId", "trace id")) : null, /* @__PURE__ */ React.createElement(Field, { label: "lookback" }, /* @__PURE__ */ React.createElement(Select, { value: String(filters.lookback), onValueChange: (v) => set("lookback", Number(v)), className: "h-8", "aria-label": "lookback" }, LOOKBACKS.map((l) => /* @__PURE__ */ React.createElement(SelectOption, { key: l.hours, value: String(l.hours) }, l.label))))),
      backend && (show("q") || show("service")) ? /* @__PURE__ */ React.createElement("div", { className: "otel-search-grid" }, show("q") ? /* @__PURE__ */ React.createElement(Field, { label: rawLabel, className: "otel-span-2", support: sup("q") }, input("q", (status == null ? void 0 : status.raw_placeholder) || "")) : null, show("service") ? /* @__PURE__ */ React.createElement(Field, { label: "service", support: sup("service") }, input("service", "any")) : null, show("rootsOnly") ? /* @__PURE__ */ React.createElement(
        "label",
        {
          className: "otel-self-end inline-flex cursor-pointer items-center gap-1.5 pb-2 text-xs text-muted-foreground",
          title: sup("rootsOnly") === "client" ? SUPPORT_NOTE.client : "list whole turns, not every matching span"
        },
        /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: filters.rootsOnly, onChange: (e) => set("rootsOnly", e.target.checked) }),
        "roots only",
        sup("rootsOnly") === "client" ? " \xB7 after fetch" : ""
      ) : null) : null,
      /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement(Button, { type: "submit", size: "sm", disabled: !!busy }, busy ? "Searching\u2026" : "Search"), /* @__PURE__ */ React.createElement(
        Button,
        {
          type: "button",
          variant: "outline",
          size: "sm",
          title: "clear every field; keeps the lookback and roots-only",
          onClick: () => onChange({ ...DEFAULT_FILTERS, lookback: filters.lookback, rootsOnly: filters.rootsOnly })
        },
        "Clear"
      ))
    );
  }

  // src/sessions.tsx
  var POLL_MS3 = 1e4;
  var PAGE2 = 50;
  function SessionCard({ row, open, onToggle, children, partial }) {
    const lookback = Math.max(1, Math.ceil((Date.now() - row.startNs / 1e6) / 36e5) + 1);
    return /* @__PURE__ */ React.createElement("div", { className: row.errors ? "otel-card-bg border border-destructive/30" : "otel-card-bg border border-border" }, /* @__PURE__ */ React.createElement(
      Clickable,
      {
        onActivate: onToggle,
        toggle: true,
        open,
        label: `${open ? "collapse" : "expand"} session ${row.session}`,
        className: "otel-row flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2"
      },
      /* @__PURE__ */ React.createElement("span", { className: "font-mono text-sm", title: row.session }, row.session),
      row.platform ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]" }, row.platform) : null,
      row.errors ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "text-[10px]" }, row.errors, " error", row.errors === 1 ? "" : "s") : null,
      partial ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]", title: "grouped from the traces on this page of results, not the whole session" }, "this page only") : null,
      /* @__PURE__ */ React.createElement("span", { className: "ml-auto flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground" }, row.model ? /* @__PURE__ */ React.createElement("span", { className: "font-mono text-foreground/80" }, row.model) : null, /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, row.turns, " turn", row.turns === 1 ? "" : "s"), row.spans != null ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, row.spans, " spans") : null, row.toolCalls != null ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, row.toolCalls, " tool call", row.toolCalls === 1 ? "" : "s") : null, row.tokens != null ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtTokens(row.tokens), " tok") : null, row.cost != null ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums otel-c-cost" }, fmtCostExact(row.cost)) : null, /* @__PURE__ */ React.createElement("span", { className: "tabular-nums", title: "wall time from the first turn's start to the last turn's end" }, fmtDurationMs((row.endNs - row.startNs) / 1e6), " wall"), /* @__PURE__ */ React.createElement(
        "button",
        {
          type: "button",
          className: "otel-link text-[11px]",
          title: "this session's log lines and events",
          onClick: (e) => {
            e.stopPropagation();
            navigate({ tab: "logs", session: row.session, trace: "", lookback: String(Math.min(720, lookback)) });
          }
        },
        "logs"
      ), /* @__PURE__ */ React.createElement("span", { title: `${fmtAbsTime(row.startNs)} \u2192 ${fmtAbsTime(row.endNs)}` }, fmtTimeAgo(row.endNs)))
    ), open ? /* @__PURE__ */ React.createElement("div", { className: "border-t border-border/60 px-3 py-2" }, children) : null);
  }
  function LiveSessions({
    filters,
    wantedSession,
    onSelectTrace,
    active
  }) {
    const [rows, setRows] = useState([]);
    const [hasMore, setHasMore] = useState(false);
    const [limit, setLimit] = useState(PAGE2);
    const [error, setError] = useState(null);
    const [open, setOpen] = useState(wantedSession || null);
    const [turns, setTurns] = useState({});
    const [loaded, setLoaded] = useState(false);
    const load = useCallback(async () => {
      try {
        const r = await api("/live/sessions", { lookback_hours: filters.lookback, limit });
        setRows(r.sessions || []);
        setHasMore(!!r.has_more);
        setError(null);
      } catch (e) {
        setError(e);
      } finally {
        setLoaded(true);
      }
    }, [filters.lookback, limit]);
    useEffect(() => {
      load();
    }, [load]);
    usePolling(load, POLL_MS3, active);
    const loadTurns = useCallback(
      async (sid) => {
        const p = liveParams({ ...filters, session: sid }, 200);
        try {
          const r = await api("/live/traces", p);
          setTurns((prev) => ({ ...prev, [sid]: r.traces || [] }));
        } catch {
          setTurns((prev) => ({ ...prev, [sid]: [] }));
        }
      },
      [filters]
    );
    useEffect(() => {
      if (open) loadTurns(open);
    }, [open, loadTurns, rows]);
    const toggle = (sid) => setOpen((o) => o === sid ? null : sid);
    if (error) return /* @__PURE__ */ React.createElement(ErrorBanner, { error, prefix: "Sessions" });
    const shown = wantedSession ? rows.filter((r) => r.session === wantedSession) : rows;
    if (loaded && !shown.length)
      return /* @__PURE__ */ React.createElement(
        Empty,
        {
          title: wantedSession ? `Session ${wantedSession} is not in the last ${lookbackLabel(filters.lookback)}` : `No sessions in the last ${lookbackLabel(filters.lookback)}`
        },
        "Turns carry ",
        /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "hermes.session_id"),
        "; sessions group them.",
        wantedSession ? /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: () => navigate({ tab: "traces", view: "sessions", session: "", trace: "" }) }, "show every session")) : null
      );
    return /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, wantedSession ? /* @__PURE__ */ React.createElement(React.Fragment, null, "one session \xB7", " ", /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-link", onClick: () => navigate({ tab: "traces", view: "sessions", session: "", trace: "" }) }, "show every session")) : `${rows.length} session${rows.length === 1 ? "" : "s"} \xB7 click one to see its turns in order`), shown.map((row) => /* @__PURE__ */ React.createElement(SessionCard, { key: row.session, row, open: open === row.session, onToggle: () => toggle(row.session) }, turns[row.session] ? turns[row.session].length ? /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-1.5" }, turns[row.session].slice().sort((a, b) => a.startNs - b.startNs).map((t) => /* @__PURE__ */ React.createElement(TraceCard, { key: t.traceId, row: { ...rowFromLive(t) }, onSelect: () => onSelectTrace(t) })), turns[row.session].length >= 200 ? /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "Showing the first 200 turns of this session.") : null) : /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "No turns in this window.") : /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "Loading\u2026"))), hasMore && !wantedSession ? /* @__PURE__ */ React.createElement("div", { className: "flex justify-center" }, /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: () => setLimit((n) => n + PAGE2) }, "Show more sessions")) : null);
  }
  function BackendSessions({ rows, wantedSession, renderTrace }) {
    const [open, setOpen] = useState(wantedSession || null);
    const grouped = groupBySession(rows.map((r) => r.raw));
    const unattributed = rows.length - grouped.reduce((n, r) => n + r.turns, 0);
    if (!rows.length) return null;
    if (!grouped.length)
      return /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground" }, "None of the ", rows.length, " results carries a session id, so they cannot be grouped. The Turns view lists them.");
    const byId = {};
    for (const t of rows) byId[t.traceId] = t;
    const shown = wantedSession ? grouped.filter((r) => r.session === wantedSession) : grouped;
    return /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, shown.length, " session", shown.length === 1 ? "" : "s", " grouped from the ", rows.length, " results on this page", unattributed ? ` \xB7 ${unattributed} without a session id` : "", wantedSession ? /* @__PURE__ */ React.createElement(React.Fragment, null, " \xB7 ", /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-link", onClick: () => navigate({ tab: "traces", view: "sessions", session: "", trace: "" }) }, "show every session")) : null), shown.map((row) => /* @__PURE__ */ React.createElement(SessionCard, { key: row.session, row, open: open === row.session, partial: true, onToggle: () => setOpen(open === row.session ? null : row.session) }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-1.5" }, row.traceIds.map((id) => byId[id]).filter(Boolean).sort((a, b) => a.startNs - b.startNs).map((t) => renderTrace(t))))));
  }

  // src/traces.tsx
  var POLL_MS4 = 3e3;
  var PAGE3 = 50;
  var VIEW_KEY = "hermes_otel.tracesView";
  function readView() {
    try {
      return localStorage.getItem(VIEW_KEY) === "sessions" ? "sessions" : "turns";
    } catch {
      return "turns";
    }
  }
  var EMPTY_PAGE = { rows: [], total: null, hasMore: false, nextBefore: null, ignored: [] };
  function useTracePaging(initialNav) {
    const [filters, setFilters] = useState(() => traceFiltersFromNav(initialNav));
    const [applied, setApplied] = useState(() => traceFiltersFromNav(initialNav));
    const [cursors, setCursors] = useState(() => cursorsFromNav(initialNav.before));
    const before = cursors.length ? cursors[cursors.length - 1] : null;
    useEffect(() => {
      writeNav({ ...navFromTraceFilters(applied), before: navFromCursors(cursors) });
    }, [applied, cursors]);
    const submit = () => {
      setApplied(filters);
      setCursors([]);
    };
    const older = (next) => {
      if (next) setCursors((c) => [...c, next]);
    };
    const newer = () => setCursors((c) => c.slice(0, -1));
    const newest = () => setCursors([]);
    return { filters, setFilters, applied, submit, cursors, before, older, newer, newest, page: cursors.length + 1 };
  }
  function LiveTraces({ view, wanted, active }) {
    const paging = useTracePaging(wanted);
    const { applied, before } = paging;
    const [page, setPage] = useState(EMPTY_PAGE);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [selected, setSelected] = useState(null);
    const [detailSpans, setDetailSpans] = useState(null);
    const [showPings, setShowPings] = useState(false);
    const [paused, setPaused] = useState(false);
    const inflight = useRef(false);
    const load = useCallback(async () => {
      if (inflight.current) return;
      inflight.current = true;
      try {
        const r = await api("/live/traces", liveParams(applied, PAGE3, before));
        const rows = (r.traces || []).map(rowFromLive);
        setPage({
          rows,
          total: typeof r.total === "number" ? r.total : null,
          hasMore: !!r.has_more,
          nextBefore: r.next_before_ns != null ? String(r.next_before_ns) : rows.length === PAGE3 ? String(rows[rows.length - 1].startNs) : null,
          ignored: []
        });
        setError(null);
      } catch (e) {
        setError(e);
      } finally {
        inflight.current = false;
        setLoading(false);
      }
    }, [applied, before]);
    useEffect(() => {
      setLoading(true);
      load();
    }, [load]);
    usePolling(load, POLL_MS4, active && !paused && !selected && view === "turns" && !before);
    const loadDetail = useCallback(async (id) => {
      const r = await api(`/live/traces/${encodeURIComponent(id)}`);
      return r;
    }, []);
    useEffect(() => {
      if (!selected) return;
      setDetailSpans(null);
      loadDetail(selected.traceId).then((r) => {
        setDetailSpans(r.spans || []);
        if (r.trace) setSelected((s) => s && s.traceId === selected.traceId ? { ...s, ...r.trace } : s);
      }).catch(() => setDetailSpans([]));
    }, [selected == null ? void 0 : selected.traceId, loadDetail]);
    const refreshDetail = useCallback(() => {
      if (!selected) return;
      loadDetail(selected.traceId).then((r) => setDetailSpans(r.spans || [])).catch(() => void 0);
    }, [selected == null ? void 0 : selected.traceId, loadDetail]);
    usePolling(refreshDetail, POLL_MS4, active && !!selected && (!!selected.partial || detailSpans != null && !findRoot(detailSpans)));
    useEffect(() => {
      if (!wanted.trace) return;
      loadDetail(wanted.trace).then((r) => {
        if (r.trace) setSelected({ ...r.trace, spans: r.spans });
      }).catch((e) => setError(e));
    }, [wanted.trace, loadDetail]);
    useEffect(() => {
      if (selected) writeNav({ trace: String(selected.traceId) });
      else if (!wanted.trace) writeNav({ trace: "" });
    }, [selected, wanted.trace]);
    if (selected) {
      const spans = detailSpans || [];
      const { roots } = liveTreeFromSpans(spans);
      const trace = { ...selected, spans };
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement(
        LiveTraceDetail,
        {
          trace,
          roots,
          loading: detailSpans === null,
          onBack: () => {
            setSelected(null);
            writeNav({ trace: "" });
          }
        }
      ));
    }
    const pingCount = page.rows.filter((t) => isMcpKeepalivePing(t.rootName, t.error)).length;
    const shown = showPings ? page.rows : page.rows.filter((t) => !isMcpKeepalivePing(t.rootName, t.error));
    const oldest = shown.length ? shown[shown.length - 1] : null;
    const newestRow = shown.length ? shown[0] : null;
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement(Card, null, /* @__PURE__ */ React.createElement(CardContent, { className: "space-y-3 pt-4" }, /* @__PURE__ */ React.createElement(
      FilterBar,
      {
        filters: paging.filters,
        onChange: paging.setFilters,
        onSubmit: paging.submit,
        backend: false,
        busy: loading,
        support: null,
        hide: view === "sessions" ? ["status", "kind", "tool", "model", "minDurationMs", "text", "traceId", "q", "service", "rootsOnly"] : void 0
      }
    ))), error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error }) : null, view === "sessions" ? /* @__PURE__ */ React.createElement(LiveSessions, { filters: applied, wantedSession: wanted.session || "", onSelectTrace: setSelected, active }) : /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3 text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", null, shown.length, page.total != null ? ` of ${page.total}` : "", " trace", page.total === 1 ? "" : "s", isDefaultFilters({ ...applied, lookback: DEFAULT_FILTERS.lookback }) ? "" : " matching", " in the last ", lookbackLabel(applied.lookback), before ? " \xB7 older page, not following" : paused ? " \xB7 paused" : " \xB7 following"), /* @__PURE__ */ React.createElement(Toggle, { checked: showPings, onChange: setShowPings, label: `show MCP keepalive pings${pingCount ? ` (${pingCount})` : ""}`, Switch: Checkbox }), /* @__PURE__ */ React.createElement(
      Button,
      {
        variant: "outline",
        size: "sm",
        className: "ml-auto",
        onClick: () => setPaused((p) => !p),
        disabled: !!before,
        title: before ? "an older page does not follow" : paused ? "resume following new turns" : "stop following new turns"
      },
      paused ? /* @__PURE__ */ React.createElement(IconPlay, { size: 12 }) : /* @__PURE__ */ React.createElement(IconPause, { size: 12 }),
      /* @__PURE__ */ React.createElement("span", { className: "ml-1" }, paused ? "Resume" : "Pause")
    )), shown.length === 0 && !loading ? /* @__PURE__ */ React.createElement(Empty, { title: page.total ? "Nothing matched" : before ? "No older traces" : "No traces yet" }, page.total ? "Widen the lookback or clear a filter." : before ? /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: paging.newer }, "\u2190 Back to the newer page") : "Run a Hermes turn \u2014 each turn appears here as a trace you can open into a span waterfall. No backend needed.") : /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-2" }, shown.map((t) => /* @__PURE__ */ React.createElement(TraceCard, { key: t.traceId, row: t, onSelect: (r) => setSelected(r.raw) }))), shown.length || before ? /* @__PURE__ */ React.createElement(
      Pager,
      {
        page: paging.page,
        hasMore: page.hasMore,
        onNewest: paging.newest,
        onNewer: paging.newer,
        onOlder: () => paging.older(page.nextBefore),
        range: oldest && newestRow ? `${new Date(oldest.startNs / 1e6).toLocaleTimeString()} \u2192 ${new Date(newestRow.startNs / 1e6).toLocaleTimeString()}` : ""
      }
    ) : null));
  }
  function StatusBar({ status, onRefresh }) {
    if (!status) return null;
    const configured = status.configured;
    const entry = (status.available || []).find((b) => b.name === status.active) || null;
    const caps = [configured ? "traces" : null, (entry == null ? void 0 : entry.metrics) || status.metrics ? "metrics" : null, (entry == null ? void 0 : entry.logs) || status.logs ? "logs" : null].filter(Boolean);
    return /* @__PURE__ */ React.createElement("div", { className: "flex items-start justify-between gap-3" }, /* @__PURE__ */ React.createElement("div", { className: "min-w-0 flex-1 space-y-1.5" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: cn("h-2.5 w-2.5 rounded-full", configured ? "otel-pulse-dot" : "bg-muted-foreground/40"), "aria-hidden": true }), /* @__PURE__ */ React.createElement("span", { className: "text-base font-semibold tracking-tight" }, configured ? status.name || status.type : "Not configured"), configured && status.type && status.type !== status.name ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px] uppercase" }, status.type) : null, caps.map((c) => /* @__PURE__ */ React.createElement(Badge, { key: c, variant: "secondary", className: "text-[10px]" }, c)), status.query_backend_pin && status.query_backend_pin === status.active ? /* @__PURE__ */ React.createElement("span", { className: "text-[10px] text-muted-foreground" }, "default (query_backend)") : null, status.default_service ? /* @__PURE__ */ React.createElement("span", { className: "text-[10px] text-muted-foreground", title: "service the adapter searches when the service field is empty" }, "service ", status.default_service) : null), configured && status.query_url ? /* @__PURE__ */ React.createElement("div", { className: "truncate font-mono text-xs text-muted-foreground" }, status.query_url) : null), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: onRefresh }, "Refresh"));
  }
  function BackendTraceDetail({
    row,
    detail,
    loading,
    error,
    onBack,
    source,
    status
  }) {
    var _a;
    const tree = useMemo(() => detail ? buildSpanTree(detail.batches || detail.trace && detail.trace.batches) : { roots: [], all: [] }, [detail]);
    const rootSpan = tree.roots[0] || null;
    const rootAttrs = rootSpan ? rootSpan._attrs : traceAttrs(row.raw);
    const durationMs = rootSpan ? rootSpan.durationMs : row.durationMs;
    const isError = tree.all.some((s) => {
      var _a2, _b, _c;
      return ((_c = (_a2 = s.status) == null ? void 0 : _a2.code) != null ? _c : (_b = s.status) == null ? void 0 : _b.statusCode) === 2;
    }) || row.error;
    const startNs = tree.all.length ? Math.min(...tree.all.map((s) => s.startNs)) : row.startNs;
    const endNs = tree.all.length ? Math.max(...tree.all.map((s) => s.endNs)) : row.endNs;
    return /* @__PURE__ */ React.createElement(Card, null, /* @__PURE__ */ React.createElement(CardHeader, { className: "otel-space-y-0" }, /* @__PURE__ */ React.createElement(
      TraceHeader,
      {
        title: (rootSpan == null ? void 0 : rootSpan.name) || row.rootName || "\u2014",
        traceId: row.traceId,
        service: row.service,
        durationMs,
        rootAttrs,
        spans: tree.all.map((s) => ({ name: s.name, attributes: s._attrs })),
        error: isError,
        truncated: !!(detail == null ? void 0 : detail.truncated),
        spanCount: (_a = detail == null ? void 0 : detail.span_count) != null ? _a : row.spanCount,
        uiUrl: (detail == null ? void 0 : detail.ui_url) || null,
        uiLabel: (status == null ? void 0 : status.name) || (status == null ? void 0 : status.type) || null,
        source,
        onBack
      }
    )), /* @__PURE__ */ React.createElement(CardContent, null, loading ? /* @__PURE__ */ React.createElement("div", { className: "py-8 text-center text-sm text-muted-foreground" }, "Loading trace\u2026") : null, error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error, prefix: "Trace" }) : null, !loading && !error ? /* @__PURE__ */ React.createElement(
      TraceTabs,
      {
        traceId: row.traceId,
        source,
        logsAvailable: !!(status == null ? void 0 : status.logs),
        spans: /* @__PURE__ */ React.createElement(SpanTreeView, { roots: tree.roots, source }),
        raw: detail,
        windowNs: [startNs, endNs]
      }
    ) : null));
  }
  function BackendTraces({
    status,
    onRefresh,
    source,
    view,
    wanted,
    support,
    active
  }) {
    const paging = useTracePaging(wanted);
    const { applied, before } = paging;
    const [page, setPage] = useState(null);
    const [showPings, setShowPings] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [selected, setSelected] = useState(null);
    const [detail, setDetail] = useState(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [detailError, setDetailError] = useState(null);
    const inflight = useRef(false);
    const byId = (id) => ({
      traceId: id,
      rootName: "(by id)",
      rootKind: "other",
      service: null,
      startNs: 0,
      endNs: 0,
      durationMs: 0,
      spanCount: null,
      model: null,
      tokens: null,
      cost: null,
      error: false,
      session: null,
      partial: false,
      toolName: null,
      inPreview: null,
      outPreview: null,
      raw: { traceID: id }
    });
    const search = useCallback(async () => {
      if (!(status == null ? void 0 : status.configured) || inflight.current) return;
      const id = applied.traceId.trim();
      if (id) {
        setSelected(byId(id));
        return;
      }
      inflight.current = true;
      setLoading(true);
      setError(null);
      try {
        const r = await api("/traces/search", backendParams(applied, source, PAGE3, before));
        const rows2 = (r.traces || []).map(rowFromBackend);
        setPage({
          rows: rows2,
          total: null,
          hasMore: !!r.has_more,
          nextBefore: r.next_before_ns != null ? String(r.next_before_ns) : null,
          ignored: r.ignored_filters || []
        });
      } catch (e) {
        setError(e);
        setPage(EMPTY_PAGE);
      } finally {
        inflight.current = false;
        setLoading(false);
      }
    }, [applied, before, status == null ? void 0 : status.configured, source]);
    useEffect(() => {
      setSelected(null);
      search();
    }, [search]);
    usePolling(search, POLL_MS4 * 5, active && view === "turns" && !selected && !before && !!(status == null ? void 0 : status.configured));
    useEffect(() => {
      if (wanted.trace && (status == null ? void 0 : status.configured)) setSelected(byId(wanted.trace));
    }, [wanted.trace, status == null ? void 0 : status.configured]);
    useEffect(() => {
      if (selected) writeNav({ trace: selected.traceId });
      else if (!wanted.trace) writeNav({ trace: "" });
    }, [selected, wanted.trace]);
    useEffect(() => {
      if (!selected) return;
      setDetail(null);
      setDetailError(null);
      setDetailLoading(true);
      const p = withBackend(new URLSearchParams(), source);
      api(`/traces/${encodeURIComponent(selected.traceId)}`, p).then(setDetail).catch((e) => setDetailError(e)).finally(() => setDetailLoading(false));
    }, [selected, source]);
    if (!(status == null ? void 0 : status.configured))
      return /* @__PURE__ */ React.createElement(Card, null, /* @__PURE__ */ React.createElement(CardContent, { className: "space-y-2 pt-4 text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("p", null, (status == null ? void 0 : status.reason) || "No queryable trace backend configured."), /* @__PURE__ */ React.createElement("p", { className: "text-xs" }, "That's fine \u2014 the ", /* @__PURE__ */ React.createElement("span", { className: "font-medium text-foreground" }, "Live"), " source needs no backend. Add a backend of a queryable type to browse historical traces here: ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, ((status == null ? void 0 : status.queryable_types) || []).join(", ") || "none available"), ".")));
    if (selected)
      return /* @__PURE__ */ React.createElement(
        BackendTraceDetail,
        {
          row: selected,
          detail,
          loading: detailLoading,
          error: detailError,
          onBack: () => {
            setSelected(null);
            writeNav({ trace: "" });
          },
          source,
          status
        }
      );
    const rows = (page == null ? void 0 : page.rows) || [];
    const pingCount = rows.filter((t) => isMcpKeepalivePing(t.rootName, t.error)).length;
    const shown = showPings ? rows : rows.filter((t) => !isMcpKeepalivePing(t.rootName, t.error));
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement(Card, null, /* @__PURE__ */ React.createElement(CardContent, { className: "space-y-3 pt-4" }, /* @__PURE__ */ React.createElement(StatusBar, { status, onRefresh }), /* @__PURE__ */ React.createElement(
      FilterBar,
      {
        filters: paging.filters,
        onChange: paging.setFilters,
        onSubmit: paging.submit,
        backend: true,
        status,
        busy: loading,
        support,
        hide: view === "sessions" ? ["traceId"] : void 0
      }
    ), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-3" }, /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, page == null ? "Searching\u2026" : `${shown.length} trace${shown.length === 1 ? "" : "s"} on this page`, (page == null ? void 0 : page.ignored.length) ? /* @__PURE__ */ React.createElement("span", { title: "fields this backend's adapter does not honour" }, " \xB7 ignored: ", page.ignored.join(", ")) : null), /* @__PURE__ */ React.createElement(Toggle, { checked: showPings, onChange: setShowPings, label: `show MCP keepalive pings${pingCount ? ` (${pingCount})` : ""}`, Switch: Checkbox })))), error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error, prefix: "Search" }) : null, page && shown.length > 0 ? view === "sessions" ? /* @__PURE__ */ React.createElement(
      BackendSessions,
      {
        rows: shown,
        wantedSession: wanted.session || "",
        renderTrace: (t) => /* @__PURE__ */ React.createElement(TraceCard, { key: t.traceId, row: t, onSelect: setSelected })
      }
    ) : /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-2" }, shown.map((t) => /* @__PURE__ */ React.createElement(TraceCard, { key: t.traceId, row: t, onSelect: setSelected }))) : page && shown.length === 0 && !error ? /* @__PURE__ */ React.createElement(Empty, { title: before ? "No older traces" : "No traces matched" }, pingCount ? `Only MCP keepalive pings matched (${pingCount} hidden): show them with the switch above.` : before ? /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: paging.newer }, "\u2190 Back to the newer page") : "Widen the lookback or run a turn.") : null, page && (shown.length || before) ? /* @__PURE__ */ React.createElement(Pager, { page: paging.page, hasMore: page.hasMore, onNewest: paging.newest, onNewer: paging.newer, onOlder: () => paging.older(page.nextBefore) }) : null);
  }
  function TracesPage() {
    const { source, setSource, status, refresh, isLive, filters } = useSource();
    const active = useActive();
    const [nav, setNav] = useState(() => readNav());
    const [view, setViewState] = useState(nav.view || readView());
    const setView = (v) => {
      try {
        localStorage.setItem(VIEW_KEY, v);
      } catch {
      }
      setViewState(v);
      writeNav({ view: v });
    };
    useEffect(() => {
      const onNav = (e) => {
        const d = e.detail || {};
        if (d.tab && d.tab !== "traces") return;
        if (d.source) setSource(d.source);
        if (d.view) setViewState(d.view);
        setNav({ ...readNav(), ...d });
      };
      window.addEventListener(NAV_EVENT, onNav);
      return () => window.removeEventListener(NAV_EVENT, onNav);
    }, [setSource]);
    useEffect(() => {
      if (active) writeNav({ tab: "traces", source: source === "live" ? "" : source, view });
    }, [source, view, active]);
    const key = `${source}:${nav.trace || ""}:${nav.session || ""}`;
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement(SourceSelect, { source, onChange: setSource, status, need: "traces" }), /* @__PURE__ */ React.createElement(
      Segmented,
      {
        value: view,
        onChange: setView,
        label: "turns or sessions",
        options: [
          { id: "turns", label: "Turns" },
          { id: "sessions", label: "Sessions" }
        ]
      }
    )), isLive ? /* @__PURE__ */ React.createElement(MiniLabel, null, "queried from the in-process store") : null), isLive ? /* @__PURE__ */ React.createElement(LiveTraces, { key, view, wanted: nav, active }) : /* @__PURE__ */ React.createElement(BackendTraces, { key, status, onRefresh: refresh, source, view, wanted: nav, support: filters, active }));
  }

  // src/metrics.tsx
  var POLL_MS5 = 3e4;
  var UNITS = {
    "hermes.token.usage": "tokens",
    "hermes.cost.usage": "USD",
    "hermes.model.usage": "calls",
    "hermes.tool.duration": "ms",
    "hermes.approval.count": "approvals",
    "hermes.approval.duration": "ms",
    "hermes.message.count": "messages",
    "hermes.session.count": "sessions",
    "hermes.session.turns": "turns",
    "hermes.session.duration": "s",
    "hermes.prompt_cache.tokens": "tokens",
    "hermes.prompt_cache.observations": "observations",
    "hermes.api.error.count": "errors",
    "hermes.retry.count": "retries",
    "hermes.subagent.count": "runs",
    "hermes.subagent.duration": "ms",
    "hermes.skill.inferred": "hits",
    "gen_ai.client.token.usage": "tokens",
    "gen_ai.client.operation.duration": "s",
    "gen_ai.agent.token.usage": "tokens",
    "process.cpu.utilization": "ratio",
    "system.cpu.utilization": "ratio",
    "hw.gpu.utilization": "ratio",
    "hw.gpu.memory.usage": "bytes",
    "hw.power": "W"
  };
  var GROUP_KEYS = ["", "model", "provider", "token_type", "tool_name", "choice", "status", "operation", "error_type"];
  function seriesTotal(b, label) {
    if (!b) return 0;
    const keys = label ? [label] : Object.keys(b.series);
    let t = 0;
    for (const k of keys) for (const v of b.series[k] || []) if (v != null) t += v;
    return t;
  }
  function totalsByLabel(b) {
    if (!b) return [];
    return Object.keys(b.series).map((label) => ({ label, value: seriesTotal(b, label) })).sort((x, y) => y.value - x.value);
  }
  function meanByLabel(b) {
    if (!b) return [];
    return Object.keys(b.series).map((label) => {
      const vals = (b.series[label] || []).filter((v) => v != null);
      return { label, value: vals.length ? vals.reduce((a, v) => a + v, 0) / vals.length : 0 };
    }).sort((x, y) => y.value - x.value);
  }
  function rangeLabel(r) {
    const b = r.bucket >= 3600 ? `${r.bucket / 3600}h` : r.bucket >= 60 ? `${r.bucket / 60}m` : `${r.bucket}s`;
    return `last ${r.label} \xB7 ${b} buckets`;
  }
  function resolveInstrument(names, otlp, prefer) {
    const all = new Set(names.map((n) => n.name));
    const canonical = (n) => {
      const m = /^(.*)\.(sum|count)$/.exec(n);
      if (m && all.has(`${m[1]}.sum`) && all.has(`${m[1]}.count`)) return m[1];
      return metricOtlpName(n);
    };
    const matches = names.filter((n) => n.otlp_name === otlp || n.name === otlp || canonical(n.name) === otlp).map((n) => n.name);
    if (!matches.length) return null;
    if (prefer) {
      const hit = matches.find((n) => n.endsWith(prefer) || n.endsWith(prefer.replace("_", ".")));
      if (hit) return hit;
    }
    return matches.find((n) => n === otlp) || matches.find((n) => !/[_.](sum|count|bucket|total)$/.test(n)) || matches[0];
  }
  var PALETTE = [
    "var(--otel-chart-1)",
    "var(--otel-chart-2)",
    "var(--otel-chart-3)",
    "var(--otel-chart-4)",
    "var(--otel-chart-5)",
    "var(--otel-chart-6)",
    "var(--otel-chart-7)",
    "var(--otel-chart-8)"
  ];
  function BarList({ rows, fmt, color }) {
    if (!rows.length) return /* @__PURE__ */ React.createElement("div", { className: "py-3 text-xs text-muted-foreground" }, "No data in this range.");
    const max = Math.max(1e-9, ...rows.map((r) => r.value));
    const top = rows.slice(0, 10);
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-1.5" }, top.map((r) => /* @__PURE__ */ React.createElement(
      "div",
      {
        key: r.label,
        className: "flex items-center gap-2",
        title: `${r.label === "_" ? "all" : r.label}: ${fmt ? fmt(r.value) : fmtInt(Math.round(r.value))}`
      },
      /* @__PURE__ */ React.createElement("span", { className: "otel-w-28 shrink-0 truncate font-mono text-[11px] text-muted-foreground" }, r.label === "_" ? "all" : r.label),
      /* @__PURE__ */ React.createElement("div", { className: "relative h-4 flex-1 bg-muted/30", role: "img", "aria-label": `${r.label}: ${fmt ? fmt(r.value) : fmtInt(Math.round(r.value))}` }, /* @__PURE__ */ React.createElement("div", { className: "absolute inset-y-0 left-0", style: { width: `${r.value / max * 100}%`, background: color || "var(--otel-chart-2)" } })),
      /* @__PURE__ */ React.createElement("span", { className: "otel-w-16 shrink-0 text-right tabular-nums text-xs" }, fmt ? fmt(r.value) : fmtInt(Math.round(r.value)))
    )), rows.length > top.length ? /* @__PURE__ */ React.createElement("div", { className: "text-[10px] text-muted-foreground" }, "+", rows.length - top.length, " more series") : null);
  }
  function Panel({ title, sub, children }) {
    return /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg border border-border p-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-baseline justify-between gap-2" }, /* @__PURE__ */ React.createElement(MiniLabel, null, title), sub ? /* @__PURE__ */ React.createElement("span", { className: "text-[10px] text-muted-foreground" }, sub) : null), /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, children));
  }
  function Chart({ b, fmt, error }) {
    if (error) return /* @__PURE__ */ React.createElement(ErrorBanner, { error });
    if (!b || !Object.keys(b.series).length) return /* @__PURE__ */ React.createElement("div", { className: "py-3 text-xs text-muted-foreground" }, "No data in this range.");
    const all = Object.keys(b.series);
    const series = all.slice(0, 8).map((label, i) => ({ label: label === "_" ? b.name : label, color: PALETTE[i % PALETTE.length], points: b.series[label] }));
    const n = b.buckets.length;
    const withDate = b.bucketS >= 3600;
    const labels = [0, Math.floor(n / 2), n - 1].map((i) => withDate ? fmtAbsTime(b.buckets[i]).slice(5, 16) : fmtClock(b.buckets[i]));
    const bucketLabels = b.buckets.map((t) => fmtAbsTime(t));
    return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement(LineChart, { series, labels, fmt, bucketLabels }), all.length > 8 ? /* @__PURE__ */ React.createElement("div", { className: "text-[10px] text-muted-foreground" }, "showing 8 of ", all.length, " series") : null);
  }
  var PANELS = [
    { key: "tokens", otlp: "hermes.token.usage", group: "token_type", agg: { live: "sum", backend: "sum" } },
    { key: "cost", otlp: "hermes.cost.usage", group: "", agg: { live: "sum", backend: "sum" } },
    { key: "calls", otlp: "hermes.model.usage", group: "model", agg: { live: "count", backend: "sum" } },
    // the duration histogram: its _sum on a backend, the raw points on live
    { key: "tools", otlp: "hermes.tool.duration", group: "tool_name", agg: { live: "avg", backend: "sum" }, prefer: "_sum" },
    // the histogram's _count is the number of tool calls on a backend
    { key: "toolcalls", otlp: "hermes.tool.duration", group: "tool_name", agg: { live: "count", backend: "sum" }, prefer: "_count" },
    { key: "approvals", otlp: "hermes.approval.count", group: "choice", agg: { live: "count", backend: "sum" } },
    { key: "cache", otlp: "hermes.prompt_cache.tokens", group: "token_type", agg: { live: "sum", backend: "sum" } },
    { key: "cpu", otlp: "process.cpu.utilization", group: "", agg: { live: "avg", backend: "avg" } },
    { key: "gpu", otlp: "hw.gpu.utilization", group: "", agg: { live: "avg", backend: "avg" } }
  ];
  function MetricsPage() {
    var _a, _b, _c, _d, _e, _f;
    const { source, setSource, status, isLive } = useSource();
    const active = useActive();
    const [ex, setEx] = useState(() => explorerFromNav(readNav()));
    const range = ex.range;
    const [names, setNames] = useState([]);
    const [error, setError] = useState(null);
    const [panels, setPanels] = useState({});
    const [panelErrors, setPanelErrors] = useState({});
    const [loaded, setLoaded] = useState(false);
    const [customGroup, setCustomGroup] = useState("");
    const [explore, setExplore] = useState(null);
    const [exploreError, setExploreError] = useState(null);
    const [exploring, setExploring] = useState(false);
    const base = isLive ? "/live" : "";
    const entry = ((status == null ? void 0 : status.available) || []).find((b) => b.name === source) || null;
    const canQuery = isLive || !!((entry == null ? void 0 : entry.metrics) || (status == null ? void 0 : status.active) === source && (status == null ? void 0 : status.metrics));
    useEffect(() => {
      if (active) writeNav(navFromExplorer(ex));
    }, [ex, active]);
    const query = useCallback(
      async (name, group, aggregate) => {
        const p = withBackend(new URLSearchParams({ name, agg: aggregate, lookback_hours: String(range.hours), bucket_s: String(range.bucket) }), source);
        if (group) p.set("group_by", group);
        return api(`${base}/metrics/query`, p);
      },
      [base, range, source]
    );
    const load = useCallback(async () => {
      if (!canQuery) return;
      try {
        const p = withBackend(new URLSearchParams({ lookback_hours: String(range.hours) }), source);
        const r = await api(`${base}/metrics/names`, p);
        const list = r.names || [];
        setNames(list);
        setError(null);
        const out = {};
        const errs = {};
        await Promise.all(
          PANELS.map(async (def) => {
            const native = resolveInstrument(list, def.otlp, isLive ? void 0 : def.prefer);
            if (!native) {
              out[def.key] = null;
              return;
            }
            const aggregate = isLive ? def.agg.live : def.prefer === "_count" && !/[_.]count$/.test(native) ? "count" : def.agg.backend;
            try {
              out[def.key] = await query(native, def.group, aggregate);
            } catch (e) {
              out[def.key] = null;
              errs[def.key] = e;
            }
          })
        );
        setPanels(out);
        setPanelErrors(errs);
      } catch (e) {
        setError(e);
      } finally {
        setLoaded(true);
      }
    }, [base, canQuery, isLive, query, range.hours, source]);
    useEffect(() => {
      setLoaded(false);
      load();
    }, [load]);
    usePolling(load, POLL_MS5, active && canQuery);
    const runExplore = useCallback(async () => {
      if (!ex.instrument) return;
      setExploring(true);
      setExploreError(null);
      try {
        setExplore(await query(ex.instrument, ex.groupBy, ex.agg));
      } catch (e) {
        setExploreError(e);
        setExplore(null);
      } finally {
        setExploring(false);
      }
    }, [ex.agg, ex.groupBy, ex.instrument, query]);
    const ranOnce = useState({ done: false })[0];
    useEffect(() => {
      if (ex.instrument && !ranOnce.done && names.length) {
        ranOnce.done = true;
        runExplore();
      }
    }, [ex.instrument, names.length, runExplore, ranOnce]);
    const tokens = panels.tokens || null;
    const cost = panels.cost || null;
    const calls = panels.calls || null;
    const tools = panels.tools || null;
    const toolCallsB = panels.toolcalls || null;
    const toolCalls = isLive ? tools ? fmtInt(tools.points) : null : toolCallsB ? fmtInt(Math.round(seriesTotal(toolCallsB))) : null;
    const approvals = panels.approvals || null;
    const cache = panels.cache || null;
    const totalTokens = tokens ? seriesTotal(tokens) : null;
    const totalCost = cost && cost.points ? seriesTotal(cost) : null;
    const tokenRows = useMemo(() => totalsByLabel(tokens), [tokens]);
    const cacheRows = useMemo(() => totalsByLabel(cache), [cache]);
    const cacheRead = (_d = (_c = (_a = tokenRows.find((r) => /cache/i.test(r.label))) == null ? void 0 : _a.value) != null ? _c : (_b = cacheRows.find((r) => /read|hit/i.test(r.label))) == null ? void 0 : _b.value) != null ? _d : null;
    const cacheAll = (_f = (_e = tokenRows.find((r) => r.label === "input")) == null ? void 0 : _e.value) != null ? _f : cacheRows.reduce((a, r) => a + r.value, 0);
    const unit = (n) => UNITS[n] || UNITS[metricOtlpName(n)] || "";
    const availableSources = ((status == null ? void 0 : status.available) || []).filter((b) => b.metrics).map((b) => b.name);
    const header = /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement(SourceSelect, { source, onChange: setSource, status, need: "metrics" }), /* @__PURE__ */ React.createElement(
      Select,
      {
        value: String(range.hours),
        onValueChange: (v) => setEx((s) => ({ ...s, range: RANGES.find((r) => String(r.hours) === v) || RANGES[1] })),
        className: "otel-w-56 h-8",
        "aria-label": "range"
      },
      RANGES.map((r) => /* @__PURE__ */ React.createElement(SelectOption, { key: r.label, value: String(r.hours) }, rangeLabel(r)))
    )), /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, names.length, " instrument", names.length === 1 ? "" : "s", isLive ? " in the store" : " in this range"));
    if (!canQuery)
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, header, /* @__PURE__ */ React.createElement(Empty, { title: "This source does not serve metrics" }, "Pick the Live source", availableSources.length ? ` or one of: ${availableSources.join(", ")}` : ", or configure a backend whose adapter serves metrics", "."));
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, header, error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error, prefix: "Metrics" }) : null, loaded && names.length === 0 && !error ? /* @__PURE__ */ React.createElement(Empty, { title: "No metrics in this range" }, "Run a Hermes turn, or widen the range. Token usage, cost, tool durations and approvals appear here.") : /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("div", { className: "otel-kpi-grid" }, /* @__PURE__ */ React.createElement(Stat, { label: "Tokens", value: totalTokens != null ? fmtInt(Math.round(totalTokens)) : null, unknownText: "not recorded" }), /* @__PURE__ */ React.createElement(Stat, { label: "Cost", value: totalCost != null ? fmtCost(totalCost) : null, unknownText: "no pricing data", accent: "cost" }), /* @__PURE__ */ React.createElement(Stat, { label: "Model calls", value: calls ? fmtInt(Math.round(seriesTotal(calls))) : null, unknownText: "not recorded" }), /* @__PURE__ */ React.createElement(
      Stat,
      {
        label: "Tool calls",
        value: toolCalls,
        unknownText: "not recorded",
        sub: isLive ? "duration points, one per call" : "from the duration histogram's count"
      }
    ), /* @__PURE__ */ React.createElement(
      Stat,
      {
        label: "Cache read",
        value: cacheRead != null && cacheAll ? `${Math.round(cacheRead / cacheAll * 100)}%` : null,
        sub: cacheRead != null ? `${fmtInt(Math.round(cacheRead))} of ${fmtInt(Math.round(cacheAll))} input tokens` : void 0,
        unknownText: "no cache data"
      }
    )), /* @__PURE__ */ React.createElement("div", { className: "grid gap-3 lg:grid-cols-2" }, /* @__PURE__ */ React.createElement(Panel, { title: "Tokens over time", sub: `by token_type \xB7 per ${range.bucket}s` }, /* @__PURE__ */ React.createElement(Chart, { b: tokens, error: panelErrors.tokens })), /* @__PURE__ */ React.createElement(Panel, { title: "Cost over time", sub: cost && cost.points ? `USD \xB7 per ${range.bucket}s` : "no pricing data for the models used" }, /* @__PURE__ */ React.createElement(Chart, { b: cost, fmt: fmtCost, error: panelErrors.cost })), /* @__PURE__ */ React.createElement(Panel, { title: "Tokens by type" }, /* @__PURE__ */ React.createElement(BarList, { rows: totalsByLabel(tokens), color: "var(--otel-chart-1)" })), /* @__PURE__ */ React.createElement(Panel, { title: "Calls by model" }, /* @__PURE__ */ React.createElement(BarList, { rows: totalsByLabel(calls), color: "var(--otel-chart-4)" })), /* @__PURE__ */ React.createElement(Panel, { title: isLive ? "Avg tool duration" : "Tool duration (sum)", sub: "ms" }, /* @__PURE__ */ React.createElement(BarList, { rows: isLive ? meanByLabel(tools) : totalsByLabel(tools), fmt: fmtDurationMs, color: "var(--otel-chart-3)" })), /* @__PURE__ */ React.createElement(Panel, { title: "Approvals by choice" }, /* @__PURE__ */ React.createElement(BarList, { rows: totalsByLabel(approvals), color: "var(--otel-chart-5)" })), panels.cpu ? /* @__PURE__ */ React.createElement(Panel, { title: "CPU", sub: "utilisation ratio, avg per bucket" }, /* @__PURE__ */ React.createElement(Chart, { b: panels.cpu, error: panelErrors.cpu })) : null, panels.gpu ? /* @__PURE__ */ React.createElement(Panel, { title: "GPU", sub: "utilisation ratio, avg per bucket" }, /* @__PURE__ */ React.createElement(Chart, { b: panels.gpu, error: panelErrors.gpu })) : null), /* @__PURE__ */ React.createElement(Panel, { title: "Explore any instrument", sub: "server-side buckets; group by an attribute" }, /* @__PURE__ */ React.createElement(
      "form",
      {
        className: "otel-search-grid",
        onSubmit: (e) => {
          e.preventDefault();
          setEx((s) => ({ ...s, groupBy: customGroup.trim() || s.groupBy }));
          runExplore();
        }
      },
      /* @__PURE__ */ React.createElement(Select, { value: ex.instrument, onValueChange: (v) => setEx((s) => ({ ...s, instrument: v })), className: "h-8", "aria-label": "instrument" }, /* @__PURE__ */ React.createElement(SelectOption, { value: "" }, "pick an instrument\u2026"), names.map((n) => /* @__PURE__ */ React.createElement(SelectOption, { key: n.name, value: n.name }, `${n.name}${n.count != null ? ` (${n.count})` : ""}${unit(n.otlp_name || n.name) ? ` \xB7 ${unit(n.otlp_name || n.name)}` : ""}`))),
      /* @__PURE__ */ React.createElement(
        Select,
        {
          value: GROUP_KEYS.includes(ex.groupBy) ? ex.groupBy : "",
          onValueChange: (v) => setEx((s) => ({ ...s, groupBy: v })),
          className: "h-8",
          "aria-label": "group by"
        },
        GROUP_KEYS.map((k) => /* @__PURE__ */ React.createElement(SelectOption, { key: k, value: k }, k ? `group by ${k}` : "no grouping"))
      ),
      /* @__PURE__ */ React.createElement(
        Input,
        {
          className: "h-8",
          placeholder: "or any attribute",
          value: customGroup,
          onChange: (e) => setCustomGroup(e.target.value),
          "aria-label": "custom group by"
        }
      ),
      /* @__PURE__ */ React.createElement(Select, { value: ex.agg, onValueChange: (v) => setEx((s) => ({ ...s, agg: v })), className: "h-8", "aria-label": "aggregation" }, AGGS.map((a) => /* @__PURE__ */ React.createElement(SelectOption, { key: a, value: a }, a))),
      /* @__PURE__ */ React.createElement(Button, { type: "submit", size: "sm", disabled: !ex.instrument || exploring }, exploring ? "Querying\u2026" : "Query")
    ), ex.instrument ? /* @__PURE__ */ React.createElement("div", { className: "mt-3 space-y-3" }, /* @__PURE__ */ React.createElement(Chart, { b: explore, error: exploreError }), /* @__PURE__ */ React.createElement("div", { className: "text-[11px] text-muted-foreground" }, explore ? `${explore.points} point${explore.points === 1 ? "" : "s"} \xB7 ${Object.keys(explore.series).length} series \xB7 ${explore.agg} per ${explore.bucketS}s${unit(ex.instrument) ? ` \xB7 ${unit(ex.instrument)}` : ""}${explore.cumulative ? " \xB7 cumulative counter shown as increases" : ""}${explore.instrument ? ` \xB7 ${explore.instrument}` : ""}` : exploreError ? "" : "press Query"), /* @__PURE__ */ React.createElement(BarList, { rows: ex.agg === "avg" ? meanByLabel(explore) : totalsByLabel(explore) })) : null)));
  }

  // src/settings-lib.ts
  var SIGNALS = ["traces", "metrics", "logs"];
  function signalPill(signal, s, typeName) {
    const how = s.configured === "auto" ? "by default" : `${signal}: ${s.configured === "on"} in the config file`;
    if (s.exported && s.supported) {
      return { label: `${signal} on`, cls: "on", title: `${typeName} accepts OTLP ${signal}; exported (${how})` };
    }
    if (s.exported) {
      return {
        label: `${signal} forced`,
        cls: "forced",
        title: `${typeName} does not accept OTLP ${signal}, but the entry sets ${signal}: true; exports fail unless a collector fronts it`
      };
    }
    if (s.supported) {
      return { label: `${signal} off`, cls: "off", title: `${typeName} accepts OTLP ${signal}; not exported (${how})` };
    }
    return { label: `${signal} n/a`, cls: "na", title: `${typeName} does not accept OTLP ${signal}; not exported` };
  }
  function queryCapabilityLine(q, typeName) {
    if (!q) return null;
    if (!q.supported) {
      return {
        text: "not queryable from this dashboard",
        title: `no query adapter for ${typeName}; the Traces, Metrics and Logs tabs use the Live source or another backend`
      };
    }
    const what = ["traces", ...q.metrics ? ["metrics"] : [], ...q.logs ? ["logs"] : []];
    return {
      text: `dashboard queries ${what.join(", ")}`,
      title: `the Traces${q.metrics ? ", Metrics" : ""}${q.logs ? ", Logs" : ""} tab${what.length > 1 ? "s" : ""} can read from this backend`
    };
  }
  function showTypeBadge(b) {
    const n = b.name.toLowerCase();
    return n !== b.type.toLowerCase() && n !== b.display_type.toLowerCase();
  }
  var DOCS_BASE = "https://briancaffey.github.io/hermes-otel";
  function renderDescription(desc) {
    const out = [];
    const re = /`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
    let last = 0;
    let m;
    while (m = re.exec(desc)) {
      if (m.index > last) out.push({ t: "text", v: desc.slice(last, m.index) });
      if (m[1] != null) out.push({ t: "code", v: m[1] });
      else out.push({ t: "link", v: m[2], href: m[3].startsWith("/") ? DOCS_BASE + m[3] : m[3] });
      last = m.index + m[0].length;
    }
    if (last < desc.length) out.push({ t: "text", v: desc.slice(last) });
    return out;
  }
  function fmtSettingValue(f) {
    const v = f.value;
    if (v == null) return "unset";
    if (f.kind === "bool") return v ? "true" : "false";
    if (f.kind === "backends") {
      const n = Array.isArray(v) ? v.length : 0;
      return n === 1 ? "1 backend" : `${n} backends`;
    }
    if (f.kind === "map") {
      return Object.entries(v).map(([k, x]) => `${k}=${x}`).join(", ");
    }
    return String(v);
  }
  function filterFields(fields, opts) {
    const q = (opts.query || "").trim().toLowerCase();
    return fields.filter((f) => {
      if (opts.changedOnly && !f.changed && !f.env_invalid && !f.file_invalid) return false;
      if (!q) return true;
      const hay = [f.key, f.group, f.description, fmtSettingValue(f), f.env_var || "", f.source].join(" ").toLowerCase();
      return q.split(/\s+/).every((w) => hay.includes(w));
    });
  }
  function groupFields(fields, groups) {
    const order = groups.length ? groups : Array.from(new Set(fields.map((f) => f.group)));
    return order.map((g) => ({ group: g, fields: fields.filter((f) => f.group === g) })).filter((g) => g.fields.length > 0);
  }
  function pathSourceLabel(ps) {
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
  var ENV_GROUP_LABELS = {
    override: "Setting overrides (HERMES_OTEL_*)",
    plugin: "Plugin",
    hermes: "Hermes",
    backend: "Backends: single-backend mode and credential fallbacks",
    langsmith: "LangSmith",
    otel: "OpenTelemetry SDK",
    other: "Other OTEL_* / HERMES_OTEL_* variables set here"
  };
  var ENV_GROUP_ORDER = ["override", "plugin", "hermes", "backend", "langsmith", "otel", "other"];
  function groupEnv(env, showUnset, query = "") {
    const seen = /* @__PURE__ */ new Set();
    const q = query.trim().toLowerCase();
    const matches = (e) => !q || [e.name, e.description, e.value || "", e.maps_to || ""].join(" ").toLowerCase().includes(q);
    const groups = [...ENV_GROUP_ORDER, ...env.map((e) => e.group).filter((g) => !ENV_GROUP_ORDER.includes(g))];
    return groups.filter((g) => seen.has(g) ? false : (seen.add(g), true)).map((g) => ({
      group: g,
      label: ENV_GROUP_LABELS[g] || g,
      entries: env.filter((e) => e.group === g && (showUnset || e.set) && matches(e))
    })).filter((g) => g.entries.length > 0);
  }
  function envCounts(env) {
    return { set: env.filter((e) => e.set).length, known: env.length };
  }
  function sourceNote(f) {
    if (f.env_invalid && f.env_var) return `${f.env_var}=${f.env_raw} is not a valid ${f.kind}; ignored`;
    if (f.derived_from && f.derived_from.length) return `follows ${f.derived_from.join(", ")}`;
    if (f.file_invalid) return `file value "${f.file_value}" is not a valid ${f.kind}; ignored`;
    if (f.source === "env" && f.file_value != null) return `overrides the file's ${fmtSettingValue({ kind: f.kind, value: f.file_value })}`;
    if (f.source === "env" && f.env_var) return `from ${f.env_var}`;
    return null;
  }

  // src/settings.tsx
  var VIEWS = [
    { id: "structured", label: "Structured" },
    { id: "raw", label: "Raw YAML" },
    { id: "env", label: "Environment" }
  ];
  function Description({ text }) {
    return /* @__PURE__ */ React.createElement("span", null, renderDescription(text).map(
      (tok, i) => tok.t === "code" ? /* @__PURE__ */ React.createElement("code", { key: i, className: "otel-code" }, tok.v) : tok.t === "link" ? /* @__PURE__ */ React.createElement("a", { key: i, className: "otel-link", href: tok.href, target: "_blank", rel: "noreferrer" }, tok.v) : /* @__PURE__ */ React.createElement("span", { key: i }, tok.v)
    ));
  }
  function SourceBadge({ source }) {
    const title = source === "env" ? "set by a HERMES_OTEL_* environment variable" : source === "file" ? "set in the config file" : "the built-in default";
    return /* @__PURE__ */ React.createElement("span", { className: cn("otel-src", `otel-src-${source}`), title }, source);
  }
  function Value({ f }) {
    const v = f.value;
    if (v == null) return /* @__PURE__ */ React.createElement("span", { className: "otel-unset" }, "unset");
    if (f.kind === "bool") return /* @__PURE__ */ React.createElement("span", { className: cn("otel-pill", v ? "otel-pill-on" : "otel-pill-off") }, v ? "on" : "off");
    if (f.kind === "map")
      return /* @__PURE__ */ React.createElement("div", { className: "otel-kv" }, Object.entries(v).map(([k, x]) => /* @__PURE__ */ React.createElement("div", { key: k, className: "font-mono text-xs" }, /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, k, ":"), " ", String(x))));
    if (f.kind === "backends") {
      const n = Array.isArray(v) ? v.length : 0;
      return /* @__PURE__ */ React.createElement("span", { className: "font-mono text-xs" }, n, " configured");
    }
    return /* @__PURE__ */ React.createElement("span", { className: "font-mono text-xs break-all" }, String(v));
  }
  function DefaultHint({ f }) {
    if (!f.changed || f.kind === "backends" || f.kind === "map") return null;
    const d = f.default;
    const text = d == null ? "unset" : f.kind === "bool" ? d ? "on" : "off" : String(d);
    return /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-muted-foreground" }, "default ", text);
  }
  function FieldRow({ f }) {
    const note = sourceNote(f);
    const warn = f.env_invalid || f.file_invalid;
    return /* @__PURE__ */ React.createElement("div", { className: cn("otel-settings-row", f.changed && "otel-changed") }, /* @__PURE__ */ React.createElement("div", { className: "min-w-0" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: "font-mono text-xs text-foreground" }, f.key), f.env_var ? /* @__PURE__ */ React.createElement("span", { className: "font-mono text-[10px] text-muted-foreground/70", title: "environment variable that overrides this setting" }, f.env_var) : /* @__PURE__ */ React.createElement("span", { className: "text-[10px] text-muted-foreground/70" }, "yaml only")), /* @__PURE__ */ React.createElement("div", { className: "mt-0.5 text-[11px] leading-snug text-muted-foreground" }, /* @__PURE__ */ React.createElement(Description, { text: f.description }))), /* @__PURE__ */ React.createElement("div", { className: "min-w-0" }, /* @__PURE__ */ React.createElement(Value, { f }), /* @__PURE__ */ React.createElement("div", { className: "mt-0.5 flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement(DefaultHint, { f }), note ? /* @__PURE__ */ React.createElement("span", { className: cn("text-[11px]", warn ? "otel-warn" : "text-muted-foreground") }, note) : null)), /* @__PURE__ */ React.createElement("div", { className: "otel-self-center" }, /* @__PURE__ */ React.createElement(SourceBadge, { source: f.source })));
  }
  function Row({ k, children, title }) {
    return /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground", title }, k), /* @__PURE__ */ React.createElement("span", { className: "min-w-0 break-all" }, children));
  }
  function BackendCard({ b, q }) {
    var _a;
    const href = b.ui.url;
    const query = queryCapabilityLine(q, b.display_type);
    const metricsOn = (_a = b.signals.metrics) == null ? void 0 : _a.exported;
    return /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg border border-border px-3 py-2.5" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2" }, href ? /* @__PURE__ */ React.createElement("a", { className: "otel-backend-name", href, target: "_blank", rel: "noreferrer noopener", title: `${b.ui.note} \xB7 opens ${href} in a new window` }, b.name, /* @__PURE__ */ React.createElement(IconExternal, { size: 12, className: "otel-backend-ext" })) : /* @__PURE__ */ React.createElement("span", { className: "text-sm font-medium", title: b.ui.note }, b.name), showTypeBadge(b) ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px] uppercase" }, b.display_type) : null, b.docs_path ? /* @__PURE__ */ React.createElement(
      "a",
      {
        className: "otel-link text-[11px] text-muted-foreground",
        href: DOCS_BASE + b.docs_path,
        target: "_blank",
        rel: "noreferrer",
        title: `${b.display_type} backend docs`
      },
      "docs"
    ) : null, /* @__PURE__ */ React.createElement("span", { className: "ml-auto flex flex-wrap items-center gap-1", title: "what the plugin exports to this backend" }, /* @__PURE__ */ React.createElement("span", { className: "text-[10px] uppercase tracking-wide text-muted-foreground" }, "export"), SIGNALS.map((sig) => {
      const st = b.signals[sig];
      if (!st) return null;
      const pill = signalPill(sig, st, b.display_type);
      return /* @__PURE__ */ React.createElement("span", { key: sig, className: cn("otel-pill", `otel-pill-${pill.cls}`), title: pill.title }, pill.label);
    }))), query ? /* @__PURE__ */ React.createElement("div", { className: "mt-1 text-[11px] text-muted-foreground", title: query.title }, /* @__PURE__ */ React.createElement("span", { className: "text-[10px] uppercase tracking-wide" }, "query"), " \xB7 ", query.text) : null, /* @__PURE__ */ React.createElement("div", { className: "otel-attr-table mt-2 text-xs" }, Object.entries(b.fields).map(([k, v]) => /* @__PURE__ */ React.createElement(Row, { key: k, k }, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, String(v)))), /* @__PURE__ */ React.createElement(Row, { k: "ui", title: "the link the name opens; set ui_url on the entry to override" }, href ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("a", { className: "otel-link font-mono", href, target: "_blank", rel: "noreferrer noopener" }, href), /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, " \xB7 ", b.ui.source === "file" ? "ui_url" : "derived")) : /* @__PURE__ */ React.createElement("span", { className: "otel-unset" }, b.ui.note)), metricsOn ? /* @__PURE__ */ React.createElement(Row, { k: "temporality", title: "aggregation temporality of this backend's metric reader" }, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, b.metrics_temporality.value), /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, " \xB7 ", b.metrics_temporality.source)) : null, Object.entries(b.query_fields || {}).map(([k, v]) => /* @__PURE__ */ React.createElement(Row, { key: `q-${k}`, k, title: "read by the dashboard's query adapter, not by the exporter" }, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, String(v)), /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, " \xB7 query"))), b.headers ? Object.entries(b.headers).map(([k, v]) => /* @__PURE__ */ React.createElement(Row, { key: `h-${k}`, k: `header ${k}` }, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, String(v)))) : null, b.credentials.map((c) => {
      var _a2;
      return /* @__PURE__ */ React.createElement(Row, { key: c.field, k: c.field }, c.set ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, (_a2 = c.value) != null ? _a2 : "set"), /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, " \xB7 ", c.source)) : /* @__PURE__ */ React.createElement("span", { className: "otel-warn" }, c.source || "not set"));
    })));
  }
  function ConfigFileLine({ r }) {
    const c = r.config;
    return /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg border border-border px-3 py-2 text-xs" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-x-3 gap-y-1" }, /* @__PURE__ */ React.createElement(MiniLabel, null, "config file"), c.path ? /* @__PURE__ */ React.createElement("span", { className: "font-mono break-all" }, c.path) : /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, "none found"), /* @__PURE__ */ React.createElement("span", { className: "otel-pill", title: "how the file was chosen: HERMES_OTEL_CONFIG, then $HERMES_HOME/hermes_otel.yaml, then the plugin directory" }, pathSourceLabel(c.path_source)), c.exists && c.mtime ? /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground", title: fmtAbsTime(c.mtime * 1e9) }, "edited ", fmtTimeAgo(c.mtime * 1e9)) : null, c.path && !c.exists ? /* @__PURE__ */ React.createElement("span", { className: "otel-warn" }, "file does not exist; defaults and environment variables apply") : null, c.exists && !c.parse_ok ? /* @__PURE__ */ React.createElement("span", { className: "otel-warn" }, "file could not be parsed; defaults and environment variables apply") : null), !c.path ? /* @__PURE__ */ React.createElement("div", { className: "mt-1 text-muted-foreground" }, "Create ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, c.durable_path), " to change settings; see the", " ", /* @__PURE__ */ React.createElement("a", { className: "otel-link", href: `${DOCS_BASE}/configuration/overview`, target: "_blank", rel: "noreferrer" }, "configuration guide"), ".") : null, c.unknown_keys.length ? /* @__PURE__ */ React.createElement("div", { className: "mt-1 text-muted-foreground" }, "Also in the file:", " ", c.unknown_keys.map((u, i) => /* @__PURE__ */ React.createElement("span", { key: u.key }, i ? ", " : "", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, u.key), u.note ? /* @__PURE__ */ React.createElement(React.Fragment, null, " ", "(", /* @__PURE__ */ React.createElement(Description, { text: u.note }), ")") : /* @__PURE__ */ React.createElement("span", { className: "otel-warn" }, " (not a known setting)")))) : null, c.deprecated_keys && c.deprecated_keys.length ? /* @__PURE__ */ React.createElement("div", { className: "mt-1 text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", { className: "otel-warn" }, "deprecated spelling:"), " ", c.deprecated_keys.map((u, i) => /* @__PURE__ */ React.createElement("span", { key: u.key }, i ? ", " : "", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, u.key))), " ", "(the ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "logs:"), " block is the preferred form; see the", " ", /* @__PURE__ */ React.createElement("a", { className: "otel-link", href: `${DOCS_BASE}/configuration/logs#configuration`, target: "_blank", rel: "noreferrer" }, "logs configuration"), ")") : null);
  }
  function SettingsPage() {
    var _a, _b;
    const { status } = useSource();
    const [report, setReport] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(false);
    const [view, setView] = useState("structured");
    const [reveal, setReveal] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const [query, setQuery] = useState("");
    const [changedOnly, setChangedOnly] = useState(false);
    const [rawMode, setRawMode] = useState("file");
    const [showUnset, setShowUnset] = useState(false);
    const load = useCallback(async () => {
      setLoading(true);
      try {
        const r = await api("/settings", { reveal: reveal ? "true" : "false" });
        setReport(r);
        setError(null);
      } catch (e) {
        setError(e);
      } finally {
        setLoading(false);
      }
    }, [reveal]);
    useEffect(() => {
      load();
    }, [load]);
    const fields = (report == null ? void 0 : report.fields) || [];
    const shown = useMemo(() => filterFields(fields, { query, changedOnly }), [fields, query, changedOnly]);
    const groups = useMemo(() => groupFields(shown, (report == null ? void 0 : report.groups) || []), [shown, report]);
    const backends = ((_a = fields.find((f) => f.key === "backends")) == null ? void 0 : _a.value) || [];
    const invalidEnv = useMemo(() => {
      const out = {};
      for (const f of fields) if (f.env_invalid && f.env_var) out[f.env_var] = `not a valid ${f.kind}; ignored`;
      return out;
    }, [fields]);
    const cap = report == null ? void 0 : report.capture_summary;
    const queryCaps = useMemo(() => {
      const out = {};
      for (const a of (status == null ? void 0 : status.available) || []) out[a.name] = { supported: a.supported, metrics: a.metrics, logs: a.logs };
      return out;
    }, [status]);
    const q = query.trim().toLowerCase();
    const rawText = rawMode === "file" ? (report == null ? void 0 : report.config.raw) || "" : (report == null ? void 0 : report.effective_yaml) || "";
    const rawMatches = q ? rawText.split("\n").filter((l) => l.toLowerCase().includes(q)).length : 0;
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: "text-base font-semibold tracking-tight" }, "Settings"), report ? /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, "hermes-otel ", report.process.plugin_version || "?", " \xB7 resolved ", fmtTimeAgo(report.resolved_at * 1e9)) : null, /* @__PURE__ */ React.createElement("div", { className: "ml-auto flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement(Segmented, { value: view, onChange: setView, label: "settings view", options: VIEWS }), reveal ? /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: () => setReveal(false), title: "mask credential values again" }, "hide secrets") : confirming ? /* @__PURE__ */ React.createElement("span", { className: "inline-flex items-center gap-2 text-[11px] text-muted-foreground" }, "credential values will be shown on this page", /* @__PURE__ */ React.createElement(
      Button,
      {
        variant: "outline",
        size: "sm",
        onClick: () => {
          setConfirming(false);
          setReveal(true);
        }
      },
      "show them"
    ), /* @__PURE__ */ React.createElement(Button, { variant: "ghost", size: "sm", onClick: () => setConfirming(false) }, "keep masked")) : /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: () => setConfirming(true), title: "credential values are masked unless you ask" }, "show secrets\u2026"), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: load, disabled: loading, title: "re-read the file and environment" }, /* @__PURE__ */ React.createElement(IconRefresh, { size: 13, className: loading ? "otel-spin" : "" }), /* @__PURE__ */ React.createElement("span", { className: "ml-1" }, "reload")))), error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error, prefix: "Settings" }) : null, !report && !error ? /* @__PURE__ */ React.createElement("div", { className: "text-sm text-muted-foreground" }, "Loading settings\u2026") : null, report ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement(ConfigFileLine, { r: report }), /* @__PURE__ */ React.createElement("div", { className: "otel-kpi-grid" }, /* @__PURE__ */ React.createElement(Stat, { label: "Settings", value: report.fields.length, sub: `${report.counts.changed} changed from default` }), /* @__PURE__ */ React.createElement(Stat, { label: "From file", value: report.counts.file, sub: report.config.exists ? "in the config file" : "no file" }), /* @__PURE__ */ React.createElement(Stat, { label: "From env", value: report.counts.env, sub: "HERMES_OTEL_* variables" }), /* @__PURE__ */ React.createElement(Stat, { label: "Backends", value: backends.length, sub: backends.map((b) => b.name).join(", ") || "live store only" }), /* @__PURE__ */ React.createElement(Stat, { label: "Content", value: cap ? cap.mode : "?", sub: cap ? cap.detail : "", accent: (cap == null ? void 0 : cap.mode) === "full" ? "cost" : void 0 })), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement(
      Input,
      {
        value: query,
        onChange: (e) => setQuery(e.target.value),
        placeholder: view === "structured" ? "filter by name, value, description\u2026" : view === "raw" ? "find in the YAML\u2026" : "filter variables\u2026",
        className: "otel-w-56 h-8 text-xs",
        "aria-label": "filter"
      }
    ), view === "structured" ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("label", { className: "inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground" }, /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: changedOnly, onChange: (e) => setChangedOnly(e.target.checked) }), "changed from default only"), /* @__PURE__ */ React.createElement("span", { className: "ml-auto text-[11px] text-muted-foreground" }, shown.length, " of ", fields.length, " \xB7 precedence: ", /* @__PURE__ */ React.createElement("span", { className: "otel-src otel-src-env" }, "env"), " over", " ", /* @__PURE__ */ React.createElement("span", { className: "otel-src otel-src-file" }, "file"), " over ", /* @__PURE__ */ React.createElement("span", { className: "otel-src otel-src-default" }, "default"))) : view === "raw" && q ? /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-muted-foreground" }, rawMatches, " line", rawMatches === 1 ? "" : "s", " match") : null), view === "structured" ? /* @__PURE__ */ React.createElement(React.Fragment, null, groups.length === 0 ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground" }, "No setting matches.") : null, groups.map((g) => /* @__PURE__ */ React.createElement("div", { key: g.group, className: "space-y-1" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2 pt-1" }, /* @__PURE__ */ React.createElement(MiniLabel, null, g.group), /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-muted-foreground/70" }, g.fields.length)), g.group === "Backends" ? /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, g.fields.map((f) => /* @__PURE__ */ React.createElement(FieldRow, { key: f.key, f })), backends.length ? /* @__PURE__ */ React.createElement("div", { className: "otel-backend-grid" }, backends.map((b, i) => /* @__PURE__ */ React.createElement(BackendCard, { key: `${b.name}-${i}`, b, q: queryCaps[b.name] }))) : /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "No ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "backends:"), " entry. Telemetry stays in the live store on this machine; single-backend environment variables, if any, are listed under Environment.")) : /* @__PURE__ */ React.createElement("div", { className: "otel-settings-list" }, g.fields.map((f) => /* @__PURE__ */ React.createElement(FieldRow, { key: f.key, f })))))) : null, view === "raw" ? /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement(
      Segmented,
      {
        value: rawMode,
        onChange: setRawMode,
        label: "raw view",
        options: [
          { id: "file", label: "File as written" },
          { id: "effective", label: "Effective config" }
        ]
      }
    ), /* @__PURE__ */ React.createElement("span", { className: "min-w-0 flex-1 truncate text-[11px] text-muted-foreground", title: rawMode === "file" ? report.config.path || "" : "" }, rawMode === "file" ? report.config.exists ? `${report.config.path}${reveal ? "" : " \xB7 secrets masked"}` : "no config file to show" : "every setting after env, file and defaults are applied; each key notes its source"), /* @__PURE__ */ React.createElement("span", { className: "shrink-0" }, /* @__PURE__ */ React.createElement(CopyButton, { text: rawText, label: "copy" }))), rawMode === "file" ? report.config.raw != null ? /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-raw" }, q ? highlightLines(report.config.raw, q) : report.config.raw) : /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, "No config file"), report.config.raw_error ? report.config.raw_error : /* @__PURE__ */ React.createElement(React.Fragment, null, "Create ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, report.config.durable_path), ". The Effective config view is a starting point you can paste in.")) : /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-raw" }, q ? highlightLines(report.effective_yaml, q) : report.effective_yaml)) : null, view === "env" ? /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, ((_b = report.env_notices) != null ? _b : []).map((n) => /* @__PURE__ */ React.createElement("div", { key: n, className: "border border-dashed border-border px-3 py-2 text-[11px] text-muted-foreground", role: "status" }, n)), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-muted-foreground" }, envCounts(report.env).set, " set of ", envCounts(report.env).known, " the plugin reads, as seen by the process that answered (pid", " ", report.process.pid, "); the gateway may differ"), /* @__PURE__ */ React.createElement("label", { className: "ml-auto inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground" }, /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: showUnset, onChange: (e) => setShowUnset(e.target.checked) }), "show unset variables")), groupEnv(report.env, showUnset, q).length === 0 ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, q ? "No variable matches" : "No plugin environment variables set"), q ? "" : 'Every setting comes from the file or its default. Tick "show unset variables" to see every variable the plugin would read.') : null, groupEnv(report.env, showUnset, q).map((g) => /* @__PURE__ */ React.createElement("div", { key: g.group, className: "space-y-1" }, /* @__PURE__ */ React.createElement(MiniLabel, null, g.label), /* @__PURE__ */ React.createElement("div", { className: "otel-settings-list" }, g.entries.map((e) => /* @__PURE__ */ React.createElement("div", { key: e.name, className: cn("otel-env-row", e.set && "otel-changed") }, /* @__PURE__ */ React.createElement("div", { className: "min-w-0" }, /* @__PURE__ */ React.createElement("div", { className: "font-mono text-xs break-all" }, e.name), e.maps_to ? /* @__PURE__ */ React.createElement("div", { className: "text-[10px] text-muted-foreground/70" }, "sets ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, e.maps_to)) : null), /* @__PURE__ */ React.createElement("div", { className: "min-w-0" }, e.set ? /* @__PURE__ */ React.createElement("span", { className: "font-mono text-xs break-all" }, e.value) : /* @__PURE__ */ React.createElement("span", { className: "otel-unset" }, "unset"), invalidEnv[e.name] ? /* @__PURE__ */ React.createElement("div", { className: "otel-warn text-[11px]" }, invalidEnv[e.name]) : null), /* @__PURE__ */ React.createElement("div", { className: "text-[11px] leading-snug text-muted-foreground" }, /* @__PURE__ */ React.createElement(Description, { text: e.description })))))))) : null, /* @__PURE__ */ React.createElement("div", { className: "text-[11px] text-muted-foreground" }, report.process.note)) : null);
  }
  function highlightLines(text, q) {
    const needle = q.toLowerCase();
    return text.split("\n").map((line, i) => /* @__PURE__ */ React.createElement(React.Fragment, { key: i }, line.toLowerCase().includes(needle) ? /* @__PURE__ */ React.createElement("mark", { className: "otel-mark" }, line) : line, "\n"));
  }

  // src/index.tsx
  var TABS = [
    { id: "live", label: "Live", Icon: IconActivity, Page: LivePage },
    { id: "traces", label: "Traces", Icon: IconList, Page: TracesPage },
    { id: "metrics", label: "Metrics", Icon: IconChart, Page: MetricsPage },
    { id: "logs", label: "Logs", Icon: IconScrollText, Page: LogsPage },
    { id: "settings", label: "Settings", Icon: IconSettings, Page: SettingsPage }
  ];
  var ActiveContext = createContext ? createContext(true) : null;
  function useActive() {
    if (!ActiveContext || !useContext) return true;
    const v = useContext(ActiveContext);
    return v == null ? true : v;
  }
  var PageBoundaryImpl = class extends React.Component {
    constructor() {
      super(...arguments);
      this.state = { error: null };
    }
    static getDerivedStateFromError(error) {
      return { error };
    }
    componentDidCatch(error) {
      console.error("[hermes_otel] page crashed", error);
    }
    render() {
      const { error } = this.state;
      const { name, children } = this.props;
      if (!error) return children;
      return /* @__PURE__ */ React.createElement("div", { role: "alert", className: "otel-error-banner" }, /* @__PURE__ */ React.createElement(IconAlert, { size: 14, className: "shrink-0" }), /* @__PURE__ */ React.createElement("div", { className: "min-w-0 space-y-1" }, /* @__PURE__ */ React.createElement("div", null, "The ", name, " tab hit an error while rendering."), /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-error-detail" }, String((error == null ? void 0 : error.stack) || error)), /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-btn", onClick: () => this.setState({ error: null }) }, "Try again")));
    }
  };
  var PageBoundary = PageBoundaryImpl;
  function schemeOf(background) {
    const m = /^#?([0-9a-f]{6})$/i.exec(background.trim()) || null;
    let rgb = null;
    if (m) rgb = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
    else {
      const n = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/i.exec(background);
      const c = /color\(\s*srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/i.exec(background);
      if (n) rgb = [Number(n[1]), Number(n[2]), Number(n[3])];
      else if (c) rgb = [Number(c[1]) * 255, Number(c[2]) * 255, Number(c[3]) * 255];
      else {
        const o = /oklch\(\s*([\d.]+%?)/i.exec(background);
        if (o) {
          const l = o[1].endsWith("%") ? Number(o[1].slice(0, -1)) / 100 : Number(o[1]);
          return l > 0.6 ? "light" : "dark";
        }
        const h = /hsla?\(\s*[\d.]+[,\s]+[\d.]+%?[,\s]+([\d.]+)%/i.exec(background);
        if (h) return Number(h[1]) > 60 ? "light" : "dark";
      }
    }
    if (!rgb) return "dark";
    const lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255;
    return lum > 0.6 ? "light" : "dark";
  }
  function useScheme() {
    const theme = useTheme ? useTheme() : void 0;
    const name = (theme == null ? void 0 : theme.themeName) || "";
    return useMemo(() => {
      try {
        const painted = getComputedStyle(document.body).backgroundColor;
        if (painted && !/rgba\(\s*0,\s*0,\s*0,\s*0\)|transparent/i.test(painted)) return schemeOf(painted);
        const probe = document.createElement("div");
        probe.style.background = "var(--background, var(--color-background))";
        probe.style.display = "none";
        document.body.appendChild(probe);
        const bg = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return schemeOf(bg);
      } catch {
        return "dark";
      }
    }, [name]);
  }
  function OtelDashboard() {
    const [tab, setTabState] = useState(() => {
      const t = readNav().tab;
      return TABS.some((x) => x.id === t) ? t : "live";
    });
    const setTab = (id) => {
      setTabState(id);
      writeNav({ tab: id, ...clearOtherTabs(id) });
    };
    useEffect(() => {
      const onNav = (e) => {
        const d = e.detail || {};
        if (d.tab && TABS.some((t) => t.id === d.tab)) setTabState(d.tab);
      };
      window.addEventListener(NAV_EVENT, onNav);
      return () => window.removeEventListener(NAV_EVENT, onNav);
    }, []);
    const [visited, setVisited] = useState(() => ({ [tab]: true }));
    useEffect(() => {
      setVisited((v) => v[tab] ? v : { ...v, [tab]: true });
    }, [tab]);
    const scheme = useScheme();
    const tz = useMemo(() => localTimezone(), []);
    const onKey = (e, i) => {
      var _a, _b, _c;
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
      e.preventDefault();
      const j = e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : (i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length;
      setTab(TABS[j].id);
      (_c = (_b = (_a = e.currentTarget.parentElement) == null ? void 0 : _a.children[j]) == null ? void 0 : _b.focus) == null ? void 0 : _c.call(_b);
    };
    return /* @__PURE__ */ React.createElement("div", { className: "otel-root space-y-4", "data-otel-scheme": scheme }, /* @__PURE__ */ React.createElement("div", { className: "otel-tabs flex items-center gap-1 border-b border-border", role: "tablist", "aria-label": "OTel views" }, TABS.map((t, i) => {
      const on = t.id === tab;
      const Icon = t.Icon;
      return /* @__PURE__ */ React.createElement(
        "button",
        {
          key: t.id,
          role: "tab",
          id: `otel-tab-${t.id}`,
          "aria-selected": on,
          "aria-controls": `otel-panel-${t.id}`,
          tabIndex: on ? 0 : -1,
          onClick: () => setTab(t.id),
          onKeyDown: (e) => onKey(e, i),
          className: cn(
            "otel-tab inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors",
            on ? "otel-tab-active text-foreground" : "text-muted-foreground hover:text-foreground"
          )
        },
        /* @__PURE__ */ React.createElement(Icon, { size: 15 }),
        t.label
      );
    }), /* @__PURE__ */ React.createElement("span", { className: "ml-auto pr-1 font-mono text-[11px] text-muted-foreground", title: "absolute times are shown in this timezone" }, tz ? `${tz} \xB7 ` : "", "hermes-otel")), /* @__PURE__ */ React.createElement(SourceProvider, null, TABS.map((t) => {
      const on = t.id === tab;
      const Page = t.Page;
      const body = /* @__PURE__ */ React.createElement(PageBoundary, { name: t.label }, /* @__PURE__ */ React.createElement(Page, null));
      return /* @__PURE__ */ React.createElement("div", { key: t.id, role: "tabpanel", id: `otel-panel-${t.id}`, "aria-labelledby": `otel-tab-${t.id}`, hidden: !on }, !visited[t.id] && !on ? null : ActiveContext ? /* @__PURE__ */ React.createElement(ActiveContext.Provider, { value: on }, body) : body);
    })));
  }
  if (sdkOk) {
    register("hermes_otel", OtelDashboard);
  } else {
    console.error("[hermes_otel] dashboard SDK unavailable \u2014 not registering");
  }
})();
