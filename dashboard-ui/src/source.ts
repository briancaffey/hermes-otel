// The data source the tab reads from: the in-process live store or one of the
// configured backends, chosen per request through the API's `backend=`
// parameter (#177). One provider at the root holds the choice and the
// server's view of what is available (#287); pages read it through
// useSource(). Remembered per browser in localStorage; a pasted link's
// `source` wins.
import { React, useState, useEffect, useCallback, useContext, createContext, api } from "./sdk";

import { LIVE, withBackend, FilterSupport } from "./params";
import { readNav, writeNav } from "./nav";
export { LIVE, withBackend };
const KEY = "hermes_otel.source";

export type BackendInfo = {
  name: string;
  type: string;
  endpoint?: string;
  supported: boolean;
  metrics: boolean;
  logs: boolean;
  /** contract §1: which search fields the adapter honours */
  filters?: FilterSupport;
};

export type SourceStatus = {
  configured: boolean;
  active: string | null;
  available: BackendInfo[];
  reason?: string;
  queryable_types?: string[];
  query_backend_pin?: string | null;
  filters?: FilterSupport;
  [k: string]: any;
};

export type Need = "traces" | "metrics" | "logs";

export function backendUsable(b: { supported: boolean; metrics: boolean; logs: boolean }, need: Need): boolean {
  if (!b.supported) return false;
  if (need === "metrics") return b.metrics;
  if (need === "logs") return b.logs;
  return true;
}

/** The source to start from: the URL's `source` (a pasted link wins), else the remembered one. */
export function readSource(search?: string): string {
  const fromUrl = readNav(search).source;
  if (fromUrl) return fromUrl;
  try {
    return localStorage.getItem(KEY) || LIVE;
  } catch {
    return LIVE;
  }
}

export function writeSource(v: string): void {
  try {
    localStorage.setItem(KEY, v);
  } catch {
    /* private mode etc. */
  }
  // Keep the URL in step so "copy link" names the source it was looking at (#268).
  writeNav({ source: v === LIVE ? "" : v });
}

export type SourceContextValue = {
  source: string;
  setSource: (s: string) => void;
  status: SourceStatus | null;
  /** The status payload for the selected backend (filters, query_url, …); the same object for live. */
  refresh: () => void;
  isLive: boolean;
  /** Filter support of the selected source (live: everything server-side). */
  filters: FilterSupport | null;
};

const LIVE_FILTERS: FilterSupport = {
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
  lookback: "server",
};

const SourceContext: any = createContext ? createContext<SourceContextValue | null>(null) : null;

/** Owns the choice and the `/status` fetch for the whole tab. */
export function useSourceState(enabled = true): SourceContextValue {
  const [source, setSourceState] = useState<string>(readSource());
  const [status, setStatus] = useState<SourceStatus | null>(null);

  const refresh = useCallback(() => {
    const p = withBackend(new URLSearchParams(), source);
    api("/status", p)
      .then((st: SourceStatus) => {
        setStatus(st);
        // A remembered backend that is no longer configured falls back to live.
        if (source !== LIVE && !(st.available || []).some((b) => b.name === source)) {
          setSourceState(LIVE);
          writeSource(LIVE);
        }
      })
      .catch(() => {
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

  const setSource = useCallback((s: string) => {
    writeSource(s);
    setSourceState(s);
  }, []);

  const isLive = source === LIVE;
  const entry = status?.available?.find((b) => b.name === source) || null;
  const filters = isLive ? LIVE_FILTERS : entry?.filters || status?.filters || null;
  return { source, setSource, status, refresh, isLive, filters };
}

export function SourceProvider({ children }: { children: any }) {
  const value = useSourceState();
  if (!SourceContext) return children;
  return React.createElement(SourceContext.Provider, { value }, children);
}

/** Shared source state from the provider, or a private one when rendered alone (tests). */
export function useSource(): SourceContextValue {
  const ctx = SourceContext && useContext ? (useContext(SourceContext) as SourceContextValue | null) : null;
  // Hooks must run unconditionally: the private state exists either way but
  // only fetches when no provider is above.
  const own = useSourceState(!ctx);
  return ctx || own;
}
