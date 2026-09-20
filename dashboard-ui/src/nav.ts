// URL state and cross-tab navigation (#185). The host routes the plugin at
// /otel; the tab keeps its own state in the query string so a refresh, the
// back button and a pasted link land on the same view.
//   ?tab=traces&source=live&view=turns&trace=<id>
// Pure functions read/write window.location; the event lets the Logs tab open
// a trace without the pages knowing about each other.

export type NavState = {
  tab?: string;
  source?: string;
  view?: string;
  trace?: string;
  session?: string;
};

export const NAV_EVENT = "hermes_otel:navigate";

export function readNav(search?: string): NavState {
  const q = new URLSearchParams(search ?? (typeof window !== "undefined" ? window.location.search : ""));
  const out: NavState = {};
  for (const k of ["tab", "source", "view", "trace", "session"] as const) {
    const v = q.get(k);
    if (v) out[k] = v;
  }
  return out;
}

export function navSearch(state: NavState, base?: string): string {
  const q = new URLSearchParams(base ?? (typeof window !== "undefined" ? window.location.search : ""));
  // Keys absent from `state` are left as they are; an empty string clears one.
  for (const k of ["tab", "source", "view", "trace", "session"] as const) {
    if (!(k in state)) continue;
    const v = state[k];
    if (v) q.set(k, v);
    else q.delete(k);
  }
  const s = q.toString();
  return s ? `?${s}` : "";
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
  writeNav(state);
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(NAV_EVENT, { detail: state }));
}
