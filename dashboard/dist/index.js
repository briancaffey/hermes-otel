/* hermes-otel dashboard tab — OTel trace viewer for Tempo / LGTM.
 *
 * No build step. Plain ES5-ish IIFE that consumes React + UI components
 * from window.__HERMES_PLUGIN_SDK__. See ../manifest.json for routing
 * and ../plugin_api.py for the FastAPI proxy that talks to Tempo.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) {
    console.error("[hermes_otel] dashboard SDK not available");
    return;
  }

  var React = SDK.React;
  var h = React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useCallback = SDK.hooks.useCallback;
  var useMemo = SDK.hooks.useMemo;

  var C = SDK.components;
  var Card = C.Card;
  var CardHeader = C.CardHeader;
  var CardTitle = C.CardTitle;
  var CardContent = C.CardContent;
  var Badge = C.Badge;
  var Button = C.Button;
  var Input = C.Input;
  var Label = C.Label;
  var Select = C.Select;
  var SelectOption = C.SelectOption;
  var Separator = C.Separator;

  var fetchJSON = SDK.fetchJSON;
  var cn = (SDK.utils && SDK.utils.cn) || function () {
    return Array.prototype.filter.call(arguments, Boolean).join(" ");
  };

  var API = "/api/plugins/hermes_otel";

  // ── helpers ─────────────────────────────────────────────────────────

  function decodeAttrValue(v) {
    if (v == null) return null;
    if (v.stringValue !== undefined) return v.stringValue;
    if (v.intValue !== undefined) return Number(v.intValue);
    if (v.doubleValue !== undefined) return v.doubleValue;
    if (v.boolValue !== undefined) return v.boolValue;
    if (v.arrayValue && v.arrayValue.values) {
      return v.arrayValue.values.map(decodeAttrValue);
    }
    if (v.kvlistValue && v.kvlistValue.values) {
      return attrsObject(v.kvlistValue.values);
    }
    return v;
  }

  function attrsObject(attrs) {
    var out = {};
    if (!attrs) return out;
    for (var i = 0; i < attrs.length; i++) {
      var a = attrs[i];
      if (a && a.key) out[a.key] = decodeAttrValue(a.value);
    }
    return out;
  }

  function fmtDurationMs(ms) {
    if (ms == null || isNaN(ms)) return "—";
    if (ms < 1) return ms.toFixed(2) + "ms";
    if (ms < 1000) return Math.round(ms) + "ms";
    return (ms / 1000).toFixed(2) + "s";
  }

  function fmtAbsTime(unixNanoStr) {
    if (!unixNanoStr) return "—";
    var ns = typeof unixNanoStr === "string" ? Number(unixNanoStr) : unixNanoStr;
    if (!isFinite(ns)) return "—";
    var d = new Date(ns / 1e6);
    return d.toLocaleString();
  }

  function fmtTimeAgo(unixNanoStr) {
    if (!unixNanoStr) return "—";
    var ns = typeof unixNanoStr === "string" ? Number(unixNanoStr) : unixNanoStr;
    if (!isFinite(ns)) return "—";
    var seconds = Math.max(0, (Date.now() - ns / 1e6) / 1000);
    if (seconds < 60) return Math.floor(seconds) + "s ago";
    if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
    if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago";
    return Math.floor(seconds / 86400) + "d ago";
  }

  function spanKindLabel(span) {
    var attrs = span._attrs || {};
    var kind = attrs["openinference.span.kind"] || attrs["llm.provider"] ? "llm" : null;
    if (attrs["tool.name"]) return "tool";
    if (kind) return kind.toLowerCase();
    return null;
  }

  // ── inline lucide-style icons ───────────────────────────────────────
  // Small SVG factories so we don't drag in lucide-react as a dep. Shape
  // + stroke-width matches Hermes's shadcn-aligned visuals.

  function _svg(size, className, children) {
    return h("svg", {
      width: size || 16,
      height: size || 16,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: 2,
      strokeLinecap: "round",
      strokeLinejoin: "round",
      className: className || "",
      "aria-hidden": true,
    }, children);
  }
  var IconZap = function (p) { return _svg(p && p.size, p && p.className,
    h("polygon", { points: "13 2 3 14 12 14 11 22 21 10 12 10 13 2" })
  ); };
  var IconWrench = function (p) { return _svg(p && p.size, p && p.className,
    h("path", { d: "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" })
  ); };
  var IconTerminal = function (p) { return _svg(p && p.size, p && p.className,
    h("polyline", { points: "4 17 10 11 4 5" }),
    h("line", { x1: 12, x2: 20, y1: 19, y2: 19 })
  ); };
  var IconClock = function (p) { return _svg(p && p.size, p && p.className,
    h("circle", { cx: 12, cy: 12, r: 10 }),
    h("polyline", { points: "12 6 12 12 16 14" })
  ); };
  var IconActivity = function (p) { return _svg(p && p.size, p && p.className,
    h("polyline", { points: "22 12 18 12 15 21 9 3 6 12 2 12" })
  ); };
  var IconChevronRight = function (p) { return _svg(p && p.size, p && p.className,
    h("path", { d: "m9 18 6-6-6-6" })
  ); };
  var IconChevronLeft = function (p) { return _svg(p && p.size, p && p.className,
    h("path", { d: "m15 18-6-6 6-6" })
  ); };
  var IconCoins = function (p) { return _svg(p && p.size, p && p.className,
    h("circle", { cx: 8, cy: 8, r: 6 }),
    h("path", { d: "M18.09 10.37A6 6 0 1 1 10.34 18" }),
    h("path", { d: "M7 6h1v4" }),
    h("path", { d: "m16.71 13.88.7.71-2.82 2.82" })
  ); };

  // Categorise a trace by its root span name; the category drives the
  // card's icon + colour so glancing at the list says what happened.
  var TRACE_CATEGORIES = {
    llm:   { Icon: IconZap,      color: "text-sky-500",     label: "llm" },
    tool:  { Icon: IconWrench,   color: "text-amber-500",   label: "tool" },
    agent: { Icon: IconTerminal, color: "text-emerald-500", label: "agent" },
    cron:  { Icon: IconClock,    color: "text-violet-500",  label: "cron" },
    other: { Icon: IconActivity, color: "text-muted-foreground", label: null },
  };

  function categorizeTrace(trace) {
    var name = (trace.rootTraceName || "").toLowerCase();
    if (name.indexOf("api.") === 0 || name.indexOf("llm.") === 0) return TRACE_CATEGORIES.llm;
    if (name.indexOf("tool.") === 0) return TRACE_CATEGORIES.tool;
    if (name === "agent" || name.indexOf("agent.") === 0) return TRACE_CATEGORIES.agent;
    if (name === "cron" || name.indexOf("cron.") === 0) return TRACE_CATEGORIES.cron;
    return TRACE_CATEGORIES.other;
  }

  // Merge all attributes returned by TraceQL ``select()`` across every
  // span in the trace's first spanSet. First non-null wins so the "most
  // specific" span (api.*) wins over the wrapping cron/agent root.
  function traceAttrs(trace) {
    var out = {};
    var spanSets = trace.spanSets || (trace.spanSet ? [trace.spanSet] : []);
    if (!spanSets.length) return out;
    var spans = spanSets[0].spans || [];
    // Priority pass: api.* → tool.* → everything else. Matches intuitive
    // expectation that a cron trace's "model" is the nested api span's model.
    function priority(span) {
      var n = (span.name || "").toLowerCase();
      if (n.indexOf("api.") === 0) return 0;
      if (n.indexOf("tool.") === 0) return 1;
      if (n.indexOf("llm.") === 0) return 2;
      return 3;
    }
    var sorted = spans.slice().sort(function (a, b) { return priority(a) - priority(b); });
    for (var i = 0; i < sorted.length; i++) {
      var attrs = sorted[i].attributes || [];
      for (var j = 0; j < attrs.length; j++) {
        var a = attrs[j];
        if (!a.key || out[a.key] != null) continue;
        var decoded = decodeAttrValue(a.value);
        if (decoded !== null && decoded !== undefined && decoded !== "") {
          out[a.key] = decoded;
        }
      }
    }
    return out;
  }

  function fmtTokens(n) {
    if (n == null || isNaN(n)) return null;
    var num = Number(n);
    if (num >= 10000) return (num / 1000).toFixed(num >= 100000 ? 0 : 1) + "k";
    return num.toLocaleString();
  }

  // Squash whitespace + clip to ``max`` chars. Used for card snippets so
  // multi-line prompts don't wrap and fold a card to three rows.
  function clip(s, max) {
    if (s == null) return null;
    var str = typeof s === "string" ? s : String(s);
    str = str.replace(/\s+/g, " ").trim();
    if (!str) return null;
    if (str.length <= max) return str;
    return str.slice(0, max - 1) + "…";
  }

  // Pull the "most useful" user-facing input text out of whatever the
  // plugin put on the span. For LLM spans that's ``input.value`` — a
  // JSON messages array where we take the last ``role:user`` turn. For
  // tool / agent spans it's usually a plain string or small object.
  function extractInputPreview(attrs) {
    var raw = attrs["input.value"];
    if (raw == null) return null;
    if (typeof raw === "string") {
      var trimmed = raw.trim();
      if (trimmed.charAt(0) === "[" || trimmed.charAt(0) === "{") {
        try {
          var parsed = JSON.parse(trimmed);
          if (Array.isArray(parsed)) {
            for (var i = parsed.length - 1; i >= 0; i--) {
              var m = parsed[i];
              if (m && m.role === "user") {
                var c = m.content;
                if (typeof c === "string") return c;
                if (Array.isArray(c)) {
                  // Multimodal content list — join text parts.
                  var parts = [];
                  for (var k = 0; k < c.length; k++) {
                    var p = c[k];
                    if (typeof p === "string") parts.push(p);
                    else if (p && typeof p.text === "string") parts.push(p.text);
                  }
                  if (parts.length) return parts.join(" ");
                }
                if (c != null) return JSON.stringify(c);
              }
            }
            // No user message — fall back to first system/assistant content
            // so something shows up for unusual traces.
            for (var j = 0; j < parsed.length; j++) {
              if (parsed[j] && typeof parsed[j].content === "string") return parsed[j].content;
            }
          }
          if (parsed && typeof parsed === "object") {
            return JSON.stringify(parsed);
          }
        } catch (_) { /* not JSON, treat as plain string below */ }
      }
      return raw;
    }
    return String(raw);
  }

  function extractOutputPreview(attrs) {
    return attrs["llm.output.content"] || attrs["output.value"] || null;
  }

  function traceSpanCount(trace) {
    if (trace.serviceStats) {
      var total = 0;
      for (var k in trace.serviceStats) {
        if (Object.prototype.hasOwnProperty.call(trace.serviceStats, k)) {
          total += (trace.serviceStats[k].spanCount || 0);
        }
      }
      if (total > 0) return total;
    }
    var ss = trace.spanSets || (trace.spanSet ? [trace.spanSet] : []);
    if (ss.length && ss[0].matched) return ss[0].matched;
    return null;
  }

  function statusColor(span) {
    if (!span.status) return null;
    var code = span.status.code;
    if (code === 2 || code === "STATUS_CODE_ERROR") return "error";
    if (code === 1 || code === "STATUS_CODE_OK") return "ok";
    return null;
  }

  function buildSpanTree(batches) {
    if (!batches || !batches.length) return { roots: [], all: [] };
    var all = [];
    for (var i = 0; i < batches.length; i++) {
      var b = batches[i];
      var resourceAttrs = attrsObject(b.resource && b.resource.attributes);
      var scopeSpans = b.scopeSpans || b.instrumentationLibrarySpans || [];
      for (var j = 0; j < scopeSpans.length; j++) {
        var sp = scopeSpans[j].spans || [];
        for (var k = 0; k < sp.length; k++) {
          var span = sp[k];
          var startNs = Number(span.startTimeUnixNano || 0);
          var endNs = Number(span.endTimeUnixNano || 0);
          all.push({
            spanId: span.spanId,
            parentSpanId: span.parentSpanId || null,
            name: span.name,
            startNs: startNs,
            endNs: endNs,
            durationMs: endNs && startNs ? (endNs - startNs) / 1e6 : 0,
            status: span.status || null,
            _attrs: attrsObject(span.attributes),
            _resource: resourceAttrs,
            children: [],
          });
        }
      }
    }
    var byId = {};
    all.forEach(function (s) { byId[s.spanId] = s; });
    var roots = [];
    all.forEach(function (s) {
      if (s.parentSpanId && byId[s.parentSpanId]) {
        byId[s.parentSpanId].children.push(s);
      } else {
        roots.push(s);
      }
    });
    function sortRec(list) {
      list.sort(function (a, b) { return a.startNs - b.startNs; });
      list.forEach(function (n) { sortRec(n.children); });
    }
    sortRec(roots);
    return { roots: roots, all: all };
  }

  function flatten(roots) {
    var out = [];
    function walk(node, depth) {
      out.push({ span: node, depth: depth });
      node.children.forEach(function (c) { walk(c, depth + 1); });
    }
    roots.forEach(function (r) { walk(r, 0); });
    return out;
  }

  // ── small UI atoms ──────────────────────────────────────────────────

  function MiniLabel(props) {
    return h("span", {
      className: "text-[11px] font-medium text-muted-foreground",
    }, props.children);
  }

  function KV(props) {
    return h("div", { className: "flex items-baseline gap-2 text-sm" },
      h(MiniLabel, null, props.k),
      h("span", { className: "font-mono text-foreground" }, props.v == null ? "—" : String(props.v))
    );
  }

  function ErrorBanner(props) {
    if (!props.error) return null;
    return h("div", {
      className: "rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive",
    }, String(props.error));
  }

  // ── status / empty states ───────────────────────────────────────────

  function StatusBar(props) {
    var s = props.status;
    if (!s) return null;
    var configured = s.configured;
    var dotClass = configured
      ? "h-2.5 w-2.5 rounded-full bg-emerald-500"
      : "h-2.5 w-2.5 rounded-full bg-muted-foreground/40";
    var primary = configured ? (s.name || s.type) : "Not configured";
    var typeChip = configured && s.type && s.type !== s.name
      ? h(Badge, { variant: "secondary", className: "text-[10px] uppercase" }, s.type)
      : null;

    return h("div", { className: "flex items-start justify-between gap-3" },
      h("div", { className: "min-w-0 flex-1 space-y-1.5" },
        h("div", { className: "flex items-center gap-2" },
          h("span", { className: dotClass }),
          h("span", { className: "text-base font-semibold tracking-tight" }, primary),
          typeChip
        ),
        configured && s.query_url
          ? h("div", { className: "hermes-otel-url truncate font-mono text-xs text-muted-foreground" }, s.query_url)
          : !configured
            ? h("div", { className: "text-sm text-muted-foreground" }, "No queryable backend configured")
            : null,
        s.backends && s.backends.length
          ? h("div", { className: "flex flex-wrap items-center gap-1.5 pt-0.5" }, s.backends.map(function (b, i) {
              return h(Badge, {
                key: i,
                variant: b.supported ? "default" : "secondary",
                className: "text-[10px]",
              }, b.name + (b.supported ? "" : " · read-only"));
            }))
          : null
      ),
      h(Button, {
        variant: "outline",
        size: "sm",
        onClick: props.onRefresh,
      }, "Refresh status")
    );
  }

  function EmptyState(props) {
    var s = props.status;
    return h(Card, null,
      h(CardHeader, null, h(CardTitle, null, "OTel Traces")),
      h(CardContent, { className: "space-y-3" },
        h("p", { className: "text-sm text-muted-foreground" }, s ? s.reason : "Loading…"),
        s && s.backends && s.backends.length
          ? h("div", { className: "space-y-2" },
              h(MiniLabel, null, "Configured backends"),
              h("ul", { className: "space-y-1" }, s.backends.map(function (b, i) {
                return h("li", { key: i, className: "flex items-center gap-2 text-sm" },
                  h(Badge, { variant: b.supported ? "default" : "secondary" }, b.type),
                  h("span", { className: "font-mono text-xs text-muted-foreground" },
                    b.endpoint || "(no endpoint)"),
                  b.supported
                    ? h("span", { className: "text-xs text-emerald-600" }, "queryable")
                    : h("span", { className: "text-xs text-muted-foreground" }, "view-only in its own UI")
                );
              }))
            )
          : null,
        h("p", { className: "text-xs text-muted-foreground" },
          "Add an ", h("code", null, "lgtm"), " or ",
          h("code", null, "tempo"), " backend in ",
          h("code", null, "~/.hermes/plugins/hermes_otel/config.yaml"),
          " to enable trace browsing here."
        )
      )
    );
  }

  // ── filters ─────────────────────────────────────────────────────────

  function FiltersForm(props) {
    var f = props.filters;
    var status = props.status || {};
    // Per-backend label + placeholder — the raw field means different
    // things to different backends (TraceQL / GraphQL filter / SQL
    // WHERE / UQL / tag pairs) so the form reflects the active one.
    var rawLabel = status.query_lang_label
      ? status.query_lang_label + " filter (optional)"
      : "Query filter (optional)";
    var rawPlaceholder = status.raw_placeholder || "";

    function set(k, v) {
      var next = Object.assign({}, f);
      next[k] = v;
      props.onChange(next);
    }
    return h("form", {
        className: "grid gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_120px_140px_120px] items-end",
        onSubmit: function (e) { e.preventDefault(); props.onSubmit(); },
      },
      h("div", { className: "space-y-1.5 md:col-span-2" },
        h(Label, { htmlFor: "otel-q" }, rawLabel),
        h(Input, {
          id: "otel-q",
          placeholder: rawPlaceholder,
          value: f.q,
          onChange: function (e) { set("q", e.target.value); },
        })
      ),
      h("div", { className: "space-y-1.5" },
        h(Label, { htmlFor: "otel-svc" }, "Service"),
        h(Input, {
          id: "otel-svc",
          placeholder: "any",
          value: f.service || "",
          onChange: function (e) { set("service", e.target.value); },
        })
      ),
      h("div", { className: "space-y-1.5" },
        h(Label, { htmlFor: "otel-lookback" }, "Lookback"),
        h(Select, {
          id: "otel-lookback",
          value: String(f.lookback),
          onValueChange: function (v) { set("lookback", Number(v)); },
        },
          h(SelectOption, { value: "0.25" }, "15m"),
          h(SelectOption, { value: "1" }, "1h"),
          h(SelectOption, { value: "6" }, "6h"),
          h(SelectOption, { value: "24" }, "24h"),
          h(SelectOption, { value: "72" }, "3d"),
          h(SelectOption, { value: "168" }, "7d")
        )
      ),
      h("div", { className: "space-y-1.5" },
        h(Label, { htmlFor: "otel-match" }, "Match"),
        h(Select, {
          id: "otel-match",
          value: f.rootsOnly ? "root" : "any",
          onValueChange: function (v) { set("rootsOnly", v === "root"); },
        },
          h(SelectOption, { value: "root" }, "Root span only"),
          h(SelectOption, { value: "any" }, "Any span")
        )
      ),
      h("div", { className: "space-y-1.5" },
        h(Label, { htmlFor: "otel-mind" }, "Min duration (ms)"),
        h(Input, {
          id: "otel-mind",
          type: "number",
          min: "0",
          placeholder: "any",
          value: f.minDuration,
          onChange: function (e) { set("minDuration", e.target.value); },
        })
      ),
      // Hidden submit so pressing Enter in any field still fires onSubmit;
      // the visible Search button lives outside the collapsible filters.
      h("button", { type: "submit", className: "hidden", tabIndex: -1, "aria-hidden": "true" })
    );
  }

  // ── trace list ──────────────────────────────────────────────────────

  function TraceCard(props) {
    var t = props.trace;
    var cat = categorizeTrace(t);
    var attrs = traceAttrs(t);
    var startNs = t.startTimeUnixNano
      ? Number(t.startTimeUnixNano)
      : (t.startTime ? new Date(t.startTime).getTime() * 1e6 : 0);
    var model = attrs["llm.model_name"];
    var provider = attrs["llm.provider"];
    var apiMode = attrs["llm.api_mode"];
    var totalTokens = attrs["gen_ai.usage.total_tokens"];
    var inputTokens = attrs["gen_ai.usage.input_tokens"];
    var outputTokens = attrs["gen_ai.usage.output_tokens"];
    var toolName = attrs["tool.name"];
    var finishReason = attrs["llm.response.finish_reason"];
    var status = attrs["status"];
    var spanCount = traceSpanCount(t);
    var isError = status === "error" || status === "STATUS_CODE_ERROR";
    var inputRaw = extractInputPreview(attrs);
    var outputRaw = extractOutputPreview(attrs);
    var inputPreview = clip(inputRaw, 140);
    var outputPreview = clip(outputRaw, 140);

    // Build secondary chip row: model / provider / apiMode / toolName.
    var chips = [];
    if (toolName) {
      chips.push({ key: "tool", label: String(toolName), variant: "secondary", mono: true });
    }
    if (model) {
      chips.push({ key: "model", label: String(model), variant: "secondary", mono: true });
    }
    if (provider) {
      chips.push({ key: "provider", label: String(provider), variant: "outline" });
    }
    if (apiMode) {
      chips.push({ key: "apiMode", label: String(apiMode), variant: "outline" });
    }
    if (finishReason && finishReason !== "stop") {
      chips.push({ key: "finish", label: "finish: " + finishReason, variant: "outline" });
    }

    // Dot separators in the meta line — wrap as a single array with keys so
    // React doesn't nag.
    var metaBits = [];
    function pushMeta(key, node) {
      if (node == null) return;
      if (metaBits.length) {
        metaBits.push(h("span", { key: key + "-sep", className: "text-border" }, "·"));
      }
      metaBits.push(h("span", { key: key }, node));
    }
    pushMeta("service", t.rootServiceName || null);
    pushMeta("dur", h("span", { className: "tabular-nums" }, fmtDurationMs(t.durationMs)));
    if (spanCount != null) {
      pushMeta("spans", h("span", { className: "tabular-nums" }, spanCount + " span" + (spanCount === 1 ? "" : "s")));
    }
    if (totalTokens != null) {
      var tokDetail = (inputTokens != null && outputTokens != null)
        ? inputTokens.toLocaleString() + " in / " + outputTokens.toLocaleString() + " out"
        : null;
      pushMeta("tokens", h("span", {
        className: "inline-flex items-center gap-1 tabular-nums",
        title: tokDetail,
      },
        h(IconCoins, { size: 12, className: "opacity-70" }),
        fmtTokens(totalTokens) + " tok"
      ));
    }
    pushMeta("time", h("span", { title: fmtAbsTime(startNs) }, fmtTimeAgo(startNs)));

    return h("div", {
      className: cn(
        "group relative border bg-card/40 cursor-pointer overflow-hidden transition-colors",
        "hover:bg-secondary/30 hover:border-border focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        isError ? "border-destructive/30 bg-destructive/[0.04]" : "border-border"
      ),
      onClick: function () { props.onSelect(t); },
      onKeyDown: function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); props.onSelect(t); }
      },
      role: "button",
      tabIndex: 0,
      title: t.traceID,
    },
      h("div", { className: "flex items-start gap-3 p-3" },
        h("div", { className: cn("shrink-0 pt-0.5", cat.color) },
          h(cat.Icon, { size: 16 })
        ),
        h("div", { className: "min-w-0 flex-1 space-y-1" },
          h("div", { className: "flex items-center gap-2 min-w-0" },
            h("span", { className: "truncate font-mono text-sm" }, t.rootTraceName || "—"),
            cat.label
              ? h(Badge, { variant: "secondary", className: "shrink-0 text-[10px]" }, cat.label)
              : null,
            isError
              ? h(Badge, { variant: "destructive", className: "shrink-0 text-[10px]" }, "error")
              : null
          ),
          chips.length
            ? h("div", { className: "flex flex-wrap items-center gap-1" }, chips.map(function (c) {
                return h(Badge, {
                  key: c.key,
                  variant: c.variant,
                  className: cn("text-[10px]", c.mono ? "font-mono" : null),
                }, c.label);
              }))
            : null,
          inputPreview || outputPreview
            ? h("div", { className: "space-y-0.5 border-l-2 border-border/60 pl-2 text-xs" },
                inputPreview
                  ? h("div", { className: "flex items-baseline gap-2", title: inputRaw || undefined },
                      h("span", { className: "w-6 shrink-0 text-[10px] font-medium text-muted-foreground" }, "in"),
                      h("span", { className: "truncate text-foreground/85" }, inputPreview)
                    )
                  : null,
                outputPreview
                  ? h("div", { className: "flex items-baseline gap-2", title: outputRaw || undefined },
                      h("span", { className: "w-6 shrink-0 text-[10px] font-medium text-muted-foreground" }, "out"),
                      h("span", { className: "truncate text-foreground/85" }, outputPreview)
                    )
                  : null
              )
            : null,
          h("div", { className: "flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground" }, metaBits),
          h("div", { className: "truncate font-mono text-[10px] text-muted-foreground/60" }, t.traceID)
        ),
        h("div", { className: "shrink-0 self-center text-muted-foreground opacity-30 transition-opacity group-hover:opacity-90" },
          h(IconChevronRight, { size: 16 })
        )
      )
    );
  }

  function TracesList(props) {
    var traces = props.traces || [];
    if (!traces.length) {
      return h("div", { className: "rounded border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground" },
        "No traces matched. Try widening the lookback window, or trigger a Hermes turn and refresh."
      );
    }
    return h("div", { className: "flex flex-col gap-2" },
      traces.map(function (t) {
        return h(TraceCard, { key: t.traceID, trace: t, onSelect: props.onSelect });
      })
    );
  }

  // ── trace detail ────────────────────────────────────────────────────

  function AttrTable(props) {
    var entries = Object.keys(props.attrs || {}).sort();
    if (!entries.length) return h("div", { className: "text-xs text-muted-foreground" }, "(no attributes)");
    return h("dl", {
      className: "hermes-otel-attr grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs",
    },
      entries.map(function (k) {
        var v = props.attrs[k];
        var rendered;
        if (v && typeof v === "object") {
          rendered = JSON.stringify(v, null, 2);
        } else {
          rendered = String(v);
        }
        return h(React.Fragment, { key: k },
          h("dt", { className: "text-muted-foreground" }, k),
          h("dd", { className: "whitespace-pre-wrap break-words text-foreground" }, rendered)
        );
      })
    );
  }

  function SpanRow(props) {
    var span = props.span;
    var depth = props.depth;
    var isOpen = props.open;
    var attrs = span._attrs || {};
    var status = statusColor(span);
    var kind = spanKindLabel(span);
    return h("li", { className: "border-b border-border last:border-b-0" },
      h("div", {
          className: "flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-accent/30",
          style: { paddingLeft: 12 + depth * 16 + "px" },
          onClick: props.onToggle,
        },
        h("span", { className: "w-3 text-xs text-muted-foreground" }, isOpen ? "▾" : "▸"),
        h("span", { className: "truncate font-mono text-sm" }, span.name),
        kind ? h(Badge, { variant: "secondary", className: "text-[10px]" }, kind) : null,
        status === "error" ? h(Badge, { variant: "destructive", className: "text-[10px]" }, "error") : null,
        h("span", { className: "ml-auto tabular-nums text-xs text-muted-foreground" }, fmtDurationMs(span.durationMs))
      ),
      isOpen ? h("div", { className: "bg-muted/20 px-4 py-3", style: { paddingLeft: 28 + depth * 16 + "px" } },
        h(AttrTable, { attrs: attrs })
      ) : null
    );
  }

  function TraceDetail(props) {
    var trace = props.trace;
    var detail = props.detail;
    var loading = props.loading;
    var error = props.error;
    var [openIds, setOpenIds] = useState({});

    var tree = useMemo(function () {
      if (!detail) return null;
      return buildSpanTree(detail.batches || (detail.trace && detail.trace.batches));
    }, [detail]);

    var flat = useMemo(function () { return tree ? flatten(tree.roots) : []; }, [tree]);

    function toggle(id) {
      setOpenIds(function (prev) {
        var next = Object.assign({}, prev);
        if (next[id]) delete next[id]; else next[id] = true;
        return next;
      });
    }
    function expandAll() {
      var next = {};
      flat.forEach(function (n) { next[n.span.spanId] = true; });
      setOpenIds(next);
    }
    function collapseAll() { setOpenIds({}); }

    var rootService = trace.rootServiceName || "—";
    var rootName = trace.rootTraceName || "—";

    return h(Card, null,
      h(CardHeader, { className: "flex flex-row items-start justify-between gap-3 space-y-0" },
        h("div", { className: "min-w-0 space-y-1" },
          h(CardTitle, { className: "truncate" }, rootName),
          h("div", { className: "flex flex-wrap items-center gap-2 text-xs text-muted-foreground" },
            h("span", null, rootService),
            h("span", null, "·"),
            h("span", { className: "font-mono" }, trace.traceID),
            h("span", null, "·"),
            h("span", null, fmtDurationMs(trace.durationMs))
          )
        ),
        h("div", { className: "flex shrink-0 gap-2" },
          h(Button, { variant: "outline", size: "sm", onClick: expandAll, disabled: !flat.length }, "Expand all"),
          h(Button, { variant: "outline", size: "sm", onClick: collapseAll, disabled: !flat.length }, "Collapse"),
          h(Button, { variant: "ghost", size: "sm", onClick: props.onBack }, "← Back")
        )
      ),
      h(CardContent, null,
        loading ? h("div", { className: "py-8 text-center text-sm text-muted-foreground" }, "Loading trace…") : null,
        error ? h(ErrorBanner, { error: error }) : null,
        !loading && !error && flat.length === 0
          ? h("div", { className: "py-8 text-center text-sm text-muted-foreground" }, "No spans returned for this trace.")
          : null,
        !loading && !error && flat.length
          ? h("ul", { className: "overflow-hidden rounded-md border border-border" },
              flat.map(function (n) {
                return h(SpanRow, {
                  key: n.span.spanId,
                  span: n.span,
                  depth: n.depth,
                  open: !!openIds[n.span.spanId],
                  onToggle: function () { toggle(n.span.spanId); },
                });
              })
            )
          : null
      )
    );
  }

  // ── main page ───────────────────────────────────────────────────────

  function OtelTracesPage() {
    var [status, setStatus] = useState(null);
    var [statusErr, setStatusErr] = useState(null);
    var [filters, setFilters] = useState({
      lookback: 1,
      q: "",
      minDuration: "",
      service: "",
      rootsOnly: true,
    });
    var [traces, setTraces] = useState([]);
    var [searchedOnce, setSearchedOnce] = useState(false);
    var [loading, setLoading] = useState(false);
    var [error, setError] = useState(null);
    var [selected, setSelected] = useState(null);
    var [detail, setDetail] = useState(null);
    var [detailLoading, setDetailLoading] = useState(false);
    var [detailError, setDetailError] = useState(null);
    var [searchCollapsed, setSearchCollapsed] = useState(false);
    var [page, setPage] = useState(0);
    var PAGE_SIZE = 10;

    var loadStatus = useCallback(function () {
      setStatusErr(null);
      fetchJSON(API + "/status")
        .then(function (s) { setStatus(s); })
        .catch(function (e) { setStatusErr(e && e.message ? e.message : String(e)); });
    }, []);

    useEffect(function () { loadStatus(); }, [loadStatus]);

    var search = useCallback(function () {
      if (!status || !status.configured) return;
      setLoading(true);
      setError(null);
      var params = new URLSearchParams();
      params.set("limit", "50");
      params.set("lookback_hours", String(filters.lookback));
      if (filters.q && filters.q.trim()) params.set("q", filters.q.trim());
      if (filters.service && filters.service.trim()) params.set("service", filters.service.trim());
      if (filters.minDuration && Number(filters.minDuration) > 0) {
        params.set("min_duration_ms", String(Math.floor(Number(filters.minDuration))));
      }
      params.set("roots_only", filters.rootsOnly ? "true" : "false");
      fetchJSON(API + "/traces/search?" + params.toString())
        .then(function (r) {
          setTraces((r && r.traces) || []);
          setSearchedOnce(true);
          setPage(0);
        })
        .catch(function (e) { setError(e && e.message ? e.message : String(e)); })
        .finally(function () { setLoading(false); });
    }, [status, filters]);

    var openTrace = useCallback(function (t) {
      setSelected(t);
      setDetail(null);
      setDetailError(null);
      setDetailLoading(true);
      fetchJSON(API + "/traces/" + encodeURIComponent(t.traceID))
        .then(function (d) { setDetail(d); })
        .catch(function (e) { setDetailError(e && e.message ? e.message : String(e)); })
        .finally(function () { setDetailLoading(false); });
    }, []);

    var back = useCallback(function () {
      setSelected(null);
      setDetail(null);
      setDetailError(null);
    }, []);

    var pagedTraces = useMemo(function () {
      var start = page * PAGE_SIZE;
      return traces.slice(start, start + PAGE_SIZE);
    }, [traces, page]);
    var totalPages = Math.max(1, Math.ceil(traces.length / PAGE_SIZE));

    // Auto-search the moment status comes back configured.
    useEffect(function () {
      if (status && status.configured && !searchedOnce && !loading) {
        search();
      }
    }, [status, searchedOnce, loading, search]);

    if (!status && !statusErr) {
      return h(Card, null, h(CardContent, { className: "py-8 text-center text-sm text-muted-foreground" }, "Loading…"));
    }
    if (statusErr) {
      return h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "OTel Traces")),
        h(CardContent, null, h(ErrorBanner, { error: "Failed to load status: " + statusErr }))
      );
    }
    if (!status.configured) {
      return h("div", { className: "hermes-otel-tab space-y-4" },
        h(Card, null, h(CardContent, { className: "pt-6" }, h(StatusBar, { status: status, onRefresh: loadStatus }))),
        h(EmptyState, { status: status })
      );
    }
    if (selected) {
      return h("div", { className: "hermes-otel-tab space-y-4" },
        h(TraceDetail, {
          trace: selected,
          detail: detail,
          loading: detailLoading,
          error: detailError,
          onBack: back,
        })
      );
    }

    // Pagination footer — matches SessionsPage layout: range + total
    // on the left, chevron controls on the right. The range shows even
    // for single-page results so the total is always visible.
    var total = traces.length;
    var rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
    var rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);
    var paginationEl = total > 0
      ? h("div", { className: "flex items-center justify-between pt-2" },
          h("span", { className: "text-xs text-muted-foreground tabular-nums" },
            total > PAGE_SIZE
              ? rangeStart + "–" + rangeEnd + " of " + total
              : total + (total === 1 ? " trace" : " traces")
          ),
          total > PAGE_SIZE
            ? h("div", { className: "flex items-center gap-1" },
                h(Button, {
                  variant: "outline",
                  size: "sm",
                  className: "h-7 w-7 p-0",
                  disabled: page <= 0,
                  onClick: function () { setPage(function (p) { return Math.max(0, p - 1); }); },
                  "aria-label": "Previous page",
                },
                  h(IconChevronLeft, { size: 16 })
                ),
                h("span", { className: "text-xs text-muted-foreground px-2 tabular-nums" },
                  "Page " + (page + 1) + " of " + totalPages
                ),
                h(Button, {
                  variant: "outline",
                  size: "sm",
                  className: "h-7 w-7 p-0",
                  disabled: (page + 1) * PAGE_SIZE >= total,
                  onClick: function () { setPage(function (p) { return p + 1; }); },
                  "aria-label": "Next page",
                },
                  h(IconChevronRight, { size: 16 })
                )
              )
            : null
        )
      : null;

    return h("div", { className: "hermes-otel-tab space-y-4" },
      h(Card, null, h(CardContent, { className: "pt-6" }, h(StatusBar, { status: status, onRefresh: loadStatus }))),
      // Filters card — only the filter inputs collapse. The Search button
      // and the results card below stay visible in both states.
      h(Card, null,
        h(CardHeader, {
            className: "cursor-pointer select-none",
            onClick: function () { setSearchCollapsed(function (c) { return !c; }); },
            role: "button",
            tabIndex: 0,
            "aria-expanded": !searchCollapsed,
          },
          h(CardTitle, { className: "flex items-center gap-2" },
            h("span", { className: "w-3 text-xs text-muted-foreground" }, searchCollapsed ? "▸" : "▾"),
            h("span", null, "Search filters"),
            filters.q || filters.service || (filters.minDuration && Number(filters.minDuration) > 0) || filters.rootsOnly === false
              ? h("span", { className: "ml-2 text-xs font-normal text-muted-foreground" }, "· active")
              : null
          )
        ),
        searchCollapsed ? null : h(CardContent, null,
          h(FiltersForm, { filters: filters, status: status, onChange: setFilters, onSubmit: search, loading: loading })
        )
      ),
      // Results card — always visible. Search button lives here so it is
      // usable whether or not the filters above are expanded.
      h(Card, null,
        h(CardHeader, { className: "flex flex-row items-center justify-between gap-2 space-y-0" },
          h(CardTitle, { className: "text-base" },
            "Results",
            traces.length
              ? h("span", { className: "ml-2 text-xs font-normal text-muted-foreground" },
                  "· " + traces.length + " trace" + (traces.length === 1 ? "" : "s"))
              : null
          ),
          h(Button, {
            type: "button",
            size: "sm",
            onClick: search,
            disabled: loading,
          }, loading ? "Searching…" : "Search")
        ),
        h(CardContent, { className: "space-y-3" },
          error ? h(ErrorBanner, { error: error }) : null,
          h(TracesList, { traces: pagedTraces, onSelect: openTrace }),
          paginationEl
        )
      )
    );
  }

  window.__HERMES_PLUGINS__.register("hermes_otel", OtelTracesPage);
})();
