// Settings tab: every plugin setting with its effective value, where it came
// from (env var, config file, default) and a one-line description; the config
// file raw and as an effective YAML; and the environment variables the plugin
// honours. Read-only: the file is edited outside the dashboard.
import { React, useState, useEffect, useCallback, useMemo, fetchJSON, API, Button, Input, Badge, cn } from "./sdk";
import { fmtAbsTime, fmtTimeAgo } from "./lib";
import { MiniLabel, ErrorBanner, Stat } from "./atoms";
import { IconRefresh, IconCopy, IconCheck, IconExternal } from "./icons";
import type { SourceStatus } from "./source";
import {
  SettingsReport,
  SettingField,
  BackendSummary,
  QueryCapability,
  SIGNALS,
  signalPill,
  queryCapabilityLine,
  showTypeBadge,
  DOCS_BASE,
  renderDescription,
  filterFields,
  groupFields,
  groupEnv,
  envCounts,
  pathSourceLabel,
  sourceNote,
} from "./settings-lib";

/* eslint-disable @typescript-eslint/no-explicit-any */
type View = "structured" | "raw" | "env";
const VIEWS: { id: View; label: string }[] = [
  { id: "structured", label: "Structured" },
  { id: "raw", label: "Raw YAML" },
  { id: "env", label: "Environment" },
];

function Description({ text }: { text: string }) {
  return (
    <span>
      {renderDescription(text).map((tok, i) =>
        tok.t === "code" ? (
          <code key={i} className="otel-code">{tok.v}</code>
        ) : tok.t === "link" ? (
          <a key={i} className="otel-link" href={tok.href} target="_blank" rel="noreferrer">{tok.v}</a>
        ) : (
          <span key={i}>{tok.v}</span>
        )
      )}
    </span>
  );
}

function SourceBadge({ source }: { source: SettingField["source"] }) {
  const title =
    source === "env" ? "set by a HERMES_OTEL_* environment variable" : source === "file" ? "set in the config file" : "the built-in default";
  return (
    <span className={cn("otel-src", `otel-src-${source}`)} title={title}>
      {source}
    </span>
  );
}

function Value({ f }: { f: SettingField }) {
  const v = f.value;
  if (v == null) return <span className="otel-unset">unset</span>;
  if (f.kind === "bool")
    return <span className={cn("otel-pill", v ? "otel-pill-on" : "otel-pill-off")}>{v ? "on" : "off"}</span>;
  if (f.kind === "map")
    return (
      <div className="otel-kv">
        {Object.entries(v as Record<string, any>).map(([k, x]) => (
          <div key={k} className="font-mono text-xs">
            <span className="text-muted-foreground">{k}:</span> {String(x)}
          </div>
        ))}
      </div>
    );
  if (f.kind === "backends") {
    const n = Array.isArray(v) ? v.length : 0;
    return <span className="font-mono text-xs">{n} configured</span>;
  }
  return <span className="font-mono text-xs break-all">{String(v)}</span>;
}

function DefaultHint({ f }: { f: SettingField }) {
  if (!f.changed || f.kind === "backends" || f.kind === "map") return null;
  const d = f.default;
  const text = d == null ? "unset" : f.kind === "bool" ? (d ? "on" : "off") : String(d);
  return <span className="text-[11px] text-muted-foreground">default {text}</span>;
}

function FieldRow({ f }: { f: SettingField }) {
  const note = sourceNote(f);
  const warn = f.env_invalid || f.file_invalid;
  return (
    <div className={cn("otel-settings-row", f.changed && "otel-changed")}>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-foreground">{f.key}</span>
          {f.env_var ? (
            <span className="font-mono text-[10px] text-muted-foreground/70" title="environment variable that overrides this setting">
              {f.env_var}
            </span>
          ) : (
            <span className="text-[10px] text-muted-foreground/70">yaml only</span>
          )}
        </div>
        <div className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
          <Description text={f.description} />
        </div>
      </div>
      <div className="min-w-0">
        <Value f={f} />
        <div className="mt-0.5 flex flex-wrap items-center gap-2">
          <DefaultHint f={f} />
          {note ? <span className={cn("text-[11px]", warn ? "otel-warn" : "text-muted-foreground")}>{note}</span> : null}
        </div>
      </div>
      <div className="otel-self-center">
        <SourceBadge source={f.source} />
      </div>
    </div>
  );
}

function Row({ k, children, title }: { k: string; children: any; title?: string }) {
  return (
    <>
      <span className="text-muted-foreground" title={title}>
        {k}
      </span>
      <span className="min-w-0 break-all">{children}</span>
    </>
  );
}

const stop = (e: any) => e.stopPropagation();

/** One configured backend. The whole card opens the backend's UI in a new
 *  window when the report could name it (an explicit `ui_url`, or a derivation
 *  it explains in the tooltip); inner links stop the click from bubbling. */
function BackendCard({ b, q }: { b: BackendSummary; q?: QueryCapability }) {
  const href = b.ui.url;
  const open = () => {
    if (href) window.open(href, "_blank", "noopener,noreferrer");
  };
  const query = queryCapabilityLine(q, b.display_type);
  const metricsOn = b.signals.metrics?.exported;
  return (
    <div
      className={cn("otel-card-bg border border-border px-3 py-2.5", href ? "otel-backend-card" : "")}
      onClick={href ? open : undefined}
      onKeyDown={href ? (e: any) => (e.key === "Enter" ? open() : undefined) : undefined}
      role={href ? "link" : undefined}
      tabIndex={href ? 0 : undefined}
      title={href ? `${b.ui.note} · opens ${href} in a new window` : b.ui.note}
    >
      <div className="flex flex-wrap items-center gap-2">
        {href ? (
          <a className="otel-backend-name" href={href} target="_blank" rel="noreferrer noopener" onClick={stop}>
            {b.name}
            <IconExternal size={12} className="otel-backend-ext" />
          </a>
        ) : (
          <span className="text-sm font-medium">{b.name}</span>
        )}
        {showTypeBadge(b) ? <Badge variant="secondary" className="text-[10px] uppercase">{b.display_type}</Badge> : null}
        {b.docs_path ? (
          <a
            className="otel-link text-[11px] text-muted-foreground"
            href={DOCS_BASE + b.docs_path}
            target="_blank"
            rel="noreferrer"
            onClick={stop}
            title={`${b.display_type} backend docs`}
          >
            docs
          </a>
        ) : null}
        <span className="ml-auto flex flex-wrap gap-1">
          {SIGNALS.map((sig) => {
            const st = b.signals[sig];
            if (!st) return null;
            const pill = signalPill(sig, st, b.display_type);
            return (
              <span key={sig} className={cn("otel-pill", `otel-pill-${pill.cls}`)} title={pill.title}>
                {pill.label}
              </span>
            );
          })}
        </span>
      </div>
      {query ? (
        <div className="mt-1 text-[11px] text-muted-foreground" title={query.title}>
          {query.text}
        </div>
      ) : null}
      <div className="otel-attr-table mt-2 text-xs">
        {Object.entries(b.fields).map(([k, v]) => (
          <Row key={k} k={k}>
            <span className="font-mono">{String(v)}</span>
          </Row>
        ))}
        <Row k="ui" title="the link the card opens; set ui_url on the entry to override">
          {href ? (
            <>
              <a className="otel-link font-mono" href={href} target="_blank" rel="noreferrer noopener" onClick={stop}>
                {href}
              </a>
              <span className="text-muted-foreground"> · {b.ui.source === "file" ? "ui_url" : "derived"}</span>
            </>
          ) : (
            <span className="otel-unset">{b.ui.note}</span>
          )}
        </Row>
        {metricsOn ? (
          <Row k="temporality" title="aggregation temporality of this backend's metric reader">
            <span className="font-mono">{b.metrics_temporality.value}</span>
            <span className="text-muted-foreground"> · {b.metrics_temporality.source}</span>
          </Row>
        ) : null}
        {Object.entries(b.query_fields || {}).map(([k, v]) => (
          <Row key={`q-${k}`} k={k} title="read by the dashboard's query adapter, not by the exporter">
            <span className="font-mono">{String(v)}</span>
            <span className="text-muted-foreground"> · query</span>
          </Row>
        ))}
        {b.headers
          ? Object.entries(b.headers).map(([k, v]) => (
              <Row key={`h-${k}`} k={`header ${k}`}>
                <span className="font-mono">{String(v)}</span>
              </Row>
            ))
          : null}
        {b.credentials.map((c) => (
          <Row key={c.field} k={c.field}>
            {c.set ? (
              <>
                <span className="font-mono">{c.value ?? "set"}</span>
                <span className="text-muted-foreground"> · {c.source}</span>
              </>
            ) : (
              <span className="otel-warn">{c.source || "not set"}</span>
            )}
          </Row>
        ))}
      </div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <Button variant="outline" size="sm" onClick={copy} title="copy to clipboard">
      {done ? <IconCheck size={13} /> : <IconCopy size={13} />}
      <span className="ml-1">{done ? "copied" : "copy"}</span>
    </Button>
  );
}

function ConfigFileLine({ r }: { r: SettingsReport }) {
  const c = r.config;
  return (
    <div className="otel-card-bg border border-border px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <MiniLabel>config file</MiniLabel>
        {c.path ? <span className="font-mono break-all">{c.path}</span> : <span className="text-muted-foreground">none found</span>}
        <span className="otel-pill" title="how the file was chosen: HERMES_OTEL_CONFIG, then $HERMES_HOME/hermes_otel.yaml, then the plugin directory">{pathSourceLabel(c.path_source)}</span>
        {c.exists && c.mtime ? (
          <span className="text-muted-foreground" title={fmtAbsTime(c.mtime * 1e9)}>
            edited {fmtTimeAgo(c.mtime * 1e9)}
          </span>
        ) : null}
        {c.path && !c.exists ? <span className="otel-warn">file does not exist; defaults and environment variables apply</span> : null}
        {c.exists && !c.parse_ok ? <span className="otel-warn">file could not be parsed; defaults and environment variables apply</span> : null}
      </div>
      {!c.path ? (
        <div className="mt-1 text-muted-foreground">
          Create <span className="font-mono">{c.durable_path}</span> to change settings; see the{" "}
          <a className="otel-link" href={`${DOCS_BASE}/configuration/overview`} target="_blank" rel="noreferrer">configuration guide</a>.
        </div>
      ) : null}
      {c.unknown_keys.length ? (
        <div className="mt-1 text-muted-foreground">
          Also in the file:{" "}
          {c.unknown_keys.map((u, i) => (
            <span key={u.key}>
              {i ? ", " : ""}
              <span className="font-mono">{u.key}</span>
              {u.note ? (
                <>
                  {" "}
                  (<Description text={u.note} />)
                </>
              ) : (
                <span className="otel-warn"> (not a known setting)</span>
              )}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function SettingsPage() {
  const [report, setReport] = useState<SettingsReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<View>("structured");
  const [reveal, setReveal] = useState(false);
  const [query, setQuery] = useState("");
  const [changedOnly, setChangedOnly] = useState(false);
  const [rawMode, setRawMode] = useState<"file" | "effective">("file");
  const [showUnset, setShowUnset] = useState(false);
  // What the dashboard can query from each backend (adapter present, metrics,
  // logs) comes from /status, the same view the source selector uses.
  const [status, setStatus] = useState<SourceStatus | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    fetchJSON(`${API}/status`)
      .then((st: SourceStatus) => setStatus(st))
      .catch(() => setStatus(null));
    try {
      const r = await fetchJSON(`${API}/settings?reveal=${reveal ? "true" : "false"}`);
      setReport(r);
      setError(null);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, [reveal]);

  useEffect(() => {
    load();
  }, [load]);

  const fields = report?.fields || [];
  const shown = useMemo(() => filterFields(fields, { query, changedOnly }), [fields, query, changedOnly]);
  const groups = useMemo(() => groupFields(shown, report?.groups || []), [shown, report]);
  const backends: BackendSummary[] = (fields.find((f) => f.key === "backends")?.value as BackendSummary[]) || [];
  const invalidEnv = useMemo(() => {
    const out: Record<string, string> = {};
    for (const f of fields) if (f.env_invalid && f.env_var) out[f.env_var] = `not a valid ${f.kind}; ignored`;
    return out;
  }, [fields]);
  const cap = report?.capture_summary;
  const queryCaps = useMemo(() => {
    const out: Record<string, QueryCapability> = {};
    for (const a of status?.available || []) out[a.name] = { supported: a.supported, metrics: a.metrics, logs: a.logs };
    return out;
  }, [status]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-semibold tracking-tight">Settings</span>
        {report ? (
          <span className="text-xs text-muted-foreground">
            hermes-otel {report.process.plugin_version || "?"} · resolved {fmtTimeAgo(report.resolved_at * 1e9)}
          </span>
        ) : null}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 border border-border p-0.5">
            {VIEWS.map((v) => (
              <button
                key={v.id}
                type="button"
                onClick={() => setView(v.id)}
                className={cn(
                  "otel-toggle px-3 py-1 text-xs font-medium transition-colors",
                  view === v.id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground"
                )}
              >
                {v.label}
              </button>
            ))}
          </div>
          <label className="inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground" title="credential values are masked unless this is on">
            <input type="checkbox" checked={reveal} onChange={(e: any) => setReveal(e.target.checked)} />
            show secrets
          </label>
          <Button variant="outline" size="sm" onClick={load} disabled={loading} title="re-read the file and environment">
            <IconRefresh size={13} className={loading ? "otel-spin" : ""} />
            <span className="ml-1">reload</span>
          </Button>
        </div>
      </div>

      {error ? <ErrorBanner error={error} /> : null}
      {!report && !error ? <div className="text-sm text-muted-foreground">Loading settings…</div> : null}

      {report ? (
        <>
          <ConfigFileLine r={report} />

          <div className="otel-kpi-grid">
            <Stat label="Settings" value={report.fields.length} sub={`${report.counts.changed} changed from default`} />
            <Stat label="From file" value={report.counts.file} sub={report.config.exists ? "in the config file" : "no file"} />
            <Stat label="From env" value={report.counts.env} sub="HERMES_OTEL_* variables" />
            <Stat label="Backends" value={backends.length} sub={backends.map((b) => b.name).join(", ") || "live store only"} />
            <Stat
              label="Content"
              value={cap ? cap.mode : "?"}
              sub={cap ? cap.detail : ""}
              accent={cap?.mode === "off" ? undefined : cap?.mode === "full" ? "cost" : undefined}
            />
          </div>

          {view === "structured" ? (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <Input
                  value={query}
                  onChange={(e: any) => setQuery(e.target.value)}
                  placeholder="filter by name, value, description…"
                  className="otel-w-56 h-8 text-xs"
                />
                <label className="inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
                  <input type="checkbox" checked={changedOnly} onChange={(e: any) => setChangedOnly(e.target.checked)} />
                  changed from default only
                </label>
                <span className="ml-auto text-[11px] text-muted-foreground">
                  {shown.length} of {fields.length} · precedence: <span className="otel-src otel-src-env">env</span> over{" "}
                  <span className="otel-src otel-src-file">file</span> over <span className="otel-src otel-src-default">default</span>
                </span>
              </div>

              {groups.length === 0 ? (
                <div className="border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">No setting matches.</div>
              ) : null}

              {groups.map((g) => (
                <div key={g.group} className="space-y-1">
                  <div className="flex items-center gap-2 pt-1">
                    <MiniLabel>{g.group}</MiniLabel>
                    <span className="text-[11px] text-muted-foreground/70">{g.fields.length}</span>
                  </div>
                  {g.group === "Backends" ? (
                    <div className="space-y-2">
                      {g.fields.map((f) => (
                        <FieldRow key={f.key} f={f} />
                      ))}
                      {backends.length ? (
                        <div className="otel-backend-grid">
                          {backends.map((b, i) => (
                            <BackendCard key={`${b.name}-${i}`} b={b} q={queryCaps[b.name]} />
                          ))}
                        </div>
                      ) : (
                        <div className="text-xs text-muted-foreground">
                          No <span className="font-mono">backends:</span> entry. Telemetry stays in the live store on this machine; single-backend environment variables, if any, are listed under Environment.
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="otel-settings-list">
                      {g.fields.map((f) => (
                        <FieldRow key={f.key} f={f} />
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </>
          ) : null}

          {view === "raw" ? (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <div className="flex items-center gap-1 border border-border p-0.5">
                  {(["file", "effective"] as const).map((id) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setRawMode(id)}
                      className={cn(
                        "otel-toggle px-3 py-1 text-xs font-medium transition-colors",
                        rawMode === id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      {id === "file" ? "File as written" : "Effective config"}
                    </button>
                  ))}
                </div>
                <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground" title={rawMode === "file" ? report.config.path || "" : ""}>
                  {rawMode === "file"
                    ? report.config.exists
                      ? `${report.config.path}${reveal ? "" : " · secrets masked"}`
                      : "no config file to show"
                    : "every setting after env, file and defaults are applied; each key notes its source"}
                </span>
                <span className="shrink-0">
                  <CopyButton text={rawMode === "file" ? report.config.raw || "" : report.effective_yaml} />
                </span>
              </div>
              {rawMode === "file" ? (
                report.config.raw != null ? (
                  <pre className="otel-pre otel-raw">{report.config.raw}</pre>
                ) : (
                  <div className="border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                    <div className="mb-1 text-base font-medium text-foreground">No config file</div>
                    {report.config.raw_error ? (
                      report.config.raw_error
                    ) : (
                      <>
                        Create <span className="font-mono">{report.config.durable_path}</span>. The Effective config view is a starting point you can paste in.
                      </>
                    )}
                  </div>
                )
              ) : (
                <pre className="otel-pre otel-raw">{report.effective_yaml}</pre>
              )}
            </div>
          ) : null}

          {view === "env" ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-[11px] text-muted-foreground">
                  {envCounts(report.env).set} set of {envCounts(report.env).known} the plugin reads, as seen by the dashboard process
                </span>
                <label className="ml-auto inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
                  <input type="checkbox" checked={showUnset} onChange={(e: any) => setShowUnset(e.target.checked)} />
                  show unset variables
                </label>
              </div>
              {groupEnv(report.env, showUnset).length === 0 ? (
                <div className="border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                  <div className="mb-1 text-base font-medium text-foreground">No plugin environment variables set</div>
                  Every setting comes from the file or its default. Tick "show unset variables" to see every variable the plugin would read.
                </div>
              ) : null}
              {groupEnv(report.env, showUnset).map((g) => (
                <div key={g.group} className="space-y-1">
                  <MiniLabel>{g.label}</MiniLabel>
                  <div className="otel-settings-list">
                    {g.entries.map((e) => (
                      <div key={e.name} className={cn("otel-env-row", e.set && "otel-changed")}>
                        <div className="min-w-0">
                          <div className="font-mono text-xs break-all">{e.name}</div>
                          {e.maps_to ? (
                            <div className="text-[10px] text-muted-foreground/70">
                              sets <span className="font-mono">{e.maps_to}</span>
                            </div>
                          ) : null}
                        </div>
                        <div className="min-w-0">
                          {e.set ? <span className="font-mono text-xs break-all">{e.value}</span> : <span className="otel-unset">unset</span>}
                          {invalidEnv[e.name] ? <div className="otel-warn text-[11px]">{invalidEnv[e.name]}</div> : null}
                        </div>
                        <div className="text-[11px] leading-snug text-muted-foreground">
                          <Description text={e.description} />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : null}

          <div className="text-[11px] text-muted-foreground">{report.process.note}</div>
        </>
      ) : null}
    </div>
  );
}
