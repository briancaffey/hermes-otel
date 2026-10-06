// One reading of an API failure for every page (#287). The host's fetchJSON
// throws Error("<status>: <body>") where the body is FastAPI's
// {"detail": "...", "kind": "..."} (kind per the dashboard contract:
// not_found | auth | config | backend). Network failures come through as a
// sentence from the host's apiErrorFromNetworkFailure.

export type ApiError = {
  status: number | null;
  kind: "not_found" | "auth" | "config" | "backend" | "validation" | "network" | "unknown";
  /** The server's `detail`, or the raw message. */
  detail: string;
  /** A short, user-facing sentence. */
  text: string;
  /** What the person can do about it. */
  hint: string | null;
};

const KIND_TEXT: Record<ApiError["kind"], string> = {
  not_found: "Not found",
  auth: "The backend rejected the credentials",
  config: "The backend entry is not configured for this",
  backend: "The backend did not answer",
  validation: "The request was rejected",
  network: "The dashboard server is unreachable",
  unknown: "Request failed",
};

export function describeError(e: unknown): ApiError {
  const msg = String((e as any)?.message ?? e ?? "").trim();
  const m = /^(\d{3}):\s*([\s\S]*)$/.exec(msg);
  const status = m ? Number(m[1]) : null;
  let detail = m ? m[2].trim() : msg;
  let kind: ApiError["kind"] = "unknown";
  if (m) {
    try {
      const body = JSON.parse(detail);
      if (body && typeof body === "object") {
        if (typeof body.detail === "string") detail = body.detail;
        else if (Array.isArray(body.detail)) detail = body.detail.map((d: any) => d?.msg || JSON.stringify(d)).join("; ");
        else if (body.detail != null) detail = JSON.stringify(body.detail);
        if (typeof body.kind === "string") kind = body.kind as ApiError["kind"];
      }
    } catch {
      /* not JSON: keep the text */
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
  const hint =
    kind === "auth"
      ? "Check the credential on the backend entry in the Settings tab."
      : kind === "config"
        ? "Check the backend entry in the Settings tab."
        : kind === "backend"
          ? "The Live source needs no backend and always works."
          : kind === "network"
            ? "Is the Hermes dashboard still running?"
            : null;
  return { status, kind, detail, text: KIND_TEXT[kind], hint };
}
