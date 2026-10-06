// Bindings to the Hermes dashboard plugin SDK (window.__HERMES_PLUGIN_SDK__).
//
// We NEVER bundle React — every component imports `React` (and hooks) from
// here, so esbuild's classic JSX (jsxFactory: React.createElement) resolves to
// the host's single React instance. Using the host's SDK.components keeps the
// dashboard visually native (same shadcn primitives + theme as core Hermes).
//
// Host facts the tab relies on (hermes-agent web/src/plugins/sdk.d.ts and the
// compiled registry, checked against v0.21.5):
//   - fetchJSON handles auth and throws Error("<status>: <body>") on a non-2xx.
//   - It appends ?profile= only for the host's own route families, never for
//     /api/plugins/*, so the tab adds the page's profile itself (#287).
//   - Select reads only `value` and a STRING `children` from each SelectOption;
//     `disabled` and `title` are ignored, so an unusable entry must not be an
//     option at all (sourceselect.tsx).
//   - Checkbox is a role=switch button with checked / onCheckedChange.
import type * as ReactTypes from "react";

const SDK: any = (window as any).__HERMES_PLUGIN_SDK__ || {};
const PLUGINS: any = (window as any).__HERMES_PLUGINS__ || {};

// Value is the host's React (any at the value level); JSX/element typing comes
// from @types/react's global namespace. Hooks are cast to the real React
// signatures so generics (useState<T>) type-check correctly.
export const React: any = SDK.React;
const hooks: any = SDK.hooks || {};
export const useState = hooks.useState as typeof ReactTypes.useState;
export const useEffect = hooks.useEffect as typeof ReactTypes.useEffect;
export const useCallback = hooks.useCallback as typeof ReactTypes.useCallback;
export const useMemo = hooks.useMemo as typeof ReactTypes.useMemo;
export const useRef = hooks.useRef as typeof ReactTypes.useRef;
export const useContext = (hooks.useContext || (SDK.React && SDK.React.useContext)) as typeof ReactTypes.useContext;
export const createContext = (hooks.createContext || (SDK.React && SDK.React.createContext)) as typeof ReactTypes.createContext;
/** The host's theme hook ({themeName, availableThemes, setTheme}); absent on older hosts. */
export const useTheme: (() => { themeName?: string } | undefined) | undefined = SDK.useTheme;

// Native UI components (shadcn primitives provided by the host).
export const C: any = SDK.components || {};
export const { Card, CardHeader, CardContent, Badge, Button, Input, Label, Select, SelectOption, Checkbox } = C;

export const API = "/api/plugins/hermes_otel";

/** The profile the page is looking at (`?profile=<name>` in the host URL), or "". */
export function pageProfile(): string {
  try {
    return new URLSearchParams(window.location.search).get("profile") || "";
  } catch {
    return "";
  }
}

/** Append the page's profile to a plugin API URL so the host scopes the request (#287). */
export function withProfile(url: string): string {
  const p = pageProfile();
  if (!p || /[?&]profile=/.test(url)) return url;
  return `${url}${url.includes("?") ? "&" : "?"}profile=${encodeURIComponent(p)}`;
}

const hostFetch: ((url: string, opts?: any) => Promise<any>) | undefined = SDK.fetchJSON;

/** JSON fetch through the host (auth, 401 redirect) with the profile attached. */
export const fetchJSON: (url: string, opts?: any) => Promise<any> = hostFetch
  ? (url, opts) => hostFetch(withProfile(url), opts)
  : async () => {
      throw new Error("0: dashboard SDK unavailable");
    };

/** `fetchJSON` for a plugin route: `api("/live/status", {limit: 5})`. */
export function api(path: string, params?: URLSearchParams | Record<string, string | number | boolean | null | undefined>): Promise<any> {
  let q = "";
  if (params instanceof URLSearchParams) q = params.toString();
  else if (params) {
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v != null && v !== "") sp.set(k, String(v));
    q = sp.toString();
  }
  return fetchJSON(`${API}${path}${q ? `?${q}` : ""}`);
}

export const cn: (...a: any[]) => string = (SDK.utils && SDK.utils.cn) || ((...a: any[]) => a.filter(Boolean).join(" "));

export function register(name: string, component: any): void {
  if (PLUGINS && typeof PLUGINS.register === "function") {
    PLUGINS.register(name, component);
  } else {
    console.error("[hermes_otel] dashboard plugin registry unavailable");
  }
}

export const sdkOk = Boolean(SDK && SDK.React && PLUGINS && PLUGINS.register);
