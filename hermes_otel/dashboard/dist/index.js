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
  var C = SDK.components || {};
  var { Card, CardHeader, CardContent, Badge, Button, Input, Label, Select, SelectOption, Checkbox } = C;
  var fetchJSON = SDK.fetchJSON || (async () => {
    throw new Error("0: dashboard SDK unavailable");
  });
  var cn = SDK.utils && SDK.utils.cn || ((...a) => a.filter(Boolean).join(" "));
  function register(name, component) {
    if (PLUGINS && typeof PLUGINS.register === "function") {
      PLUGINS.register(name, component);
    } else {
      console.error("[hermes_otel] dashboard plugin registry unavailable");
    }
  }
  var API = "/api/plugins/hermes_otel";
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
  function fmtAbsTime(unixNano) {
    if (!unixNano) return "";
    try {
      return new Date(unixNano / 1e6).toLocaleString();
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
    return num.toLocaleString();
  }
  function fmtCost(usd) {
    if (!usd) return "$0";
    if (usd < 0.01) return `$${usd.toFixed(4)}`;
    return `$${usd.toFixed(2)}`;
  }
  function fmtInt(n) {
    return n == null ? "0" : n.toLocaleString();
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
  var KIND_HEX = {
    agent: "#34d399",
    llm: "#38bdf8",
    api: "#22d3ee",
    tool: "#fbbf24",
    skill: "#6ee7b7",
    approval: "#f472b6",
    subagent: "#a78bfa",
    session: "#34d399",
    cron: "#a78bfa",
    other: "#94a3b8"
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
    return out;
  }
  function traceSpanCount(trace) {
    if (typeof trace.spanCount === "number" && trace.spanCount > 0) return trace.spanCount;
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
  var liveTokens = (s) => attrNum(s.attributes, "gen_ai.usage.total_tokens", "llm.token_count.total");
  var liveCost = (s) => attrNum(s.attributes, "hermes.cost.usage");
  var liveModel = (s) => s.attributes["gen_ai.request.model"] || s.attributes["llm.model_name"] || s.attributes["gen_ai.response.model"] || null;
  var sessionOf = (s) => s.attributes["hermes.session_id"] || s.attributes["session_id"] || s.attributes["session.id"] || null;
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
  function traceTotals(spans) {
    const ids = new Set(spans.map((s) => s.span_id));
    const root = spans.find((s) => !s.parent_span_id || !ids.has(s.parent_span_id)) || null;
    const pick = (get) => {
      if (root) {
        const v = get(root);
        if (v != null) return v;
      }
      let sum = 0;
      let seen = false;
      for (const s of spans) {
        if (!s.name.startsWith("api.")) continue;
        const v = get(s);
        if (v != null) {
          sum += v;
          seen = true;
        }
      }
      return seen ? sum : null;
    };
    const noCostTotal = root != null && root.attributes["hermes.cost.status"] != null && liveCost(root) == null;
    return { tokens: pick(liveTokens), cost: noCostTotal ? null : pick(liveCost) };
  }
  function groupLiveTraces(spans) {
    var _a;
    const byTrace = {};
    for (const s of spans) (byTrace[_a = s.trace_id] || (byTrace[_a] = [])).push(s);
    const out = [];
    for (const tid in byTrace) {
      const ss = byTrace[tid];
      const ids = new Set(ss.map((s) => s.span_id));
      const root = ss.find((s) => !s.parent_span_id || !ids.has(s.parent_span_id)) || ss[0];
      const startNs = Math.min(...ss.map((s) => s.start_time_unix_nano || 0));
      const endNs = Math.max(...ss.map((s) => s.end_time_unix_nano || s.start_time_unix_nano || 0));
      const totals = traceTotals(ss);
      let model = null;
      let error = false;
      for (const s of ss) {
        if (!model) model = liveModel(s);
        if (s.status === "ERROR") error = true;
      }
      out.push({
        traceId: tid,
        root,
        rootName: root.name,
        rootKind: kindOf(root.name, root.attributes),
        service: root.attributes["service.name"] || "hermes",
        startNs,
        endNs,
        durationMs: (endNs - startNs) / 1e6,
        spanCount: ss.length,
        model,
        tokens: totals.tokens,
        cost: totals.cost,
        error,
        session: sessionOf(root),
        spans: ss
      });
    }
    return out.sort((a, b) => b.startNs - a.startNs);
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
  function headerFacts(root, spansAttrs = []) {
    const a = root || {};
    const num = (...keys) => attrNum(a, ...keys);
    let totalTokens = num("gen_ai.usage.total_tokens", "llm.token_count.total");
    let inputTokens = num("gen_ai.usage.input_tokens", "llm.token_count.prompt");
    let outputTokens = num("gen_ai.usage.output_tokens", "llm.token_count.completion");
    if (totalTokens == null) {
      let t = 0;
      let i = 0;
      let o = 0;
      let seen = false;
      for (const s of spansAttrs) {
        const v = attrNum(s, "gen_ai.usage.total_tokens", "llm.token_count.total");
        if (v != null) {
          t += v;
          i += attrNum(s, "gen_ai.usage.input_tokens", "llm.token_count.prompt") || 0;
          o += attrNum(s, "gen_ai.usage.output_tokens", "llm.token_count.completion") || 0;
          seen = true;
        }
      }
      if (seen) {
        totalTokens = t;
        inputTokens = inputTokens != null ? inputTokens : i;
        outputTokens = outputTokens != null ? outputTokens : o;
      }
    }
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
      cost: num("hermes.cost.usage"),
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

  // src/atoms.tsx
  function MiniLabel(props) {
    return /* @__PURE__ */ React.createElement("span", { className: "text-[11px] font-medium uppercase tracking-wide text-muted-foreground" }, props.children);
  }
  function ErrorBanner({ error }) {
    return /* @__PURE__ */ React.createElement("div", { className: "border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive" }, error);
  }
  function Stat({ label, value, sub, accent }) {
    const valColor = accent === "cost" ? "text-emerald-400" : accent === "error" ? "text-destructive" : "text-foreground";
    return /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg border border-border px-3 py-2.5" }, /* @__PURE__ */ React.createElement("div", { className: cn("text-xl font-semibold tabular-nums tracking-tight", valColor) }, value), /* @__PURE__ */ React.createElement("div", { className: "text-[11px] uppercase tracking-wide text-muted-foreground" }, label), sub != null ? /* @__PURE__ */ React.createElement("div", { className: "mt-0.5 text-[11px] text-muted-foreground" }, sub) : null);
  }
  function Pulse({ active }) {
    return /* @__PURE__ */ React.createElement("span", { className: cn("inline-block h-2.5 w-2.5 rounded-full", active ? "otel-pulse-dot otel-pulse" : "bg-muted-foreground/40") });
  }
  function Sparkline({ values, height = 30, color }) {
    const max = Math.max(1, ...values);
    const w = values.length || 1;
    const bw = 100 / w;
    const fill = color || "var(--color-primary, #34d399)";
    return /* @__PURE__ */ React.createElement("svg", { viewBox: `0 0 100 ${height}`, preserveAspectRatio: "none", style: { width: "100%", height } }, values.map((v, i) => {
      const h = v / max * (height - 2);
      return /* @__PURE__ */ React.createElement("rect", { key: i, x: i * bw + 0.25, y: height - h, width: Math.max(0.5, bw - 0.5), height: h || 0.5, fill, opacity: 0.3 + 0.7 * (i / w) });
    }));
  }
  function LineChart({
    series,
    height = 120,
    labels,
    fmt
  }) {
    const n = Math.max(1, ...series.map((s) => s.points.length));
    const rawMax = Math.max(...series.flatMap((s) => s.points), 0);
    const max = rawMax > 0 ? rawMax : 1;
    const fmtY = (v) => fmt ? fmt(v) : v >= 1e3 ? `${(v / 1e3).toFixed(v >= 1e4 ? 0 : 1)}k` : v.toFixed(v < 10 && v !== Math.round(v) ? 2 : 0);
    const W = 100;
    const path = (pts) => pts.map((v, i) => `${i === 0 ? "M" : "L"} ${i / Math.max(1, n - 1) * W} ${height - v / max * (height - 6) - 3}`).join(" ");
    return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("svg", { viewBox: `0 0 ${W} ${height}`, preserveAspectRatio: "none", style: { width: "100%", height } }, [0.25, 0.5, 0.75].map((g) => /* @__PURE__ */ React.createElement("line", { key: g, x1: 0, x2: W, y1: height * g, y2: height * g, stroke: "var(--color-border)", strokeWidth: 0.3 })), series.map((s) => /* @__PURE__ */ React.createElement("path", { key: s.label, d: path(s.points), fill: "none", stroke: s.color, strokeWidth: 1, vectorEffect: "non-scaling-stroke" }))), /* @__PURE__ */ React.createElement("div", { className: "mt-1 flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement("span", { className: "text-[10px] tabular-nums text-muted-foreground/70", title: "y-axis maximum" }, "max ", fmtY(rawMax)), labels && labels.length ? /* @__PURE__ */ React.createElement("span", { className: "text-[10px] tabular-nums text-muted-foreground/70" }, labels.join(" \xB7 ")) : null, series.map((s) => /* @__PURE__ */ React.createElement("span", { key: s.label, className: "inline-flex items-center gap-1.5 text-[11px] text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", { className: "otel-w-2 inline-block h-2 rounded-full", style: { background: s.color } }), s.label))));
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
  var IconCoins = (p) => svg(p.size, p.className, [
    /* @__PURE__ */ React.createElement("circle", { key: "a", cx: 8, cy: 8, r: 6 }),
    /* @__PURE__ */ React.createElement("path", { key: "b", d: "M18.09 10.37A6 6 0 1 1 10.34 18" }),
    /* @__PURE__ */ React.createElement("path", { key: "c", d: "M7 6h1v4" }),
    /* @__PURE__ */ React.createElement("path", { key: "d", d: "m16.71 13.88.7.71-2.82 2.82" })
  ]);
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
  function categorize(rootName) {
    const n = (rootName || "").toLowerCase();
    if (n.startsWith("api.") || n.startsWith("llm.")) return CATEGORY.llm;
    if (n.startsWith("skill.")) return CATEGORY.skill;
    if (n.startsWith("approval")) return CATEGORY.approval;
    if (n.startsWith("subagent")) return CATEGORY.subagent;
    if (n.startsWith("tool.")) return CATEGORY.tool;
    if (n === "agent" || n.startsWith("agent.")) return CATEGORY.agent;
    if (n === "cron" || n.startsWith("cron")) return CATEGORY.cron;
    return CATEGORY.other;
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
  var currentMode = readViewMode();
  var modeListeners = /* @__PURE__ */ new Set();
  function setGlobalMode(m) {
    currentMode = m;
    writeViewMode(m);
    modeListeners.forEach((fn) => fn(m));
  }
  function useViewMode() {
    const [mode, setMode] = useState(currentMode);
    useEffect(() => {
      modeListeners.add(setMode);
      return () => {
        modeListeners.delete(setMode);
      };
    }, []);
    return [mode, setGlobalMode];
  }
  function LongText({ text, mono, markdown }) {
    const [open, setOpen] = useState(false);
    const long = text.length > CLAMP_CHARS;
    const shown = long && !open ? text.slice(0, CLAMP_CHARS) : text;
    return /* @__PURE__ */ React.createElement("div", { className: "otel-longtext" }, markdown ? /* @__PURE__ */ React.createElement(Markdown, { text: shown }) : /* @__PURE__ */ React.createElement("pre", { className: cn("otel-pre", mono ? "" : "otel-prose") }, shown), long ? /* @__PURE__ */ React.createElement("button", { type: "button", className: "otel-link otel-more", onClick: () => setOpen((o) => !o) }, open ? "show less" : `show all (${fmtCount(text.length)} chars)`) : null);
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
        onClick: () => onChange(id),
        className: cn("otel-toggle otel-mode-btn", mode === id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground")
      },
      label
    );
    return /* @__PURE__ */ React.createElement("span", { className: "otel-mode" }, /* @__PURE__ */ React.createElement(Btn, { id: "structured", label: "structured" }), /* @__PURE__ */ React.createElement(Btn, { id: "raw", label: "raw" }));
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
  var KINDS = ["agent", "cron", "subagent", "tool", "llm", "api", "approval", "skill"];
  var KIND_REGEX = {
    agent: "^agent",
    cron: "^cron",
    subagent: "^subagent",
    tool: "^tool\\.",
    llm: "^llm\\.",
    api: "^api\\.",
    approval: "^approval",
    skill: "^skill\\."
  };
  function liveParams(f, limit = 100) {
    const p = new URLSearchParams({ lookback_hours: String(f.lookback), limit: String(limit) });
    if (f.status) p.set("status", f.status);
    if (f.kind) p.set("kind", f.kind);
    if (f.tool.trim()) p.set("tool", f.tool.trim());
    if (f.model.trim()) p.set("model", f.model.trim());
    if (f.session.trim()) p.set("session", f.session.trim());
    if (Number(f.minDurationMs) > 0) p.set("min_duration_ms", String(Math.floor(Number(f.minDurationMs))));
    if (f.text.trim()) p.set("text", f.text.trim());
    if (f.traceId.trim()) p.set("trace_id", f.traceId.trim());
    return p;
  }
  function backendParams(f, source, limit = 50) {
    const p = withBackend(new URLSearchParams({ lookback_hours: String(f.lookback), limit: String(limit) }), source);
    const rootsOnly = f.rootsOnly && !f.kind && !f.tool.trim();
    p.set("roots_only", String(rootsOnly));
    if (f.status) p.set("status", f.status);
    if (f.kind && KIND_REGEX[f.kind]) p.set("name_regex", KIND_REGEX[f.kind]);
    if (f.tool.trim()) p.set("tool", f.tool.trim());
    if (f.model.trim()) p.set("model", f.model.trim());
    if (f.session.trim()) p.set("session", f.session.trim());
    if (Number(f.minDurationMs) > 0) p.set("min_duration_ms", String(Math.floor(Number(f.minDurationMs))));
    if (f.text.trim()) p.set("free_text", f.text.trim());
    if (f.q.trim()) p.set("q", f.q.trim());
    if (f.service.trim()) p.set("service", f.service.trim());
    return p;
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

  // src/nav.ts
  var NAV_KEYS = [
    "tab",
    "source",
    "view",
    "trace",
    "session",
    "level",
    "logger",
    "text",
    "lookback",
    "events",
    "event",
    "center",
    "win",
    "size",
    "before"
  ];
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
    writeNav(state);
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(NAV_EVENT, { detail: state }));
  }

  // src/source.ts
  var KEY = "hermes_otel.source";
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
  function useSource() {
    const [source, setSourceState] = useState(readSource());
    const [status, setStatus] = useState(null);
    const refresh = useCallback(() => {
      const p = withBackend(new URLSearchParams(), source);
      fetchJSON(`${API}/status?${p}`).then((st) => {
        setStatus(st);
        if (source !== LIVE && !(st.available || []).some((b) => b.name === source)) {
          setSourceState(LIVE);
          writeSource(LIVE);
        }
      }).catch(() => setStatus({ configured: false, active: null, available: [], reason: "status unavailable" }));
    }, [source]);
    useEffect(() => {
      refresh();
    }, [refresh]);
    const setSource = useCallback((s) => {
      writeSource(s);
      setSourceState(s);
    }, []);
    return { source, setSource, status, refresh, isLive: source === LIVE };
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
  function backendUsable(b, need) {
    if (!b.supported) return false;
    if (need === "metrics") return b.metrics;
    if (need === "logs") return b.logs;
    return true;
  }
  function SourceSelect({
    source,
    onChange,
    status,
    need,
    className
  }) {
    const backends = (status == null ? void 0 : status.available) || [];
    return /* @__PURE__ */ React.createElement("div", { className: cn("inline-flex items-center gap-2", className) }, /* @__PURE__ */ React.createElement("span", { className: "text-[11px] font-medium uppercase tracking-wide text-muted-foreground" }, "source"), /* @__PURE__ */ React.createElement(Select, { value: source, onValueChange: onChange, className: "otel-w-56 h-8" }, /* @__PURE__ */ React.createElement(SelectOption, { value: LIVE }, "\u26A1 Live (in-process)"), backends.map((b) => {
      const ok = backendUsable(b, need);
      const why = !b.supported ? "no dashboard adapter for this type" : need === "metrics" && !b.metrics ? "this backend does not serve metrics to the tab" : need === "logs" && !b.logs ? "this backend does not serve logs to the tab" : "";
      return /* @__PURE__ */ React.createElement(SelectOption, { key: b.name, value: b.name, disabled: !ok, title: why }, "\u{1F5C4} ", b.name, b.type !== b.name ? ` (${b.type})` : "", ok ? "" : " \xB7 unavailable");
    })));
  }

  // src/logs.tsx
  var POLL_MS = 3e3;
  var LEVEL_CLASS = {
    ERROR: "text-destructive",
    CRITICAL: "text-destructive",
    FATAL: "text-destructive",
    WARNING: "otel-c-tool",
    WARN: "otel-c-tool",
    INFO: "otel-c-llm",
    DEBUG: "text-muted-foreground"
  };
  function LogRow({
    l,
    absolute,
    wrap = true,
    expanded,
    onToggle,
    actions
  }) {
    const lvl = (l.level || "INFO").toUpperCase();
    const ts = l.time_unix_nano || 0;
    const attrs = l.attributes || {};
    const hint = attributionHint(attrs);
    const isError = severityOf(lvl) === "ERROR";
    const edge = isError ? "border-l-2 border-l-primary" : "";
    return /* @__PURE__ */ React.createElement("div", { className: cn("border-b border-border/60 last:border-b-0", expanded ? "bg-muted/30" : "otel-hoverable", edge) }, /* @__PURE__ */ React.createElement(
      "div",
      {
        className: "flex cursor-pointer items-start gap-2 px-3 py-1",
        onClick: onToggle,
        role: "button",
        tabIndex: 0,
        title: expanded ? "collapse" : "expand attributes"
      },
      /* @__PURE__ */ React.createElement("span", { className: cn("shrink-0 text-muted-foreground/70", absolute ? "otel-w-40" : "otel-w-14"), title: ts ? fmtAbsTime(ts) : "" }, ts ? absolute ? fmtAbsTime(ts) : fmtTimeAgo(ts) : ""),
      /* @__PURE__ */ React.createElement("span", { className: cn("otel-w-12 shrink-0 font-semibold", LEVEL_CLASS[lvl] || "text-muted-foreground") }, lvl),
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
    } }, "\xB130 s around this line") : null, /* @__PURE__ */ React.createElement(CopyButton, { text: JSON.stringify(l, null, 2), label: "copy JSON" }))), l.body ? /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-raw whitespace-pre-wrap break-words" }, l.body) : null, groups.length ? /* @__PURE__ */ React.createElement("div", { className: "grid gap-x-4 gap-y-1 sm:grid-cols-2" }, groups.map((g) => /* @__PURE__ */ React.createElement("div", { key: g.label, className: "min-w-0" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-muted-foreground" }, g.label), /* @__PURE__ */ React.createElement("table", { className: "otel-kv-table w-full" }, /* @__PURE__ */ React.createElement("tbody", null, g.entries.map(([k, v]) => /* @__PURE__ */ React.createElement("tr", { key: k }, /* @__PURE__ */ React.createElement("td", { className: "otel-kv text-muted-foreground" }, k), /* @__PURE__ */ React.createElement("td", { className: "break-all text-foreground/90" }, typeof v === "string" ? v : JSON.stringify(v))))))))) : /* @__PURE__ */ React.createElement("div", { className: "text-muted-foreground" }, "No attributes on this record."), stacktrace ? /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-raw max-h-80 overflow-auto whitespace-pre-wrap break-words" }, stacktrace) : null);
  }
  function SeveritySummary({ rows, buckets }) {
    const c = severityCounts(rows);
    const max = Math.max(1, ...buckets.map((b) => b.total));
    const w = 160;
    const h = 24;
    const bw = buckets.length ? w / buckets.length : w;
    return /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, c.total, " shown", c.events ? ` \xB7 ${c.events} event${c.events === 1 ? "" : "s"}` : ""), /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, /* @__PURE__ */ React.createElement("span", { className: c.bySeverity.ERROR ? "text-destructive" : "" }, c.bySeverity.ERROR, " error"), " \xB7 ", /* @__PURE__ */ React.createElement("span", { className: c.bySeverity.WARN ? "otel-c-tool" : "" }, c.bySeverity.WARN, " warn"), " \xB7 ", c.bySeverity.INFO, " info", c.bySeverity.DEBUG ? ` \xB7 ${c.bySeverity.DEBUG} debug` : ""), buckets.length > 1 ? /* @__PURE__ */ React.createElement("svg", { width: w, height: h, role: "img", "aria-label": "lines per time bucket, oldest left", className: "shrink-0" }, /* @__PURE__ */ React.createElement("rect", { x: "0", y: h - 1, width: w, height: "1", className: "text-muted-foreground", fill: "currentColor", opacity: "0.3" }), buckets.map((b, i) => {
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
          className: b.errors ? "text-destructive" : "otel-c-tool",
          fill: "currentColor"
        }
      ) : null);
    })) : null);
  }
  function LogsPage() {
    const { source, setSource, status, isLive } = useSource();
    const initialNav = readNav();
    const [filters, setFilters] = useState(() => logFiltersFromNav(initialNav));
    const [applied, setApplied] = useState(() => logFiltersFromNav(initialNav));
    const [pageSize, setPageSize] = useState(() => logPageSizeFromNav(initialNav.size));
    const [cursors, setCursors] = useState(() => initialNav.before && /^\d+$/.test(initialNav.before) ? [initialNav.before] : []);
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
    const [error, setError] = useState(null);
    const [live, setLive] = useState(null);
    const inflight = useRef(false);
    const before = cursors.length ? cursors[cursors.length - 1] : null;
    const onNewestPage = cursors.length === 0;
    const base = isLive ? `${API}/live` : API;
    const canQuery = isLive || !!(status == null ? void 0 : status.logs);
    useEffect(() => {
      writeNav({ ...navFromLogFilters(applied), size: pageSize !== 200 ? String(pageSize) : "", before: before || "" });
    }, [applied, pageSize, before]);
    const load = useCallback(async () => {
      if (!canQuery || inflight.current) return;
      inflight.current = true;
      try {
        if (isLive) {
          const st = await fetchJSON(`${API}/live/status`);
          setLive(st && st.live !== false);
          if (!st || st.live === false) return;
        }
        const r = await fetchJSON(`${base}/logs/search?${logParams(applied, source, pageSize, before)}`);
        setLogs(dedupe(r.logs || []));
        setNextBefore(r.next_before_ns != null ? String(r.next_before_ns) : null);
        setHasMore(!!r.has_more);
        setError(null);
      } catch (e) {
        setError(String((e == null ? void 0 : e.message) || e));
      } finally {
        inflight.current = false;
      }
    }, [applied, base, before, canQuery, isLive, pageSize, source]);
    useEffect(() => {
      load();
    }, [load]);
    usePolling(load, POLL_MS, !paused && canQuery && onNewestPage);
    useEffect(() => {
      if (!canQuery) return;
      const p = withBackend(new URLSearchParams(), source);
      fetchJSON(`${base}/loggers?${p}`).then((r) => setLoggers(r.loggers || [])).catch(() => setLoggers([]));
    }, [base, canQuery, source]);
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
      var _a;
      return String((_a = l.seq) != null ? _a : `${l.time_unix_nano || 0}:${i}`);
    };
    const ordered = useMemo(() => follow ? [...logs].reverse() : logs, [logs, follow]);
    const buckets = useMemo(() => timeBuckets(logs, 24), [logs]);
    useEffect(() => {
      var _a, _b;
      if (follow && onNewestPage && !paused) (_b = (_a = listEnd.current) == null ? void 0 : _a.scrollIntoView) == null ? void 0 : _b.call(_a, { block: "nearest" });
    }, [logs, follow, onNewestPage, paused]);
    const permalink = typeof window !== "undefined" ? window.location.href : "";
    const oldestShown = logs.length ? logs[logs.length - 1].time_unix_nano || 0 : 0;
    const newestShown = logs.length ? logs[0].time_unix_nano || 0 : 0;
    const header = /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement(SourceSelect, { source, onChange: setSource, status, need: "logs" }), /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, logs.length, " line", logs.length === 1 ? "" : "s", cursors.length ? ` \xB7 page ${cursors.length + 1}` : "", onNewestPage ? paused ? " \xB7 paused" : " \xB7 following" : " \xB7 older page, not following")), /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement("label", { className: "inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: absolute, onChange: (e) => setAbsolute(e.target.checked) }), "absolute times"), /* @__PURE__ */ React.createElement(
      "label",
      {
        className: "inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground",
        title: "oldest first, newest at the bottom, scrolls with new lines"
      },
      /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: follow, onChange: (e) => setFollow(e.target.checked) }),
      "follow"
    ), /* @__PURE__ */ React.createElement("label", { className: "inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground", title: "wrap long lines" }, /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: wrap, onChange: (e) => setWrap(e.target.checked) }), "wrap"), /* @__PURE__ */ React.createElement(CopyButton, { text: permalink, label: "copy link" }), /* @__PURE__ */ React.createElement(
      Select,
      {
        value: String(pageSize),
        onValueChange: (v) => {
          setPageSize(Number(v));
          setCursors([]);
        },
        className: "h-8"
      },
      LOG_PAGE_SIZES.map((n) => /* @__PURE__ */ React.createElement(SelectOption, { key: n, value: String(n) }, n, " / page"))
    ), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: () => setPaused((p) => !p), disabled: !onNewestPage }, paused ? "\u25B6 Resume" : "\u23F8 Pause")));
    if (!canQuery)
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, header, /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, "This source does not serve logs"), "Pick the Live source, or a backend whose adapter serves logs (OpenObserve, SigNoz, Uptrace, LGTM)."));
    const pager = /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", null, logs.length ? `${fmtAbsTime(oldestShown)} \u2192 ${fmtAbsTime(newestShown)}` : ""), /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: newest, disabled: onNewestPage }, "\u23EE Newest"), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: newer, disabled: onNewestPage }, "\u2190 Newer"), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: older, disabled: !hasMore || !nextBefore }, "Older \u2192")));
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, header, /* @__PURE__ */ React.createElement(
      "form",
      {
        className: "otel-search-grid",
        onSubmit: (e) => {
          e.preventDefault();
          apply(filters);
        }
      },
      /* @__PURE__ */ React.createElement(Select, { value: filters.minLevel, onValueChange: (v) => set("minLevel", v), className: "h-8" }, /* @__PURE__ */ React.createElement(SelectOption, { value: "0" }, "All levels"), /* @__PURE__ */ React.createElement(SelectOption, { value: "20" }, "Info+"), /* @__PURE__ */ React.createElement(SelectOption, { value: "30" }, "Warn+"), /* @__PURE__ */ React.createElement(SelectOption, { value: "40" }, "Error")),
      /* @__PURE__ */ React.createElement(Select, { value: filters.logger, onValueChange: (v) => set("logger", v), className: "h-8" }, /* @__PURE__ */ React.createElement(SelectOption, { value: "" }, "Any logger"), filters.logger && !loggers.some((l) => l.logger === filters.logger) ? /* @__PURE__ */ React.createElement(SelectOption, { value: filters.logger }, filters.logger) : null, loggers.map((l) => /* @__PURE__ */ React.createElement(SelectOption, { key: l.logger, value: l.logger }, l.logger, " (", l.count, ")"))),
      /* @__PURE__ */ React.createElement(Input, { className: "h-8", placeholder: "session id", value: filters.session, onChange: (e) => set("session", e.target.value) }),
      /* @__PURE__ */ React.createElement(Input, { className: "h-8", placeholder: "trace id", value: filters.traceId, onChange: (e) => set("traceId", e.target.value) }),
      /* @__PURE__ */ React.createElement(Input, { className: "h-8", placeholder: "text\u2026", value: filters.text, onChange: (e) => set("text", e.target.value) }),
      /* @__PURE__ */ React.createElement(
        "label",
        {
          className: "inline-flex h-8 cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground",
          title: "only hermes.* / GenAI events (logs.events.enabled)"
        },
        /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: filters.eventsOnly || !!filters.eventName, onChange: (e) => set("eventsOnly", e.target.checked) }),
        "events only"
      ),
      /* @__PURE__ */ React.createElement(
        Input,
        {
          className: "h-8 font-mono",
          placeholder: "event name\u2026",
          value: filters.eventName,
          onChange: (e) => set("eventName", e.target.value),
          title: "one structured event, e.g. hermes.tool.call"
        }
      ),
      /* @__PURE__ */ React.createElement(Select, { value: String(filters.lookback), onValueChange: (v) => set("lookback", Number(v)), className: "h-8" }, /* @__PURE__ */ React.createElement(SelectOption, { value: "0.25" }, "15m"), /* @__PURE__ */ React.createElement(SelectOption, { value: "1" }, "1h"), /* @__PURE__ */ React.createElement(SelectOption, { value: "6" }, "6h"), /* @__PURE__ */ React.createElement(SelectOption, { value: "24" }, "24h"), /* @__PURE__ */ React.createElement(SelectOption, { value: "168" }, "7d"), /* @__PURE__ */ React.createElement(SelectOption, { value: "720" }, "30d")),
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
    ), error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error }) : null, isLive && live === false ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, "Live mode is off"), "Set ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "dashboard_live: true"), " and ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "logs.capture: true"), " (or", " ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "logs.events.enabled: true"), "), then run a turn.") : logs.length === 0 ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, onNewestPage ? "No log lines" : "No older lines"), !onNewestPage ? /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: newer }, "\u2190 Back to the newer page") : isLive ? /* @__PURE__ */ React.createElement(React.Fragment, null, "Set ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "logs.capture: true"), " or ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "logs.events.enabled: true"), " in the plugin config and run a turn \u2014 the agent's log lines and events stream here. Lines written while a turn is in flight carry its trace and session id; click a line for its attributes.") : "Nothing matched in this window.") : /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement(SeveritySummary, { rows: logs, buckets }), applied.centerNs ? /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, "\xB1", applied.windowS, " s around ", fmtAbsTime(Number(applied.centerNs)), " ", /* @__PURE__ */ React.createElement(
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
    )) : null), pager, /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg overflow-hidden border border-border font-mono text-xs" }, ordered.map((l, i) => {
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
    }), /* @__PURE__ */ React.createElement("div", { ref: listEnd })), pager));
  }
  function dedupe(rows) {
    const seen = /* @__PURE__ */ new Set();
    const out = [];
    for (const r of rows) {
      const k = `${r.time_unix_nano || 0}|${r.logger || ""}|${r.body || ""}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(r);
    }
    return out;
  }

  // src/detail.tsx
  function CopyButton({ text, label }) {
    const [done, setDone] = useState(false);
    return /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        className: "otel-link text-[10px] text-muted-foreground",
        title: `copy ${text}`,
        onClick: (e) => {
          var _a;
          e.stopPropagation();
          try {
            (_a = navigator.clipboard) == null ? void 0 : _a.writeText(text);
            setDone(true);
            setTimeout(() => setDone(false), 1200);
          } catch {
          }
        }
      },
      done ? "copied" : label || "copy"
    );
  }
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
    spansAttrs,
    error,
    uiUrl,
    uiLabel,
    source,
    onBack
  }) {
    var _a, _b;
    const f = headerFacts(rootAttrs, spansAttrs || []);
    const tokens = f.totalTokens != null ? `${fmtTokens(f.totalTokens)}${f.inputTokens != null || f.outputTokens != null ? ` (in ${fmtTokens((_a = f.inputTokens) != null ? _a : 0)} \xB7 out ${fmtTokens((_b = f.outputTokens) != null ? _b : 0)}${f.reasoningTokens ? ` \xB7 reasoning ${fmtTokens(f.reasoningTokens)}` : ""}${f.cacheReadTokens ? ` \xB7 cache read ${fmtTokens(f.cacheReadTokens)}` : ""})` : ""}` : null;
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-start justify-between gap-3" }, /* @__PURE__ */ React.createElement("div", { className: "min-w-0 space-y-1" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: "text-lg font-semibold uppercase tracking-tight" }, title), error ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "text-[10px]" }, "error") : null, f.platform ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]" }, f.platform) : null, f.turn != null ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]" }, "turn ", f.turn) : null), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2 text-xs text-muted-foreground" }, service ? /* @__PURE__ */ React.createElement("span", null, service) : null, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, traceId), /* @__PURE__ */ React.createElement(CopyButton, { text: traceId, label: "copy id" }), uiUrl ? /* @__PURE__ */ React.createElement("a", { className: "otel-link", href: uiUrl, target: "_blank", rel: "noreferrer" }, "open in ", uiLabel || "backend", " \u2197") : null)), /* @__PURE__ */ React.createElement(Button, { variant: "ghost", size: "sm", onClick: onBack }, "\u2190 Back")), /* @__PURE__ */ React.createElement("div", { className: "otel-facts-grid" }, /* @__PURE__ */ React.createElement(Fact, { label: "duration" }, fmtDurationMs(durationMs)), /* @__PURE__ */ React.createElement(Fact, { label: "model" }, f.requestModel ? /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, f.requestModel) : null, f.responseModel ? /* @__PURE__ */ React.createElement("span", { className: "ml-1 text-xs text-muted-foreground", title: "the model named in the response, when it differs from the request" }, "(served: ", f.responseModel, ")") : null), /* @__PURE__ */ React.createElement(Fact, { label: "tokens" }, tokens), /* @__PURE__ */ React.createElement(Fact, { label: "cost" }, f.cost != null ? /* @__PURE__ */ React.createElement("span", { className: "text-emerald-400" }, "$", f.cost.toFixed(4)) : /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, "no pricing data")), /* @__PURE__ */ React.createElement(Fact, { label: "tools" }, f.tools.length ? f.tools.join(", ") : null), /* @__PURE__ */ React.createElement(Fact, { label: "outcome" }, f.finalStatus || f.exitReason ? `${f.finalStatus || ""}${f.finalStatus && f.exitReason ? " \xB7 " : ""}${f.exitReason || ""}` : null), /* @__PURE__ */ React.createElement(Fact, { label: "session" }, f.session ? /* @__PURE__ */ React.createElement(
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
    var _a, _b, _c, _d;
    const a = span._attrs || {};
    const kind = kindOf(span.name, a);
    const err = (_a = a["error.message"]) != null ? _a : a["exception.message"];
    const errorBlock = err ? /* @__PURE__ */ React.createElement(ValueView, { attrKey: "error.message", value: err, label: a["error.type"] ? `error \xB7 ${a["error.type"]}` : "error" }) : null;
    if (kind === "tool") {
      const truncated = String(a["hermes.preview.output.truncated"]) === "true";
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
          label: truncated ? `result \xB7 preview of ${fmtTokens(a["hermes.preview.output.original_chars"]) || "?"} chars` : "result",
          keys: ["output.value", "gen_ai.tool.call.result"]
        }
      ));
    }
    if (kind === "llm" || kind === "api") {
      const f = headerFacts(a);
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
            { label: "finish", value: first(a, "llm.response.finish_reason", "gen_ai.response.finish_reasons") },
            { label: "latency", value: a["llm.response.duration_ms"] != null ? fmtDurationMs(Number(a["llm.response.duration_ms"])) : null },
            { label: "messages", value: a["llm.request.message_count"] },
            { label: "mode", value: a["llm.api_mode"] },
            { label: "tool calls", value: a["llm.response.tool_calls"] },
            {
              label: "http",
              value: first(a, "http.response.status_code", "gen_ai.response.status_code"),
              tone: Number(first(a, "http.response.status_code", "gen_ai.response.status_code")) >= 400 ? "bad" : void 0
            },
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
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2 text-xs" }, a["hermes.approval.choice"] ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]" }, "\u{1F464} ", String(a["hermes.approval.choice"])) : null, a["hermes.approval.granted"] != null ? /* @__PURE__ */ React.createElement(Badge, { variant: String(a["hermes.approval.granted"]) === "true" ? "secondary" : "destructive", className: "text-[10px]" }, String(a["hermes.approval.granted"]) === "true" ? "granted" : "denied") : null, String(a["hermes.approval.timed_out"]) === "true" ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "text-[10px]" }, "timed out") : null), /* @__PURE__ */ React.createElement(
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
    return errorBlock ? /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, errorBlock) : null;
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
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, groups.map((g) => /* @__PURE__ */ React.createElement("div", { key: g.prefix }, /* @__PURE__ */ React.createElement(MiniLabel, null, g.prefix), /* @__PURE__ */ React.createElement("dl", { className: "otel-attr-table text-xs" }, g.entries.map((e) => /* @__PURE__ */ React.createElement(React.Fragment, { key: e.key }, /* @__PURE__ */ React.createElement("dt", { className: "text-muted-foreground", title: e.aliases.length ? `also: ${e.aliases.join(", ")}` : "" }, e.key, e.aliases.length ? /* @__PURE__ */ React.createElement("span", { className: "ml-1 text-[10px] text-muted-foreground/60" }, "+", e.aliases.length) : null), /* @__PURE__ */ React.createElement("dd", { className: "min-w-0 break-words text-foreground" }, CONTENT_KEYS.has(e.key) ? /* @__PURE__ */ React.createElement("details", { className: "otel-details" }, /* @__PURE__ */ React.createElement("summary", { className: "cursor-pointer text-[11px] text-muted-foreground" }, String(e.value).length.toLocaleString("en-US"), " chars"), /* @__PURE__ */ React.createElement("div", { className: "mt-1" }, /* @__PURE__ */ React.createElement(ValueView, { attrKey: e.key, value: e.value }))) : /* @__PURE__ */ React.createElement(ValueView, { attrKey: e.key, value: e.value, onSessionClick: onSession }))))))));
  }
  function TraceTabs({ traceId, source, logsAvailable, spans, raw }) {
    const [tab, setTab] = useState("spans");
    const [logs, setLogs] = useState(null);
    const [openLog, setOpenLog] = useState(null);
    const [error, setError] = useState(null);
    useEffect(() => {
      if (tab !== "logs" || logs !== null) return;
      const base = source === "live" ? `${API}/live` : API;
      const p = withBackend(new URLSearchParams({ trace_id: traceId, limit: "500", lookback_hours: "8760" }), source);
      fetchJSON(`${base}/logs/search?${p}`).then((r) => setLogs(r.logs || [])).catch((e) => {
        setError(String((e == null ? void 0 : e.message) || e));
        setLogs([]);
      });
    }, [tab, logs, source, traceId]);
    const Btn = ({ id, label }) => /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        onClick: () => setTab(id),
        className: cn(
          "otel-toggle px-3 py-1 text-xs font-medium transition-colors",
          tab === id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground"
        )
      },
      label
    );
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg inline-flex border border-border p-0.5" }, /* @__PURE__ */ React.createElement(Btn, { id: "spans", label: "Spans" }), logsAvailable ? /* @__PURE__ */ React.createElement(Btn, { id: "logs", label: "Logs" }) : null, /* @__PURE__ */ React.createElement(Btn, { id: "raw", label: "Raw" })), tab === "spans" ? spans : null, tab === "logs" ? error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error }) : logs === null ? /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "Loading\u2026") : logs.length === 0 ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground" }, "No log lines carry this trace id.") : /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", null, logs.length, " line", logs.length === 1 ? "" : "s", " carry this trace id \xB7 click a line for its attributes"), /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        className: "otel-link",
        title: "open the Logs tab filtered to this trace",
        onClick: () => navigate({ tab: "logs", source, trace: traceId, session: "", lookback: "8760" })
      },
      "open in Logs tab \u2192"
    )), /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg overflow-hidden border border-border font-mono text-xs" }, logs.map((l, i) => {
      var _a;
      const k = String((_a = l.seq) != null ? _a : i);
      return /* @__PURE__ */ React.createElement(
        LogRow,
        {
          key: k,
          l,
          absolute: true,
          expanded: openLog === k,
          onToggle: () => setOpenLog(openLog === k ? null : k),
          actions: { onTrace: () => void 0 }
        }
      );
    }))) : null, tab === "raw" ? /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-raw" }, JSON.stringify(raw, null, 2)) : null);
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
    hasKids
  }) {
    const kind = kindOf(span.name, span._attrs);
    const hex = KIND_HEX[kind];
    const isErr = statusCode(span.status) === "error";
    const cost = span._attrs["hermes.cost.usage"];
    const tokens = span._attrs["gen_ai.usage.total_tokens"] || span._attrs["llm.token_count.total"];
    const approval = span._attrs["hermes.approval.choice"];
    const width = Math.min(100 - offsetPct, Math.max(0.8, durPct));
    return /* @__PURE__ */ React.createElement("div", { className: cn("otel-card-bg otel-hoverable overflow-hidden border transition-colors", isErr ? "border-destructive/40" : "border-border") }, /* @__PURE__ */ React.createElement("div", { className: "otel-track relative h-1.5 w-full", title: `+${fmtDurationMs(startMs)} \xB7 ${fmtDurationMs(span.durationMs)}` }, /* @__PURE__ */ React.createElement("div", { className: "absolute inset-y-0", style: { left: `${offsetPct}%`, width: `${width}%`, minWidth: 2, background: hex } })), /* @__PURE__ */ React.createElement("div", { className: "flex cursor-pointer items-center gap-2 px-3 py-2", style: { paddingLeft: 12 + depth * 20 }, onClick: onToggle }, /* @__PURE__ */ React.createElement("span", { className: "w-3 shrink-0 text-xs text-muted-foreground" }, hasKids ? open ? "\u25BE" : "\u25B8" : ""), /* @__PURE__ */ React.createElement("span", { className: "otel-w-2 inline-block h-2 shrink-0 rounded-full", style: { background: hex } }), /* @__PURE__ */ React.createElement("span", { className: "truncate font-mono text-sm", title: span.name }, span.name), isErr ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "shrink-0 text-[10px]" }, "error") : null, approval ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "shrink-0 text-[10px]" }, "\u{1F464} ", approval) : null, /* @__PURE__ */ React.createElement("div", { className: "ml-auto flex shrink-0 items-center gap-3 text-[11px] text-muted-foreground" }, tokens ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtTokens(tokens), " tok") : null, cost ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums text-emerald-400" }, "$", Number(cost).toFixed(4)) : null, startMs > 0.5 ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums", title: "start offset from trace begin" }, "+", fmtDurationMs(startMs)) : null, /* @__PURE__ */ React.createElement("span", { className: "otel-w-14 text-right font-medium tabular-nums text-foreground" }, fmtDurationMs(span.durationMs)))), open ? /* @__PURE__ */ React.createElement("div", { className: "space-y-3 border-t border-border/60 bg-muted/20 px-3 py-3" }, /* @__PURE__ */ React.createElement(SpanSummary, { span }), /* @__PURE__ */ React.createElement("details", { className: "otel-details" }, /* @__PURE__ */ React.createElement("summary", { className: "cursor-pointer text-[11px] font-medium uppercase tracking-wide text-muted-foreground" }, "all attributes"), /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(AttrGroups, { attrs: span._attrs })))) : null);
  }
  function SpanTreeView({ roots, defaultOpen }) {
    const flat = useMemo(() => flatten(roots), [roots]);
    const [openIds, setOpenIds] = useState(() => defaultOpen ? Object.fromEntries(flat.map((n) => [n.span.spanId, true])) : {});
    if (!flat.length) return /* @__PURE__ */ React.createElement("div", { className: "py-6 text-center text-sm text-muted-foreground" }, "No spans.");
    const t0 = Math.min(...flat.map((n) => n.span.startNs));
    const total = Math.max(...flat.map((n) => n.span.endNs)) - t0 || 1;
    const toggle = (id) => setOpenIds((p) => ({ ...p, [id]: !p[id] }));
    const expandAll = () => setOpenIds(Object.fromEntries(flat.map((n) => [n.span.spanId, true])));
    const collapseAll = () => setOpenIds({});
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between" }, /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-muted-foreground" }, flat.length, " span", flat.length === 1 ? "" : "s", " \xB7 ", fmtDurationMs(total / 1e6), " total"), /* @__PURE__ */ React.createElement("div", { className: "flex gap-2" }, /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: expandAll }, "Expand all"), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: collapseAll }, "Collapse"))), /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-1.5" }, flat.map((n) => /* @__PURE__ */ React.createElement(
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
        hasKids: n.span.children.length > 0
      }
    ))));
  }
  function LiveTraceCard({ trace, onSelect }) {
    const Icon = kindIcon(trace.rootKind);
    return /* @__PURE__ */ React.createElement(
      "div",
      {
        className: cn(
          "otel-card-bg otel-hover-parent flex cursor-pointer items-start gap-3 border p-3 transition-colors hover:bg-secondary/30 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          trace.error ? "otel-error-bg border-destructive/30" : "border-border"
        ),
        role: "button",
        tabIndex: 0,
        onClick: () => onSelect(trace),
        onKeyDown: (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(trace);
          }
        },
        title: trace.traceId
      },
      /* @__PURE__ */ React.createElement("div", { className: "shrink-0 pt-0.5", style: { color: KIND_HEX[trace.rootKind] } }, /* @__PURE__ */ React.createElement(Icon, { size: 16 })),
      /* @__PURE__ */ React.createElement("div", { className: "min-w-0 flex-1 space-y-1" }, /* @__PURE__ */ React.createElement("div", { className: "flex min-w-0 items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: "truncate font-mono text-sm" }, trace.rootName), trace.error ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "shrink-0 text-[10px]" }, "error") : null), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground" }, trace.model ? /* @__PURE__ */ React.createElement("span", { className: "font-mono text-foreground/80" }, trace.model) : null, /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, trace.spanCount, " span", trace.spanCount === 1 ? "" : "s"), /* @__PURE__ */ React.createElement("span", { className: "text-border" }, "\xB7"), /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtDurationMs(trace.durationMs)), trace.tokens ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("span", { className: "text-border" }, "\xB7"), /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtTokens(trace.tokens), " tok")) : null, trace.cost ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums text-emerald-400" }, "$", trace.cost.toFixed(4)) : null, /* @__PURE__ */ React.createElement("span", { className: "text-border" }, "\xB7"), /* @__PURE__ */ React.createElement("span", { title: fmtAbsTime(trace.startNs) }, fmtTimeAgo(trace.endNs || trace.startNs)))),
      /* @__PURE__ */ React.createElement("div", { className: "otel-self-center otel-reveal shrink-0 text-muted-foreground transition-opacity" }, /* @__PURE__ */ React.createElement(IconChevronRight, { size: 16 }))
    );
  }
  function LiveTraceDetail({ trace, roots, onBack, source = "live" }) {
    const spans = trace.spans || [];
    const ids = new Set(spans.map((s) => s.span_id));
    const root = spans.find((s) => !s.parent_span_id || !ids.has(s.parent_span_id)) || spans[0];
    return /* @__PURE__ */ React.createElement(Card, null, /* @__PURE__ */ React.createElement(CardHeader, { className: "otel-space-y-0" }, /* @__PURE__ */ React.createElement(
      TraceHeader,
      {
        title: trace.rootName,
        traceId: String(trace.traceId),
        service: trace.service,
        durationMs: trace.durationMs,
        rootAttrs: (root == null ? void 0 : root.attributes) || {},
        spansAttrs: spans.map((s) => s.attributes || {}),
        error: trace.error,
        source,
        onBack
      }
    )), /* @__PURE__ */ React.createElement(CardContent, null, /* @__PURE__ */ React.createElement(TraceTabs, { traceId: String(trace.traceId), source, logsAvailable: true, spans: /* @__PURE__ */ React.createElement(SpanTreeView, { roots, defaultOpen: true }), raw: spans })));
  }

  // src/live.tsx
  var POLL_MS2 = 1500;
  var MAX_KEEP = 1500;
  function deriveStats(traces, spans) {
    let cost = 0;
    let tokens = 0;
    let errors = 0;
    const byKind = {};
    for (const t of traces) {
      cost += t.cost || 0;
      tokens += t.tokens || 0;
    }
    for (const s of spans) {
      if (s.status === "ERROR") errors++;
      const k = kindOf(s.name, s.attributes);
      byKind[k] = (byKind[k] || 0) + 1;
    }
    return { cost, tokens, errors, traces: traces.length, byKind };
  }
  function LivePage() {
    const [spans, setSpans] = useState([]);
    const [status, setStatus] = useState(null);
    const [error, setError] = useState(null);
    const [paused, setPaused] = useState(false);
    const [selected, setSelected] = useState(null);
    const [showPings, setShowPings] = useState(false);
    const cursor = useRef(0);
    const poll = useCallback(async () => {
      var _a;
      try {
        const st = await fetchJSON(`${API}/live/status`);
        setStatus(st);
        if (!st || st.live === false) return;
        const sp = await fetchJSON(`${API}/live/spans?since=${cursor.current}&limit=2000`);
        cursor.current = Math.max(sp.cursor || 0, cursor.current);
        if ((_a = sp.spans) == null ? void 0 : _a.length) setSpans((prev) => [...prev, ...sp.spans].slice(-MAX_KEEP));
        setError(null);
      } catch (e) {
        setError(String((e == null ? void 0 : e.message) || e));
      }
    }, []);
    useEffect(() => {
      poll();
    }, [poll]);
    usePolling(poll, POLL_MS2, !paused && !selected);
    const allTraces = groupLiveTraces(spans);
    const traces = showPings ? allTraces : allTraces.filter((t) => !isMcpKeepalivePing(t.rootName, t.error));
    const hiddenPings = allTraces.length - traces.length;
    const visibleSpans = showPings ? spans : traces.flatMap((t) => t.spans || []);
    const stats = deriveStats(traces, visibleSpans);
    const lastSession = spans.length ? sessionOf(spans[spans.length - 1]) : null;
    const now = Date.now();
    const buckets = new Array(50).fill(0);
    for (const s of visibleSpans) {
      const t = (s.end_time_unix_nano || s.start_time_unix_nano) / 1e6;
      const idx = 49 - Math.floor((now - t) / 2e3);
      if (idx >= 0 && idx < 50) buckets[idx]++;
    }
    if (status && status.live === false) {
      return /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, "Live mode is off"), status.reason || "Set dashboard_live: true in the plugin config (it's on by default), then run a turn.");
    }
    if (selected) {
      const fresh = groupLiveTraces(spans).find((t) => t.traceId === selected.traceId) || selected;
      const { roots } = liveTreeFromSpans(fresh.spans || []);
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement(LiveTraceDetail, { trace: fresh, roots, onBack: () => setSelected(null) }));
    }
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2.5" }, /* @__PURE__ */ React.createElement(Pulse, { active: !paused && ((status == null ? void 0 : status.spans) || 0) > 0 }), /* @__PURE__ */ React.createElement("span", { className: "text-base font-semibold tracking-tight" }, "Live agent activity"), /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, fmtInt(status == null ? void 0 : status.spans), " spans buffered", lastSession ? /* @__PURE__ */ React.createElement(React.Fragment, null, " ", "\xB7 session ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, String(lastSession).slice(0, 12))) : null)), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: () => setPaused((p) => !p) }, paused ? "\u25B6 Resume" : "\u23F8 Pause")), error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error }) : null, /* @__PURE__ */ React.createElement("div", { className: "otel-kpi-grid" }, /* @__PURE__ */ React.createElement(Stat, { label: "Cost", value: fmtCost(stats.cost), accent: "cost" }), /* @__PURE__ */ React.createElement(Stat, { label: "Tokens", value: fmtInt(stats.tokens) }), /* @__PURE__ */ React.createElement(Stat, { label: "Turns", value: fmtInt(stats.traces) }), /* @__PURE__ */ React.createElement(Stat, { label: "Spans", value: fmtInt(visibleSpans.length) }), /* @__PURE__ */ React.createElement(Stat, { label: "Errors", value: fmtInt(stats.errors), accent: stats.errors ? "error" : void 0 })), /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg flex items-center gap-4 border border-border px-3 py-2" }, /* @__PURE__ */ React.createElement(MiniLabel, null, "activity \xB7 spans per 2 s \xB7 last 100 s"), /* @__PURE__ */ React.createElement("div", { className: "otel-w-44" }, /* @__PURE__ */ React.createElement(Sparkline, { values: buckets })), /* @__PURE__ */ React.createElement("div", { className: "ml-auto flex flex-wrap gap-3" }, Object.keys(stats.byKind).sort((a, b) => stats.byKind[b] - stats.byKind[a]).slice(0, 7).map((k) => /* @__PURE__ */ React.createElement("span", { key: k, className: "inline-flex items-center gap-1.5 text-[11px] text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", { className: "otel-w-2 inline-block h-2 rounded-full", style: { background: KIND_HEX[k] } }), k, " ", stats.byKind[k])))), /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between pt-1" }, /* @__PURE__ */ React.createElement(MiniLabel, null, "recent turns"), /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-3 text-[11px] text-muted-foreground" }, /* @__PURE__ */ React.createElement("label", { className: "inline-flex cursor-pointer items-center gap-1.5" }, /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: showPings, onChange: (e) => setShowPings(e.target.checked) }), "show MCP keepalive pings", hiddenPings ? ` (${hiddenPings} hidden)` : ""), /* @__PURE__ */ React.createElement("span", null, "click a turn to open its span waterfall"))), /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-2" }, traces.length === 0 ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, "Waiting for activity\u2026"), "Run a Hermes turn (CLI, Telegram, anything). Each turn appears here as a card \u2014 open it to see every span, timing and attribute. No backend required.") : traces.map((t) => /* @__PURE__ */ React.createElement(LiveTraceCard, { key: t.traceId, trace: t, onSelect: setSelected }))));
  }

  // src/filters.tsx
  function Field({ label, children, className }) {
    return /* @__PURE__ */ React.createElement("div", { className: cn("space-y-1", className) }, /* @__PURE__ */ React.createElement(Label, { className: "text-[10px] uppercase tracking-wide text-muted-foreground" }, label), children);
  }
  function FilterBar({
    filters,
    onChange,
    onSubmit,
    backend,
    status,
    busy
  }) {
    const set = (k, v) => onChange({ ...filters, [k]: v });
    const input = (k, placeholder, type = "text") => {
      var _a;
      return /* @__PURE__ */ React.createElement(Input, { className: "h-8", type, placeholder, value: String((_a = filters[k]) != null ? _a : ""), onChange: (e) => set(k, e.target.value) });
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
      /* @__PURE__ */ React.createElement("div", { className: "otel-search-grid" }, /* @__PURE__ */ React.createElement(Field, { label: "status" }, /* @__PURE__ */ React.createElement(Select, { value: filters.status, onValueChange: (v) => set("status", v), className: "h-8" }, /* @__PURE__ */ React.createElement(SelectOption, { value: "" }, "any"), /* @__PURE__ */ React.createElement(SelectOption, { value: "ok" }, "ok"), /* @__PURE__ */ React.createElement(SelectOption, { value: "error" }, "error"))), /* @__PURE__ */ React.createElement(Field, { label: "kind" }, /* @__PURE__ */ React.createElement(Select, { value: filters.kind, onValueChange: (v) => set("kind", v), className: "h-8" }, /* @__PURE__ */ React.createElement(SelectOption, { value: "" }, "any"), KINDS.map((k) => /* @__PURE__ */ React.createElement(SelectOption, { key: k, value: k }, k)))), /* @__PURE__ */ React.createElement(Field, { label: "tool" }, input("tool", "terminal")), /* @__PURE__ */ React.createElement(Field, { label: "model" }, input("model", backend ? "exact model name" : "substring")), /* @__PURE__ */ React.createElement(Field, { label: "session id" }, input("session", "20260920_0814\u2026")), /* @__PURE__ */ React.createElement(Field, { label: "min duration (ms)" }, input("minDurationMs", "0", "number")), /* @__PURE__ */ React.createElement(Field, { label: "text" }, input("text", backend ? "in the prompt" : "anywhere in attributes")), /* @__PURE__ */ React.createElement(Field, { label: "trace id" }, input("traceId", "32 hex chars")), /* @__PURE__ */ React.createElement(Field, { label: "lookback" }, /* @__PURE__ */ React.createElement(Select, { value: String(filters.lookback), onValueChange: (v) => set("lookback", Number(v)), className: "h-8" }, /* @__PURE__ */ React.createElement(SelectOption, { value: "0.25" }, "15m"), /* @__PURE__ */ React.createElement(SelectOption, { value: "1" }, "1h"), /* @__PURE__ */ React.createElement(SelectOption, { value: "6" }, "6h"), /* @__PURE__ */ React.createElement(SelectOption, { value: "24" }, "24h"), /* @__PURE__ */ React.createElement(SelectOption, { value: "72" }, "3d"), /* @__PURE__ */ React.createElement(SelectOption, { value: "168" }, "7d"), /* @__PURE__ */ React.createElement(SelectOption, { value: "720" }, "30d")))),
      backend ? /* @__PURE__ */ React.createElement("div", { className: "otel-search-grid" }, /* @__PURE__ */ React.createElement(Field, { label: rawLabel, className: "otel-span-2" }, input("q", (status == null ? void 0 : status.raw_placeholder) || "")), /* @__PURE__ */ React.createElement(Field, { label: "service" }, input("service", "any")), /* @__PURE__ */ React.createElement("label", { className: "otel-self-end inline-flex cursor-pointer items-center gap-1.5 pb-2 text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: filters.rootsOnly, onChange: (e) => set("rootsOnly", e.target.checked) }), "roots only")) : null,
      /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement(Button, { type: "submit", size: "sm", disabled: !!busy }, busy ? "Searching\u2026" : "Search"), /* @__PURE__ */ React.createElement(
        Button,
        {
          type: "button",
          variant: "outline",
          size: "sm",
          onClick: () => onChange({ ...DEFAULT_FILTERS, lookback: filters.lookback, rootsOnly: filters.rootsOnly })
        },
        "Clear"
      ))
    );
  }

  // src/sessions.tsx
  function SessionCard({ row, open, onToggle, children }) {
    return /* @__PURE__ */ React.createElement("div", { className: cn("otel-card-bg border", row.errors ? "border-destructive/30" : "border-border") }, /* @__PURE__ */ React.createElement("div", { className: "flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2", onClick: onToggle, role: "button", tabIndex: 0 }, /* @__PURE__ */ React.createElement("span", { className: "w-3 shrink-0 text-xs text-muted-foreground" }, open ? "\u25BE" : "\u25B8"), /* @__PURE__ */ React.createElement("span", { className: "font-mono text-sm", title: row.session }, row.session), row.platform ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px]" }, row.platform) : null, row.errors ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "text-[10px]" }, row.errors, " error", row.errors === 1 ? "" : "s") : null, /* @__PURE__ */ React.createElement("span", { className: "ml-auto flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground" }, row.model ? /* @__PURE__ */ React.createElement("span", { className: "font-mono text-foreground/80" }, row.model) : null, /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, row.turns, " turn", row.turns === 1 ? "" : "s"), row.spans != null ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, row.spans, " spans") : null, row.toolCalls != null ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, row.toolCalls, " tool calls") : null, row.tokens != null ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtTokens(row.tokens), " tok") : null, row.cost != null ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums text-emerald-400" }, "$", row.cost.toFixed(4)) : null, /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtDurationMs((row.endNs - row.startNs) / 1e6)), /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        className: "otel-link text-[11px]",
        title: "this session's log lines and events",
        onClick: (e) => {
          e.stopPropagation();
          navigate({ tab: "logs", session: row.session, trace: "", lookback: "168" });
        }
      },
      "logs"
    ), /* @__PURE__ */ React.createElement("span", { title: `${fmtAbsTime(row.startNs)} \u2192 ${fmtAbsTime(row.endNs)}` }, fmtTimeAgo(row.endNs)))), open ? /* @__PURE__ */ React.createElement("div", { className: "border-t border-border/60 px-3 py-2" }, children) : null);
  }
  function LiveSessions({ filters, onSelectTrace }) {
    const [rows, setRows] = useState([]);
    const [error, setError] = useState(null);
    const [open, setOpen] = useState(null);
    const [turns, setTurns] = useState({});
    const load = useCallback(async () => {
      try {
        const r = await fetchJSON(`${API}/live/sessions?lookback_hours=${filters.lookback}&limit=100`);
        setRows(r.sessions || []);
        setError(null);
      } catch (e) {
        setError(String((e == null ? void 0 : e.message) || e));
      }
    }, [filters.lookback]);
    useEffect(() => {
      load();
    }, [load]);
    const toggle = async (sid) => {
      if (open === sid) return setOpen(null);
      setOpen(sid);
      if (!turns[sid]) {
        const p = liveParams({ ...filters, session: sid }, 200);
        try {
          const r = await fetchJSON(`${API}/live/traces?${p}`);
          setTurns((prev) => ({ ...prev, [sid]: r.traces || [] }));
        } catch {
          setTurns((prev) => ({ ...prev, [sid]: [] }));
        }
      }
    };
    if (error) return /* @__PURE__ */ React.createElement(ErrorBanner, { error });
    if (!rows.length)
      return /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground" }, "No sessions in the last ", filters.lookback, "h. Turns carry ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "hermes.session_id"), "; sessions group them.");
    return /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, rows.length, " session", rows.length === 1 ? "" : "s", " \xB7 click one to see its turns in order"), rows.map((row) => /* @__PURE__ */ React.createElement(SessionCard, { key: row.session, row, open: open === row.session, onToggle: () => toggle(row.session) }, turns[row.session] ? turns[row.session].length ? /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-1.5" }, turns[row.session].slice().sort((a, b) => a.startNs - b.startNs).map((t) => /* @__PURE__ */ React.createElement(LiveTraceCard, { key: t.traceId, trace: t, onSelect: onSelectTrace }))) : /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "No turns matched the current filters.") : /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "Loading\u2026"))));
  }
  function BackendSessions({ traces, renderTrace }) {
    const [open, setOpen] = useState(null);
    const rows = groupBySession(traces);
    const unattributed = traces.length - rows.reduce((n, r) => n + r.turns, 0);
    if (!traces.length) return null;
    if (!rows.length)
      return /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground" }, "None of the ", traces.length, " results carries a session id, so they cannot be grouped. The Turns view lists them.");
    const byId = {};
    for (const t of traces) byId[t.traceID || t.traceId] = t;
    return /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, rows.length, " session", rows.length === 1 ? "" : "s", " from ", traces.length, " results", unattributed ? ` \xB7 ${unattributed} without a session id` : ""), rows.map((row) => /* @__PURE__ */ React.createElement(SessionCard, { key: row.session, row, open: open === row.session, onToggle: () => setOpen(open === row.session ? null : row.session) }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-1.5" }, row.traceIds.map((id) => byId[id]).filter(Boolean).sort((a, b) => Number(a.startTimeUnixNano || 0) - Number(b.startTimeUnixNano || 0)).map((t) => renderTrace(t))))));
  }
  function ViewToggle({ view, onChange }) {
    const Btn = ({ id, label }) => /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        onClick: () => onChange(id),
        className: cn(
          "otel-toggle px-3 py-1 text-xs font-medium transition-colors",
          view === id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground"
        )
      },
      label
    );
    return /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg inline-flex border border-border p-0.5" }, /* @__PURE__ */ React.createElement(Btn, { id: "turns", label: "Turns" }), /* @__PURE__ */ React.createElement(Btn, { id: "sessions", label: "Sessions" }));
  }

  // src/traces.tsx
  var POLL_MS3 = 3e3;
  var VIEW_KEY = "hermes_otel.tracesView";
  function readView() {
    try {
      return localStorage.getItem(VIEW_KEY) === "sessions" ? "sessions" : "turns";
    } catch {
      return "turns";
    }
  }
  function LiveTraces({ view, wanted }) {
    const initial = {
      ...DEFAULT_FILTERS,
      session: wanted.session || "",
      lookback: wanted.session || wanted.trace ? 168 : DEFAULT_FILTERS.lookback
    };
    const [filters, setFilters] = useState(initial);
    const [applied, setApplied] = useState(initial);
    const [rows, setRows] = useState([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);
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
        const r = await fetchJSON(`${API}/live/traces?${liveParams(applied)}`);
        setRows(r.traces || []);
        setTotal(r.total || 0);
        setError(null);
      } catch (e) {
        setError(String((e == null ? void 0 : e.message) || e));
      } finally {
        inflight.current = false;
        setLoading(false);
      }
    }, [applied]);
    useEffect(() => {
      setLoading(true);
      load();
    }, [load]);
    usePolling(load, POLL_MS3, !paused && !selected && view === "turns");
    useEffect(() => {
      if (!selected) return;
      setDetailSpans(null);
      fetchJSON(`${API}/live/traces/${selected.traceId}`).then((r) => setDetailSpans(r.spans || [])).catch(() => setDetailSpans([]));
    }, [selected]);
    useEffect(() => {
      if (!wanted.trace) return;
      fetchJSON(`${API}/live/traces/${wanted.trace}`).then((r) => {
        if (r.trace) setSelected({ ...r.trace, spans: r.spans });
      }).catch(() => setError(`Trace ${wanted.trace} is not in the live store`));
    }, [wanted.trace]);
    useEffect(() => {
      writeNav({ trace: selected ? String(selected.traceId) : "" });
    }, [selected]);
    const submit = () => setApplied(filters);
    if (selected) {
      const spans = detailSpans || [];
      const { roots } = liveTreeFromSpans(spans);
      const trace = { ...selected, spans };
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, detailSpans === null ? /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "Loading spans\u2026") : null, /* @__PURE__ */ React.createElement(LiveTraceDetail, { trace, roots, onBack: () => setSelected(null) }));
    }
    const pingCount = rows.filter((t) => isMcpKeepalivePing(t.rootName, t.error)).length;
    const shown = showPings ? rows : rows.filter((t) => !isMcpKeepalivePing(t.rootName, t.error));
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement(Card, null, /* @__PURE__ */ React.createElement(CardContent, { className: "space-y-3 pt-4" }, /* @__PURE__ */ React.createElement(FilterBar, { filters, onChange: setFilters, onSubmit: submit, backend: false, busy: loading }))), error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error }) : null, view === "sessions" ? /* @__PURE__ */ React.createElement(LiveSessions, { filters: applied, onSelectTrace: setSelected }) : /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3 text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", null, shown.length, " of ", total, " trace", total === 1 ? "" : "s", isDefaultFilters(applied) ? "" : " matching", " in the last ", applied.lookback, "h"), /* @__PURE__ */ React.createElement("label", { className: "inline-flex cursor-pointer items-center gap-1.5" }, /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: showPings, onChange: (e) => setShowPings(e.target.checked) }), "show MCP keepalive pings", pingCount ? ` (${pingCount})` : ""), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", className: "ml-auto", onClick: () => setPaused((p) => !p) }, paused ? "\u25B6 Resume" : "\u23F8 Pause")), shown.length === 0 ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, total ? "Nothing matched" : "No traces yet"), total ? "Widen the lookback or clear a filter." : "Run a Hermes turn \u2014 each turn appears here as a trace you can open into a span waterfall. No backend needed.") : /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-2" }, shown.map((t) => /* @__PURE__ */ React.createElement(LiveTraceCard, { key: t.traceId, trace: t, onSelect: setSelected })))));
  }
  function StatusBar({ status, onRefresh }) {
    if (!status) return null;
    const configured = status.configured;
    const caps = [configured ? "traces" : null, status.metrics ? "metrics" : null, status.logs ? "logs" : null].filter(Boolean);
    return /* @__PURE__ */ React.createElement("div", { className: "flex items-start justify-between gap-3" }, /* @__PURE__ */ React.createElement("div", { className: "min-w-0 flex-1 space-y-1.5" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: cn("h-2.5 w-2.5 rounded-full", configured ? "otel-pulse-dot" : "bg-muted-foreground/40") }), /* @__PURE__ */ React.createElement("span", { className: "text-base font-semibold tracking-tight" }, configured ? status.name || status.type : "Not configured"), configured && status.type && status.type !== status.name ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px] uppercase" }, status.type) : null, caps.map((c) => /* @__PURE__ */ React.createElement(Badge, { key: c, variant: "secondary", className: "text-[10px]" }, c)), status.query_backend_pin && status.query_backend_pin === status.active ? /* @__PURE__ */ React.createElement("span", { className: "text-[10px] text-muted-foreground" }, "default (query_backend)") : null), configured && status.query_url ? /* @__PURE__ */ React.createElement("div", { className: "truncate font-mono text-xs text-muted-foreground" }, status.query_url) : null), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: onRefresh }, "Refresh"));
  }
  function BackendTraceCard({ trace, onSelect }) {
    const cat = categorize(trace.rootTraceName || "");
    const attrs = traceAttrs(trace);
    const startNs = trace.startTimeUnixNano ? Number(trace.startTimeUnixNano) : 0;
    const model = attrs["llm.model_name"] || attrs["gen_ai.response.model"];
    const toolName = attrs["tool.name"];
    const totalTokens = attrs["gen_ai.usage.total_tokens"] || attrs["llm.token_count.total"];
    const cost = attrs["hermes.cost.usage"];
    const isError = attrs["status"] === "error" || attrs["error.type"];
    const inP = clip(extractInputPreview(attrs), 140);
    const outP = clip(extractOutputPreview(attrs), 140);
    const spanCount = traceSpanCount(trace);
    const Icon = cat.Icon;
    return /* @__PURE__ */ React.createElement(
      "div",
      {
        className: cn(
          "otel-card-bg otel-hover-parent flex cursor-pointer items-start gap-3 border p-3 transition-colors hover:bg-secondary/30",
          isError ? "border-destructive/30" : "border-border"
        ),
        role: "button",
        tabIndex: 0,
        onClick: () => onSelect(trace),
        onKeyDown: (e) => {
          if (e.key === "Enter") onSelect(trace);
        },
        title: trace.traceID || trace.traceId
      },
      /* @__PURE__ */ React.createElement("div", { className: cn("shrink-0 pt-0.5", cat.color) }, /* @__PURE__ */ React.createElement(Icon, { size: 16 })),
      /* @__PURE__ */ React.createElement("div", { className: "min-w-0 flex-1 space-y-1" }, /* @__PURE__ */ React.createElement("div", { className: "flex min-w-0 items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: "truncate font-mono text-sm" }, trace.rootTraceName || "\u2014"), cat.label ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "shrink-0 text-[10px]" }, cat.label) : null, isError ? /* @__PURE__ */ React.createElement(Badge, { variant: "destructive", className: "shrink-0 text-[10px]" }, "error") : null), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-1" }, toolName ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "font-mono text-[10px]" }, String(toolName)) : null, model ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "font-mono text-[10px]" }, String(model)) : null), inP || outP ? /* @__PURE__ */ React.createElement("div", { className: "otel-pl-2 space-y-0.5 border-l-2 border-border/60 text-xs" }, inP ? /* @__PURE__ */ React.createElement("div", { className: "truncate text-foreground/80" }, /* @__PURE__ */ React.createElement("span", { className: "otel-mr-2 text-[10px] text-muted-foreground" }, "in"), inP) : null, outP ? /* @__PURE__ */ React.createElement("div", { className: "truncate text-foreground/80" }, /* @__PURE__ */ React.createElement("span", { className: "otel-mr-2 text-[10px] text-muted-foreground" }, "out"), outP) : null) : null, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("span", null, trace.rootServiceName || "\u2014"), /* @__PURE__ */ React.createElement("span", { className: "text-border" }, "\xB7"), /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, fmtDurationMs(trace.durationMs)), spanCount != null ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("span", { className: "text-border" }, "\xB7"), /* @__PURE__ */ React.createElement("span", { className: "tabular-nums" }, spanCount, " spans")) : null, totalTokens != null ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("span", { className: "text-border" }, "\xB7"), /* @__PURE__ */ React.createElement("span", { className: "inline-flex items-center gap-1 tabular-nums" }, /* @__PURE__ */ React.createElement(IconCoins, { size: 12, className: "opacity-70" }), fmtTokens(totalTokens), " tok")) : null, cost ? /* @__PURE__ */ React.createElement("span", { className: "tabular-nums text-emerald-400" }, "$", Number(cost).toFixed(4)) : null, /* @__PURE__ */ React.createElement("span", { className: "text-border" }, "\xB7"), /* @__PURE__ */ React.createElement("span", { title: fmtAbsTime(startNs) }, fmtTimeAgo(startNs))), /* @__PURE__ */ React.createElement("div", { className: "truncate font-mono text-[10px] text-muted-foreground/60" }, trace.traceID || trace.traceId)),
      /* @__PURE__ */ React.createElement("div", { className: "otel-self-center otel-reveal shrink-0 text-muted-foreground" }, /* @__PURE__ */ React.createElement(IconChevronRight, { size: 16 }))
    );
  }
  function BackendTraceDetail({
    trace,
    detail,
    loading,
    error,
    onBack,
    source,
    status
  }) {
    const tree = detail ? buildSpanTree(detail.batches || detail.trace && detail.trace.batches) : { roots: [], all: [] };
    const rootSpan = tree.roots[0] || null;
    const rootAttrs = rootSpan ? rootSpan._attrs : traceAttrs(trace);
    const durationMs = rootSpan ? rootSpan.durationMs : trace.durationMs;
    const traceId = String(trace.traceID || trace.traceId);
    const isError = tree.all.some((s) => {
      var _a, _b, _c;
      return ((_c = (_a = s.status) == null ? void 0 : _a.code) != null ? _c : (_b = s.status) == null ? void 0 : _b.statusCode) === 2;
    }) || traceAttrs(trace)["status"] === "error";
    return /* @__PURE__ */ React.createElement(Card, null, /* @__PURE__ */ React.createElement(CardHeader, { className: "otel-space-y-0" }, /* @__PURE__ */ React.createElement(
      TraceHeader,
      {
        title: (rootSpan == null ? void 0 : rootSpan.name) || trace.rootTraceName || "\u2014",
        traceId,
        service: trace.rootServiceName,
        durationMs,
        rootAttrs,
        spansAttrs: tree.all.map((s) => s._attrs),
        error: isError,
        uiUrl: (detail == null ? void 0 : detail.ui_url) || null,
        uiLabel: (status == null ? void 0 : status.name) || (status == null ? void 0 : status.type) || null,
        source,
        onBack
      }
    )), /* @__PURE__ */ React.createElement(CardContent, null, loading ? /* @__PURE__ */ React.createElement("div", { className: "py-8 text-center text-sm text-muted-foreground" }, "Loading trace\u2026") : null, error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error }) : null, !loading && !error ? /* @__PURE__ */ React.createElement(TraceTabs, { traceId, source, logsAvailable: !!(status == null ? void 0 : status.logs), spans: /* @__PURE__ */ React.createElement(SpanTreeView, { roots: tree.roots }), raw: detail }) : null));
  }
  function BackendTraces({
    status,
    onRefresh,
    source,
    view,
    wanted
  }) {
    const [filters, setFilters] = useState({
      ...DEFAULT_FILTERS,
      session: wanted.session || "",
      lookback: wanted.session || wanted.trace ? 168 : DEFAULT_FILTERS.lookback
    });
    const [traces, setTraces] = useState(null);
    const [showPings, setShowPings] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [selected, setSelected] = useState(null);
    const [detail, setDetail] = useState(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [detailError, setDetailError] = useState(null);
    const search = useCallback(async () => {
      if (!(status == null ? void 0 : status.configured)) return;
      setLoading(true);
      setError(null);
      setSelected(null);
      const id = filters.traceId.trim();
      if (id) {
        setTraces([{ traceID: id, rootTraceName: "(by id)", spanSets: [] }]);
        setSelected({ traceID: id, rootTraceName: "(by id)" });
        setLoading(false);
        return;
      }
      try {
        const r = await fetchJSON(`${API}/traces/search?${backendParams(filters, source)}`);
        setTraces(r.traces || []);
      } catch (e) {
        setError(String((e == null ? void 0 : e.message) || e).replace(/^.*?:\s*/, ""));
        setTraces([]);
      } finally {
        setLoading(false);
      }
    }, [filters, status, source]);
    useEffect(() => {
      setTraces(null);
      setSelected(null);
      setError(null);
    }, [source]);
    useEffect(() => {
      if (wanted.trace && (status == null ? void 0 : status.configured)) setSelected({ traceID: wanted.trace, rootTraceName: "(by id)" });
    }, [wanted.trace, status == null ? void 0 : status.configured]);
    useEffect(() => {
      writeNav({ trace: selected ? String(selected.traceID || selected.traceId) : "" });
    }, [selected]);
    useEffect(() => {
      if (!selected) return;
      setDetail(null);
      setDetailError(null);
      setDetailLoading(true);
      const p = withBackend(new URLSearchParams(), source);
      fetchJSON(`${API}/traces/${selected.traceID || selected.traceId}?${p}`).then(setDetail).catch((e) => setDetailError(String((e == null ? void 0 : e.message) || e))).finally(() => setDetailLoading(false));
    }, [selected, source]);
    if (!(status == null ? void 0 : status.configured))
      return /* @__PURE__ */ React.createElement(Card, null, /* @__PURE__ */ React.createElement(CardContent, { className: "space-y-2 pt-4 text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("p", null, (status == null ? void 0 : status.reason) || "No queryable trace backend configured."), /* @__PURE__ */ React.createElement("p", { className: "text-xs" }, "That's fine \u2014 the ", /* @__PURE__ */ React.createElement("span", { className: "font-medium text-foreground" }, "\u26A1 Live"), " source needs no backend. Add a backend of a queryable type to browse historical traces here: ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, ((status == null ? void 0 : status.queryable_types) || []).join(", ") || "none available"), ".")));
    if (selected)
      return /* @__PURE__ */ React.createElement(
        BackendTraceDetail,
        {
          trace: selected,
          detail,
          loading: detailLoading,
          error: detailError,
          onBack: () => setSelected(null),
          source,
          status
        }
      );
    const isPing = (t) => isMcpKeepalivePing(t.rootTraceName, traceAttrs(t)["status"] === "error");
    const pingCount = traces ? traces.filter(isPing).length : 0;
    const shown = traces && !showPings ? traces.filter((t) => !isPing(t)) : traces;
    const renderCard = (t) => /* @__PURE__ */ React.createElement(BackendTraceCard, { key: t.traceID || t.traceId, trace: t, onSelect: setSelected });
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement(Card, null, /* @__PURE__ */ React.createElement(CardContent, { className: "space-y-3 pt-4" }, /* @__PURE__ */ React.createElement(StatusBar, { status, onRefresh }), /* @__PURE__ */ React.createElement(FilterBar, { filters, onChange: setFilters, onSubmit: search, backend: true, status, busy: loading }), /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between gap-3" }, /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, shown == null ? "Not searched yet" : `${shown.length} trace${shown.length === 1 ? "" : "s"}`), /* @__PURE__ */ React.createElement("label", { className: "inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground" }, /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: showPings, onChange: (e) => setShowPings(e.target.checked) }), "show MCP keepalive pings", pingCount ? ` (${pingCount})` : "")))), error ? /* @__PURE__ */ React.createElement("div", { className: "space-y-1" }, /* @__PURE__ */ React.createElement(ErrorBanner, { error: `Backend query failed: ${error}` }), /* @__PURE__ */ React.createElement("p", { className: "px-1 text-xs text-muted-foreground" }, "Backend unreachable from the dashboard. Use the \u26A1 Live source \u2014 it reads the in-process store and always works.")) : null, shown && shown.length > 0 ? view === "sessions" ? /* @__PURE__ */ React.createElement(BackendSessions, { traces: shown, renderTrace: renderCard }) : /* @__PURE__ */ React.createElement("div", { className: "flex flex-col gap-2" }, shown.map(renderCard)) : shown && shown.length === 0 && !error ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground" }, pingCount ? `Only MCP keepalive pings matched (${pingCount} hidden) \u2014 tick "show MCP keepalive pings" to see them.` : "No traces matched \u2014 widen the lookback or run a turn.") : null);
  }
  function TracesPage() {
    const { source, setSource, status, refresh, isLive } = useSource();
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
        setNav({ ...d });
      };
      window.addEventListener(NAV_EVENT, onNav);
      return () => window.removeEventListener(NAV_EVENT, onNav);
    }, [setSource]);
    useEffect(() => {
      const wantedSource = readNav().source;
      if (wantedSource && wantedSource !== source) setSource(wantedSource);
    }, []);
    useEffect(() => {
      writeNav({ tab: "traces", source, view });
    }, [source, view]);
    const key = `${source}:${nav.trace || ""}:${nav.session || ""}`;
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement(SourceSelect, { source, onChange: setSource, status, need: "traces" }), /* @__PURE__ */ React.createElement(ViewToggle, { view, onChange: setView })), isLive ? /* @__PURE__ */ React.createElement(MiniLabel, null, "queried from the in-process store") : null), isLive ? /* @__PURE__ */ React.createElement(LiveTraces, { key, view, wanted: nav }) : /* @__PURE__ */ React.createElement(BackendTraces, { key, status, onRefresh: refresh, source, view, wanted: nav }));
  }

  // src/metrics.tsx
  var POLL_MS4 = 15e3;
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
  var RANGES = [
    { label: "15m", hours: 0.25, bucket: 15 },
    { label: "1h", hours: 1, bucket: 60 },
    { label: "6h", hours: 6, bucket: 300 },
    { label: "24h", hours: 24, bucket: 900 },
    { label: "7d", hours: 168, bucket: 3600 * 3 }
  ];
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
  var PALETTE = ["#38bdf8", "#34d399", "#fbbf24", "#a78bfa", "#f472b6", "#22d3ee", "#6ee7b7", "#94a3b8"];
  function BarList({ rows, fmt, color }) {
    if (!rows.length) return /* @__PURE__ */ React.createElement("div", { className: "py-3 text-xs text-muted-foreground" }, "No data in this range.");
    const max = Math.max(1e-9, ...rows.map((r) => r.value));
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-1.5" }, rows.slice(0, 10).map((r) => /* @__PURE__ */ React.createElement("div", { key: r.label, className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: "otel-w-28 shrink-0 truncate font-mono text-[11px] text-muted-foreground", title: r.label }, r.label === "_" ? "all" : r.label), /* @__PURE__ */ React.createElement("div", { className: "relative h-4 flex-1 bg-muted/30" }, /* @__PURE__ */ React.createElement("div", { className: "absolute inset-y-0 left-0", style: { width: `${r.value / max * 100}%`, background: color || "var(--color-primary, #34d399)" } })), /* @__PURE__ */ React.createElement("span", { className: "otel-w-16 shrink-0 text-right tabular-nums text-xs" }, fmt ? fmt(r.value) : fmtInt(Math.round(r.value))))));
  }
  function Panel({ title, sub, children }) {
    return /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg border border-border p-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-baseline justify-between gap-2" }, /* @__PURE__ */ React.createElement(MiniLabel, null, title), sub ? /* @__PURE__ */ React.createElement("span", { className: "text-[10px] text-muted-foreground" }, sub) : null), /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, children));
  }
  function Chart({ b, fmt }) {
    if (!b || !Object.keys(b.series).length) return /* @__PURE__ */ React.createElement("div", { className: "py-3 text-xs text-muted-foreground" }, "No data in this range.");
    const series = Object.keys(b.series).slice(0, 8).map((label, i) => ({ label: label === "_" ? b.name : label, color: PALETTE[i % PALETTE.length], points: b.series[label].map((v) => v != null ? v : 0) }));
    const n = b.buckets.length;
    const labels = [0, Math.floor(n / 2), n - 1].map((i) => fmtAbsTime(b.buckets[i]).replace(/^.*?, /, ""));
    return /* @__PURE__ */ React.createElement(LineChart, { series, labels, fmt });
  }
  function MetricsPage() {
    var _a, _b, _c, _d, _e, _f;
    const { source, setSource, status, isLive } = useSource();
    const [range, setRange] = useState(RANGES[1]);
    const [names, setNames] = useState([]);
    const [error, setError] = useState(null);
    const [panels, setPanels] = useState({});
    const [pick, setPick] = useState("");
    const [groupBy, setGroupBy] = useState("");
    const [customGroup, setCustomGroup] = useState("");
    const [agg, setAgg] = useState("sum");
    const [explore, setExplore] = useState(null);
    const base = isLive ? `${API}/live` : API;
    const canQuery = isLive || !!(status == null ? void 0 : status.metrics);
    const query = useCallback(
      async (name, group, aggregate) => {
        const p = withBackend(new URLSearchParams({ name, agg: aggregate, lookback_hours: String(range.hours), bucket_s: String(range.bucket) }), source);
        if (group) p.set("group_by", group);
        try {
          return await fetchJSON(`${base}/metrics/query?${p}`);
        } catch {
          return null;
        }
      },
      [base, range, source]
    );
    const load = useCallback(async () => {
      if (!canQuery) return;
      try {
        const p = withBackend(new URLSearchParams({ lookback_hours: String(range.hours) }), source);
        const r = await fetchJSON(`${base}/metrics/names?${p}`);
        const list = r.names || [];
        setNames(list);
        setError(null);
        const have = new Set(list.map((n) => n.name));
        const want = [
          ["tokens", isLive ? "hermes.token.usage" : "hermes_token_usage", "token_type", "sum"],
          ["cost", isLive ? "hermes.cost.usage" : "hermes_cost_usage", "", "sum"],
          ["calls", isLive ? "hermes.model.usage" : "hermes_model_usage", "model", isLive ? "count" : "sum"],
          ["tools", isLive ? "hermes.tool.duration" : "hermes_tool_duration_sum", "tool_name", isLive ? "avg" : "sum"],
          ["approvals", isLive ? "hermes.approval.count" : "hermes_approval_count", "choice", isLive ? "count" : "sum"],
          ["cache", isLive ? "hermes.prompt_cache.tokens" : "hermes_prompt_cache_tokens", "token_type", "sum"],
          ["cpu", "process.cpu.utilization", "", "avg"],
          ["gpu", "hw.gpu.utilization", "", "avg"]
        ];
        const out = {};
        await Promise.all(
          want.map(async ([key, name, group, aggregate]) => {
            out[key] = have.has(name) ? await query(name, group, aggregate) : null;
          })
        );
        setPanels(out);
      } catch (e) {
        setError(String((e == null ? void 0 : e.message) || e));
      }
    }, [base, canQuery, isLive, query, range.hours, source]);
    useEffect(() => {
      load();
    }, [load]);
    usePolling(load, POLL_MS4, canQuery);
    const runExplore = useCallback(async () => {
      if (!pick) return;
      setExplore(await query(pick, customGroup.trim() || groupBy, agg));
    }, [agg, customGroup, groupBy, pick, query]);
    useEffect(() => {
      runExplore();
    }, [runExplore]);
    const tokens = panels.tokens || null;
    const cost = panels.cost || null;
    const calls = panels.calls || null;
    const tools = panels.tools || null;
    const approvals = panels.approvals || null;
    const cache = panels.cache || null;
    const totalTokens = seriesTotal(tokens);
    const totalCost = seriesTotal(cost);
    const tokenRows = useMemo(() => totalsByLabel(tokens), [tokens]);
    const cacheRows = useMemo(() => totalsByLabel(cache), [cache]);
    const cacheRead = (_d = (_c = (_a = tokenRows.find((r) => /cache/i.test(r.label))) == null ? void 0 : _a.value) != null ? _c : (_b = cacheRows.find((r) => /read|hit/i.test(r.label))) == null ? void 0 : _b.value) != null ? _d : null;
    const cacheAll = (_f = (_e = tokenRows.find((r) => r.label === "input")) == null ? void 0 : _e.value) != null ? _f : cacheRows.reduce((a, r) => a + r.value, 0);
    const unit = (n) => UNITS[n] || UNITS[metricOtlpName(n)] || "";
    const header = /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement(SourceSelect, { source, onChange: setSource, status, need: "metrics" }), /* @__PURE__ */ React.createElement(
      Select,
      {
        value: String(range.hours),
        onValueChange: (v) => setRange(RANGES.find((r) => String(r.hours) === v) || RANGES[1]),
        className: "otel-w-56 h-8"
      },
      RANGES.map((r) => /* @__PURE__ */ React.createElement(SelectOption, { key: r.label, value: String(r.hours) }, rangeLabel(r)))
    )), /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, names.length, " instrument", names.length === 1 ? "" : "s", " in this range"));
    if (!canQuery)
      return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, header, /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, "This source does not serve metrics"), "Pick the Live source, or a backend whose adapter serves metrics (OpenObserve, SigNoz, Uptrace, LGTM)."));
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, header, error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error }) : null, names.length === 0 ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, "No metrics in this range"), "Run a Hermes turn, or widen the range. Token usage, cost, tool durations and approvals appear here.") : /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("div", { className: "otel-kpi-grid" }, /* @__PURE__ */ React.createElement(Stat, { label: "Tokens", value: fmtInt(Math.round(totalTokens)) }), /* @__PURE__ */ React.createElement(
      Stat,
      {
        label: "Cost",
        value: cost && cost.points ? fmtCost(totalCost) : "\u2014",
        sub: cost && cost.points ? void 0 : "no pricing data",
        accent: cost && cost.points ? "cost" : void 0
      }
    ), /* @__PURE__ */ React.createElement(Stat, { label: "Model calls", value: fmtInt(Math.round(seriesTotal(calls))) }), /* @__PURE__ */ React.createElement(Stat, { label: "Tool calls", value: tools ? fmtInt(tools.points) : "0" }), /* @__PURE__ */ React.createElement(
      Stat,
      {
        label: "Cache read",
        value: cacheRead != null && cacheAll ? `${Math.round(cacheRead / cacheAll * 100)}%` : "\u2014",
        sub: cacheRead != null ? `${fmtInt(Math.round(cacheRead))} of ${fmtInt(Math.round(cacheAll))} input tokens` : "no cache data"
      }
    )), /* @__PURE__ */ React.createElement("div", { className: "grid gap-3 lg:grid-cols-2" }, /* @__PURE__ */ React.createElement(Panel, { title: "Tokens over time", sub: `by token_type \xB7 per ${range.bucket}s` }, /* @__PURE__ */ React.createElement(Chart, { b: tokens })), /* @__PURE__ */ React.createElement(Panel, { title: "Cost over time", sub: cost && cost.points ? `USD \xB7 per ${range.bucket}s` : "no pricing data for the models used" }, /* @__PURE__ */ React.createElement(Chart, { b: cost, fmt: fmtCost })), /* @__PURE__ */ React.createElement(Panel, { title: "Tokens by type" }, /* @__PURE__ */ React.createElement(BarList, { rows: totalsByLabel(tokens), color: "#38bdf8" })), /* @__PURE__ */ React.createElement(Panel, { title: "Calls by model" }, /* @__PURE__ */ React.createElement(BarList, { rows: totalsByLabel(calls), color: "#a78bfa" })), /* @__PURE__ */ React.createElement(Panel, { title: isLive ? "Avg tool duration" : "Tool duration (sum)", sub: "ms" }, /* @__PURE__ */ React.createElement(BarList, { rows: isLive ? meanByLabel(tools) : totalsByLabel(tools), fmt: fmtDurationMs, color: "#fbbf24" })), /* @__PURE__ */ React.createElement(Panel, { title: "Approvals by choice" }, /* @__PURE__ */ React.createElement(BarList, { rows: totalsByLabel(approvals), color: "#f472b6" })), panels.cpu || panels.gpu ? /* @__PURE__ */ React.createElement(Panel, { title: "Host", sub: "utilisation ratio, avg per bucket" }, /* @__PURE__ */ React.createElement(Chart, { b: panels.cpu || panels.gpu })) : null), /* @__PURE__ */ React.createElement(Panel, { title: "Explore any instrument", sub: "server-side buckets; group by an attribute" }, /* @__PURE__ */ React.createElement("div", { className: "otel-search-grid" }, /* @__PURE__ */ React.createElement(Select, { value: pick, onValueChange: setPick, className: "h-8" }, /* @__PURE__ */ React.createElement(SelectOption, { value: "" }, "pick an instrument\u2026"), names.map((n) => /* @__PURE__ */ React.createElement(SelectOption, { key: n.name, value: n.name }, n.name, n.count != null ? ` (${n.count})` : "", unit(n.name) ? ` \xB7 ${unit(n.name)}` : ""))), /* @__PURE__ */ React.createElement(Select, { value: groupBy, onValueChange: setGroupBy, className: "h-8" }, GROUP_KEYS.map((k) => /* @__PURE__ */ React.createElement(SelectOption, { key: k, value: k }, k ? `group by ${k}` : "no grouping"))), /* @__PURE__ */ React.createElement(Input, { className: "h-8", placeholder: "or any attribute", value: customGroup, onChange: (e) => setCustomGroup(e.target.value) }), /* @__PURE__ */ React.createElement(Select, { value: agg, onValueChange: setAgg, className: "h-8" }, ["sum", "count", "avg", "max", "last"].map((a) => /* @__PURE__ */ React.createElement(SelectOption, { key: a, value: a }, a))), /* @__PURE__ */ React.createElement(Button, { type: "button", size: "sm", onClick: runExplore, disabled: !pick }, "Query")), pick ? /* @__PURE__ */ React.createElement("div", { className: "mt-3 space-y-3" }, /* @__PURE__ */ React.createElement(Chart, { b: explore }), /* @__PURE__ */ React.createElement("div", { className: "text-[11px] text-muted-foreground" }, explore ? `${explore.points} point${explore.points === 1 ? "" : "s"} \xB7 ${Object.keys(explore.series).length} series \xB7 ${explore.agg} per ${explore.bucketS}s${unit(pick) ? ` \xB7 ${unit(pick)}` : ""}${explore.cumulative ? " \xB7 cumulative counter shown as increases" : ""}` : "no data"), /* @__PURE__ */ React.createElement(BarList, { rows: agg === "avg" ? meanByLabel(explore) : totalsByLabel(explore) })) : null)));
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
  function groupEnv(env, showUnset) {
    const seen = /* @__PURE__ */ new Set();
    const groups = [...ENV_GROUP_ORDER, ...env.map((e) => e.group).filter((g) => !ENV_GROUP_ORDER.includes(g))];
    return groups.filter((g) => seen.has(g) ? false : (seen.add(g), true)).map((g) => ({
      group: g,
      label: ENV_GROUP_LABELS[g] || g,
      entries: env.filter((e) => e.group === g && (showUnset || e.set))
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
  var stop = (e) => e.stopPropagation();
  function BackendCard({ b, q }) {
    var _a;
    const href = b.ui.url;
    const open = () => {
      if (href) window.open(href, "_blank", "noopener,noreferrer");
    };
    const query = queryCapabilityLine(q, b.display_type);
    const metricsOn = (_a = b.signals.metrics) == null ? void 0 : _a.exported;
    return /* @__PURE__ */ React.createElement(
      "div",
      {
        className: cn("otel-card-bg border border-border px-3 py-2.5", href ? "otel-backend-card" : ""),
        onClick: href ? open : void 0,
        onKeyDown: href ? (e) => e.key === "Enter" ? open() : void 0 : void 0,
        role: href ? "link" : void 0,
        tabIndex: href ? 0 : void 0,
        title: href ? `${b.ui.note} \xB7 opens ${href} in a new window` : b.ui.note
      },
      /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2" }, href ? /* @__PURE__ */ React.createElement("a", { className: "otel-backend-name", href, target: "_blank", rel: "noreferrer noopener", onClick: stop }, b.name, /* @__PURE__ */ React.createElement(IconExternal, { size: 12, className: "otel-backend-ext" })) : /* @__PURE__ */ React.createElement("span", { className: "text-sm font-medium" }, b.name), showTypeBadge(b) ? /* @__PURE__ */ React.createElement(Badge, { variant: "secondary", className: "text-[10px] uppercase" }, b.display_type) : null, b.docs_path ? /* @__PURE__ */ React.createElement(
        "a",
        {
          className: "otel-link text-[11px] text-muted-foreground",
          href: DOCS_BASE + b.docs_path,
          target: "_blank",
          rel: "noreferrer",
          onClick: stop,
          title: `${b.display_type} backend docs`
        },
        "docs"
      ) : null, /* @__PURE__ */ React.createElement("span", { className: "ml-auto flex flex-wrap gap-1" }, SIGNALS.map((sig) => {
        const st = b.signals[sig];
        if (!st) return null;
        const pill = signalPill(sig, st, b.display_type);
        return /* @__PURE__ */ React.createElement("span", { key: sig, className: cn("otel-pill", `otel-pill-${pill.cls}`), title: pill.title }, pill.label);
      }))),
      query ? /* @__PURE__ */ React.createElement("div", { className: "mt-1 text-[11px] text-muted-foreground", title: query.title }, query.text) : null,
      /* @__PURE__ */ React.createElement("div", { className: "otel-attr-table mt-2 text-xs" }, Object.entries(b.fields).map(([k, v]) => /* @__PURE__ */ React.createElement(Row, { key: k, k }, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, String(v)))), /* @__PURE__ */ React.createElement(Row, { k: "ui", title: "the link the card opens; set ui_url on the entry to override" }, href ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("a", { className: "otel-link font-mono", href, target: "_blank", rel: "noreferrer noopener", onClick: stop }, href), /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, " \xB7 ", b.ui.source === "file" ? "ui_url" : "derived")) : /* @__PURE__ */ React.createElement("span", { className: "otel-unset" }, b.ui.note)), metricsOn ? /* @__PURE__ */ React.createElement(Row, { k: "temporality", title: "aggregation temporality of this backend's metric reader" }, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, b.metrics_temporality.value), /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, " \xB7 ", b.metrics_temporality.source)) : null, Object.entries(b.query_fields || {}).map(([k, v]) => /* @__PURE__ */ React.createElement(Row, { key: `q-${k}`, k, title: "read by the dashboard's query adapter, not by the exporter" }, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, String(v)), /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, " \xB7 query"))), b.headers ? Object.entries(b.headers).map(([k, v]) => /* @__PURE__ */ React.createElement(Row, { key: `h-${k}`, k: `header ${k}` }, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, String(v)))) : null, b.credentials.map((c) => {
        var _a2;
        return /* @__PURE__ */ React.createElement(Row, { key: c.field, k: c.field }, c.set ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, (_a2 = c.value) != null ? _a2 : "set"), /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, " \xB7 ", c.source)) : /* @__PURE__ */ React.createElement("span", { className: "otel-warn" }, c.source || "not set"));
      }))
    );
  }
  function CopyButton2({ text }) {
    const [done, setDone] = useState(false);
    const copy = async () => {
      try {
        await navigator.clipboard.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      } catch {
      }
    };
    return /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: copy, title: "copy to clipboard" }, done ? /* @__PURE__ */ React.createElement(IconCheck, { size: 13 }) : /* @__PURE__ */ React.createElement(IconCopy, { size: 13 }), /* @__PURE__ */ React.createElement("span", { className: "ml-1" }, done ? "copied" : "copy"));
  }
  function ConfigFileLine({ r }) {
    const c = r.config;
    return /* @__PURE__ */ React.createElement("div", { className: "otel-card-bg border border-border px-3 py-2 text-xs" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-x-3 gap-y-1" }, /* @__PURE__ */ React.createElement(MiniLabel, null, "config file"), c.path ? /* @__PURE__ */ React.createElement("span", { className: "font-mono break-all" }, c.path) : /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground" }, "none found"), /* @__PURE__ */ React.createElement("span", { className: "otel-pill", title: "how the file was chosen: HERMES_OTEL_CONFIG, then $HERMES_HOME/hermes_otel.yaml, then the plugin directory" }, pathSourceLabel(c.path_source)), c.exists && c.mtime ? /* @__PURE__ */ React.createElement("span", { className: "text-muted-foreground", title: fmtAbsTime(c.mtime * 1e9) }, "edited ", fmtTimeAgo(c.mtime * 1e9)) : null, c.path && !c.exists ? /* @__PURE__ */ React.createElement("span", { className: "otel-warn" }, "file does not exist; defaults and environment variables apply") : null, c.exists && !c.parse_ok ? /* @__PURE__ */ React.createElement("span", { className: "otel-warn" }, "file could not be parsed; defaults and environment variables apply") : null), !c.path ? /* @__PURE__ */ React.createElement("div", { className: "mt-1 text-muted-foreground" }, "Create ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, c.durable_path), " to change settings; see the", " ", /* @__PURE__ */ React.createElement("a", { className: "otel-link", href: `${DOCS_BASE}/configuration/overview`, target: "_blank", rel: "noreferrer" }, "configuration guide"), ".") : null, c.unknown_keys.length ? /* @__PURE__ */ React.createElement("div", { className: "mt-1 text-muted-foreground" }, "Also in the file:", " ", c.unknown_keys.map((u, i) => /* @__PURE__ */ React.createElement("span", { key: u.key }, i ? ", " : "", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, u.key), u.note ? /* @__PURE__ */ React.createElement(React.Fragment, null, " ", "(", /* @__PURE__ */ React.createElement(Description, { text: u.note }), ")") : /* @__PURE__ */ React.createElement("span", { className: "otel-warn" }, " (not a known setting)")))) : null);
  }
  function SettingsPage() {
    var _a, _b;
    const [report, setReport] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(false);
    const [view, setView] = useState("structured");
    const [reveal, setReveal] = useState(false);
    const [query, setQuery] = useState("");
    const [changedOnly, setChangedOnly] = useState(false);
    const [rawMode, setRawMode] = useState("file");
    const [showUnset, setShowUnset] = useState(false);
    const [status, setStatus] = useState(null);
    const load = useCallback(async () => {
      setLoading(true);
      fetchJSON(`${API}/status`).then((st) => setStatus(st)).catch(() => setStatus(null));
      try {
        const r = await fetchJSON(`${API}/settings?reveal=${reveal ? "true" : "false"}`);
        setReport(r);
        setError(null);
      } catch (e) {
        setError(String((e == null ? void 0 : e.message) || e));
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
    return /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement("span", { className: "text-base font-semibold tracking-tight" }, "Settings"), report ? /* @__PURE__ */ React.createElement("span", { className: "text-xs text-muted-foreground" }, "hermes-otel ", report.process.plugin_version || "?", " \xB7 resolved ", fmtTimeAgo(report.resolved_at * 1e9)) : null, /* @__PURE__ */ React.createElement("div", { className: "ml-auto flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-1 border border-border p-0.5" }, VIEWS.map((v) => /* @__PURE__ */ React.createElement(
      "button",
      {
        key: v.id,
        type: "button",
        onClick: () => setView(v.id),
        className: cn(
          "otel-toggle px-3 py-1 text-xs font-medium transition-colors",
          view === v.id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground"
        )
      },
      v.label
    ))), /* @__PURE__ */ React.createElement(
      "label",
      {
        className: "inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground",
        title: "credential values are masked unless this is on"
      },
      /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: reveal, onChange: (e) => setReveal(e.target.checked) }),
      "show secrets"
    ), /* @__PURE__ */ React.createElement(Button, { variant: "outline", size: "sm", onClick: load, disabled: loading, title: "re-read the file and environment" }, /* @__PURE__ */ React.createElement(IconRefresh, { size: 13, className: loading ? "otel-spin" : "" }), /* @__PURE__ */ React.createElement("span", { className: "ml-1" }, "reload")))), error ? /* @__PURE__ */ React.createElement(ErrorBanner, { error }) : null, !report && !error ? /* @__PURE__ */ React.createElement("div", { className: "text-sm text-muted-foreground" }, "Loading settings\u2026") : null, report ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement(ConfigFileLine, { r: report }), /* @__PURE__ */ React.createElement("div", { className: "otel-kpi-grid" }, /* @__PURE__ */ React.createElement(Stat, { label: "Settings", value: report.fields.length, sub: `${report.counts.changed} changed from default` }), /* @__PURE__ */ React.createElement(Stat, { label: "From file", value: report.counts.file, sub: report.config.exists ? "in the config file" : "no file" }), /* @__PURE__ */ React.createElement(Stat, { label: "From env", value: report.counts.env, sub: "HERMES_OTEL_* variables" }), /* @__PURE__ */ React.createElement(Stat, { label: "Backends", value: backends.length, sub: backends.map((b) => b.name).join(", ") || "live store only" }), /* @__PURE__ */ React.createElement(
      Stat,
      {
        label: "Content",
        value: cap ? cap.mode : "?",
        sub: cap ? cap.detail : "",
        accent: (cap == null ? void 0 : cap.mode) === "off" ? void 0 : (cap == null ? void 0 : cap.mode) === "full" ? "cost" : void 0
      }
    )), view === "structured" ? /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement(
      Input,
      {
        value: query,
        onChange: (e) => setQuery(e.target.value),
        placeholder: "filter by name, value, description\u2026",
        className: "otel-w-56 h-8 text-xs"
      }
    ), /* @__PURE__ */ React.createElement("label", { className: "inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground" }, /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: changedOnly, onChange: (e) => setChangedOnly(e.target.checked) }), "changed from default only"), /* @__PURE__ */ React.createElement("span", { className: "ml-auto text-[11px] text-muted-foreground" }, shown.length, " of ", fields.length, " \xB7 precedence: ", /* @__PURE__ */ React.createElement("span", { className: "otel-src otel-src-env" }, "env"), " over", " ", /* @__PURE__ */ React.createElement("span", { className: "otel-src otel-src-file" }, "file"), " over ", /* @__PURE__ */ React.createElement("span", { className: "otel-src otel-src-default" }, "default"))), groups.length === 0 ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground" }, "No setting matches.") : null, groups.map((g) => /* @__PURE__ */ React.createElement("div", { key: g.group, className: "space-y-1" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2 pt-1" }, /* @__PURE__ */ React.createElement(MiniLabel, null, g.group), /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-muted-foreground/70" }, g.fields.length)), g.group === "Backends" ? /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, g.fields.map((f) => /* @__PURE__ */ React.createElement(FieldRow, { key: f.key, f })), backends.length ? /* @__PURE__ */ React.createElement("div", { className: "otel-backend-grid" }, backends.map((b, i) => /* @__PURE__ */ React.createElement(BackendCard, { key: `${b.name}-${i}`, b, q: queryCaps[b.name] }))) : /* @__PURE__ */ React.createElement("div", { className: "text-xs text-muted-foreground" }, "No ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, "backends:"), " entry. Telemetry stays in the live store on this machine; single-backend environment variables, if any, are listed under Environment.")) : /* @__PURE__ */ React.createElement("div", { className: "otel-settings-list" }, g.fields.map((f) => /* @__PURE__ */ React.createElement(FieldRow, { key: f.key, f })))))) : null, view === "raw" ? /* @__PURE__ */ React.createElement("div", { className: "space-y-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-1 border border-border p-0.5" }, ["file", "effective"].map((id) => /* @__PURE__ */ React.createElement(
      "button",
      {
        key: id,
        type: "button",
        onClick: () => setRawMode(id),
        className: cn(
          "otel-toggle px-3 py-1 text-xs font-medium transition-colors",
          rawMode === id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground"
        )
      },
      id === "file" ? "File as written" : "Effective config"
    ))), /* @__PURE__ */ React.createElement("span", { className: "min-w-0 flex-1 truncate text-[11px] text-muted-foreground", title: rawMode === "file" ? report.config.path || "" : "" }, rawMode === "file" ? report.config.exists ? `${report.config.path}${reveal ? "" : " \xB7 secrets masked"}` : "no config file to show" : "every setting after env, file and defaults are applied; each key notes its source"), /* @__PURE__ */ React.createElement("span", { className: "shrink-0" }, /* @__PURE__ */ React.createElement(CopyButton2, { text: rawMode === "file" ? report.config.raw || "" : report.effective_yaml }))), rawMode === "file" ? report.config.raw != null ? /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-raw" }, report.config.raw) : /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, "No config file"), report.config.raw_error ? report.config.raw_error : /* @__PURE__ */ React.createElement(React.Fragment, null, "Create ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, report.config.durable_path), ". The Effective config view is a starting point you can paste in.")) : /* @__PURE__ */ React.createElement("pre", { className: "otel-pre otel-raw" }, report.effective_yaml)) : null, view === "env" ? /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, ((_b = report.env_notices) != null ? _b : []).map((n) => /* @__PURE__ */ React.createElement("div", { key: n, className: "border border-dashed border-border px-3 py-2 text-[11px] text-muted-foreground", role: "status" }, n)), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-3" }, /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-muted-foreground" }, envCounts(report.env).set, " set of ", envCounts(report.env).known, " the plugin reads, as seen by the dashboard process"), /* @__PURE__ */ React.createElement("label", { className: "ml-auto inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground" }, /* @__PURE__ */ React.createElement("input", { type: "checkbox", checked: showUnset, onChange: (e) => setShowUnset(e.target.checked) }), "show unset variables")), groupEnv(report.env, showUnset).length === 0 ? /* @__PURE__ */ React.createElement("div", { className: "border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground" }, /* @__PURE__ */ React.createElement("div", { className: "mb-1 text-base font-medium text-foreground" }, "No plugin environment variables set"), 'Every setting comes from the file or its default. Tick "show unset variables" to see every variable the plugin would read.') : null, groupEnv(report.env, showUnset).map((g) => /* @__PURE__ */ React.createElement("div", { key: g.group, className: "space-y-1" }, /* @__PURE__ */ React.createElement(MiniLabel, null, g.label), /* @__PURE__ */ React.createElement("div", { className: "otel-settings-list" }, g.entries.map((e) => /* @__PURE__ */ React.createElement("div", { key: e.name, className: cn("otel-env-row", e.set && "otel-changed") }, /* @__PURE__ */ React.createElement("div", { className: "min-w-0" }, /* @__PURE__ */ React.createElement("div", { className: "font-mono text-xs break-all" }, e.name), e.maps_to ? /* @__PURE__ */ React.createElement("div", { className: "text-[10px] text-muted-foreground/70" }, "sets ", /* @__PURE__ */ React.createElement("span", { className: "font-mono" }, e.maps_to)) : null), /* @__PURE__ */ React.createElement("div", { className: "min-w-0" }, e.set ? /* @__PURE__ */ React.createElement("span", { className: "font-mono text-xs break-all" }, e.value) : /* @__PURE__ */ React.createElement("span", { className: "otel-unset" }, "unset"), invalidEnv[e.name] ? /* @__PURE__ */ React.createElement("div", { className: "otel-warn text-[11px]" }, invalidEnv[e.name]) : null), /* @__PURE__ */ React.createElement("div", { className: "text-[11px] leading-snug text-muted-foreground" }, /* @__PURE__ */ React.createElement(Description, { text: e.description })))))))) : null, /* @__PURE__ */ React.createElement("div", { className: "text-[11px] text-muted-foreground" }, report.process.note)) : null);
  }

  // src/index.tsx
  var TABS = [
    { id: "live", label: "Live", Icon: IconActivity, render: () => /* @__PURE__ */ React.createElement(LivePage, null) },
    { id: "traces", label: "Traces", Icon: IconList, render: () => /* @__PURE__ */ React.createElement(TracesPage, null) },
    { id: "metrics", label: "Metrics", Icon: IconChart, render: () => /* @__PURE__ */ React.createElement(MetricsPage, null) },
    { id: "logs", label: "Logs", Icon: IconList, render: () => /* @__PURE__ */ React.createElement(LogsPage, null) },
    { id: "settings", label: "Settings", Icon: IconSettings, render: () => /* @__PURE__ */ React.createElement(SettingsPage, null) }
  ];
  function OtelDashboard() {
    const [tab, setTabState] = useState(() => TABS.some((t) => t.id === readNav().tab) ? readNav().tab : "live");
    const setTab = (id) => {
      setTabState(id);
      writeNav({ tab: id, trace: "", session: "" });
    };
    useEffect(() => {
      const onNav = (e) => {
        const d = e.detail || {};
        if (d.tab && TABS.some((t) => t.id === d.tab)) setTabState(d.tab);
      };
      window.addEventListener(NAV_EVENT, onNav);
      return () => window.removeEventListener(NAV_EVENT, onNav);
    }, []);
    const active = TABS.find((t) => t.id === tab) || TABS[0];
    return /* @__PURE__ */ React.createElement("div", { className: "otel-root space-y-4" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-1 border-b border-border" }, TABS.map((t) => {
      const on = t.id === tab;
      const Icon = t.Icon;
      return /* @__PURE__ */ React.createElement(
        "button",
        {
          key: t.id,
          onClick: () => setTab(t.id),
          className: cn(
            "otel-tab inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors",
            on ? "otel-tab-active text-foreground" : "text-muted-foreground hover:text-foreground"
          )
        },
        /* @__PURE__ */ React.createElement(Icon, { size: 15 }),
        t.label
      );
    }), /* @__PURE__ */ React.createElement("span", { className: "ml-auto pr-1 font-mono text-[11px] text-muted-foreground/60" }, "hermes-otel")), /* @__PURE__ */ React.createElement("div", null, active.render()));
  }
  if (sdkOk) {
    register("hermes_otel", OtelDashboard);
  } else {
    console.error("[hermes_otel] dashboard SDK unavailable \u2014 not registering");
  }
})();
