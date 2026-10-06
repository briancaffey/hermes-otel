// URL state and cross-tab navigation (#185, #287). The host routes the plugin
// at /otel; the tab keeps its own state in the query string so a refresh, the
// back button and a pasted link land on the same view:
//   ?tab=traces&source=live&view=turns&trace=<id>&status=error&kind=tool&lookback=24
//   ?tab=logs&source=signoz&level=30&logger=hermes_otel&session=<id>&trace=<id>
//        &text=…&lookback=24&size=200&before=<unix ns>
//   ?tab=metrics&range=24&inst=hermes.token.usage&group=model&agg=sum
// Keys are grouped per tab: switching tabs clears the other tabs' keys, so
// `text` can mean "trace text" on one tab and "log text" on another without
// colliding. `tab`, `source`, `trace` and `session` are shared: a trace open in
// the Traces tab is the trace the Logs tab pre-filters on when a link asks
// for it (`navigate`), and the host's `profile` is left alone.

export type NavState = {
  tab?: string;
  source?: string;
  trace?: string;
  session?: string;
  view?: string;
  // Traces tab search bar (#281)
  status?: string;
  kind?: string;
  tool?: string;
  model?: string;
  mindur?: string;
  text?: string;
  q?: string;
  service?: string;
  roots?: string; // "0" = roots only off
  lookback?: string;
  before?: string; // keyset cursor, unix ns (traces and logs)
  // Logs tab filters and paging (#186)
  level?: string;
  logger?: string;
  events?: string; // "1" = events only (logs.events)
  event?: string; // one event name
  center?: string; // context window centre, unix ns
  win?: string; // context window half-width, seconds
  size?: string;
  // Metrics tab (#284)
  range?: string;
  inst?: string;
  group?: string;
  agg?: string;
};

export const SHARED_KEYS = ["tab", "source", "trace", "session"] as const;
export const TAB_KEYS: Record<string, readonly (keyof NavState)[]> = {
  live: [],
  traces: ["view", "status", "kind", "tool", "model", "mindur", "text", "q", "service", "roots", "lookback", "before"],
  logs: ["level", "logger", "text", "lookback", "events", "event", "center", "win", "size", "before"],
  metrics: ["range", "inst", "group", "agg"],
  settings: [],
};
export const NAV_KEYS = Array.from(new Set<keyof NavState>([...SHARED_KEYS, ...Object.values(TAB_KEYS).flat()]));

export const NAV_EVENT = "hermes_otel:navigate";

export function readNav(search?: string): NavState {
  const q = new URLSearchParams(search ?? (typeof window !== "undefined" ? window.location.search : ""));
  const out: NavState = {};
  for (const k of NAV_KEYS) {
    const v = q.get(k);
    if (v) out[k] = v;
  }
  return out;
}

export function navSearch(state: NavState, base?: string): string {
  const q = new URLSearchParams(base ?? (typeof window !== "undefined" ? window.location.search : ""));
  // Keys absent from `state` are left as they are; an empty string clears one.
  for (const k of NAV_KEYS) {
    if (!(k in state)) continue;
    const v = state[k];
    if (v) q.set(k, v);
    else q.delete(k);
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** The patch that clears every key owned by tabs other than `tab`. */
export function clearOtherTabs(tab: string): NavState {
  const out: NavState = {};
  for (const [t, keys] of Object.entries(TAB_KEYS)) {
    if (t === tab) continue;
    for (const k of keys) if (!(TAB_KEYS[tab] || []).includes(k)) (out as any)[k] = "";
  }
  return out;
}

/** Merge `patch` into the URL without a navigation (replaceState). */
export function writeNav(patch: NavState): void {
  if (typeof window === "undefined" || !window.history?.replaceState) return;
  try {
    const url = `${window.location.pathname}${navSearch(patch)}${window.location.hash}`;
    window.history.replaceState(window.history.state, "", url);
  } catch {
    /* ignore */
  }
}

/** Ask the tab to show something (e.g. a trace from the Logs tab). */
export function navigate(state: NavState): void {
  const patch = state.tab ? { ...clearOtherTabs(state.tab), ...state } : state;
  writeNav(patch);
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(NAV_EVENT, { detail: patch }));
}
