// The data source the tab reads from: the in-process live store or one of the
// configured backends, chosen per request through the API's `backend=`
// parameter (#177). Remembered per browser in localStorage.
import { useState, useEffect, useCallback, fetchJSON, API } from "./sdk";

import { LIVE, withBackend } from "./params";
export { LIVE, withBackend };
const KEY = "hermes_otel.source";

export type BackendInfo = {
  name: string;
  type: string;
  endpoint?: string;
  supported: boolean;
  metrics: boolean;
  logs: boolean;
};

export type SourceStatus = {
  configured: boolean;
  active: string | null;
  available: BackendInfo[];
  reason?: string;
  queryable_types?: string[];
  query_backend_pin?: string | null;
  [k: string]: any;
};

export function readSource(): string {
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
}

/**
 * Shared source state: the chosen source, the server's view of what is
 * available, and a refresh. `status` is fetched for the chosen backend so the
 * label/placeholder match the adapter that will actually answer.
 */
export function useSource(): {
  source: string;
  setSource: (s: string) => void;
  status: SourceStatus | null;
  refresh: () => void;
  isLive: boolean;
} {
  const [source, setSourceState] = useState<string>(readSource());
  const [status, setStatus] = useState<SourceStatus | null>(null);

  const refresh = useCallback(() => {
    const p = withBackend(new URLSearchParams(), source);
    fetchJSON(`${API}/status?${p}`)
      .then((st: SourceStatus) => {
        setStatus(st);
        // A remembered backend that is no longer configured falls back to live.
        if (source !== LIVE && !(st.available || []).some((b) => b.name === source)) {
          setSourceState(LIVE);
          writeSource(LIVE);
        }
      })
      .catch(() => setStatus({ configured: false, active: null, available: [], reason: "status unavailable" }));
  }, [source]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const setSource = useCallback((s: string) => {
    writeSource(s);
    setSourceState(s);
  }, []);

  return { source, setSource, status, refresh, isLive: source === LIVE };
}
