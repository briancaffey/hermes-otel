// One reading of an API failure for every page (#287). Since Hermes 0.22 the
// host's fetchJSON throws an ApiError (web/src/lib/api-error.ts): the message
// is the detail sentence, and the HTTP status and raw body are separate fields
// (`.status`, `.body`; status 0 means the request never reached the server).
// Older hosts threw Error("<status>: <body>"); both forms are read (#611). The
// body is FastAPI's {"detail": "...", "kind": "..."} (kind per the dashboard
// contract: not_found | auth | config | request | backend).

export type ApiError = {
  status: number | null;
  kind: "not_found" | "auth" | "config" | "request" | "backend" | "validation" | "network" | "unknown";
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
  request: "The plugin rejected the request",
  backend: "The backend did not answer",
  validation: "The request was rejected",
  network: "The dashboard server is unreachable",
  unknown: "Request failed",
};

// "cannot reach" is the host's own unreachable sentence (API_UNREACHABLE_MESSAGE).
const NETWORK_RE = /unreachable|cannot reach|failed to fetch|network|refused|offline/i;

/** The status, raw body and message of a thrown value, from either host form. */
function parts(e: unknown): { status: number | null; body: string | null; msg: string } {
  const msg = String((e as any)?.message ?? e ?? "").trim();
  const status = (e as any)?.status;
  const body = (e as any)?.body;
  if (typeof status === "number" && Number.isFinite(status)) {
    return { status, body: typeof body === "string" ? body : null, msg };
  }
  const m = /^(\d{3}):\s*([\s\S]*)$/.exec(msg);
  if (m) return { status: Number(m[1]), body: m[2].trim(), msg: m[2].trim() };
  return { status: null, body: null, msg };
}

export function describeError(e: unknown): ApiError {
  const { status, body, msg } = parts(e);
  let detail = msg;
  let kind: ApiError["kind"] = "unknown";
  if (status !== null && status !== 0) {
    try {
      const parsed = JSON.parse(body ?? "");
      if (parsed && typeof parsed === "object") {
        if (typeof parsed.detail === "string") detail = parsed.detail;
        else if (Array.isArray(parsed.detail)) detail = parsed.detail.map((d: any) => d?.msg || JSON.stringify(d)).join("; ");
        else if (parsed.detail != null) detail = JSON.stringify(parsed.detail);
        if (typeof parsed.kind === "string" && parsed.kind in KIND_TEXT) kind = parsed.kind as ApiError["kind"];
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
  } else if (status === 0 || NETWORK_RE.test(msg)) {
    kind = "network";
  }
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
