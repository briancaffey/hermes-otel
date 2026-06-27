// Bindings to the Hermes dashboard plugin SDK (window.__HERMES_PLUGIN_SDK__).
//
// We NEVER bundle React — every component imports `React` (and hooks) from
// here, so esbuild's classic JSX (jsxFactory: React.createElement) resolves to
// the host's single React instance. Using the host's SDK.components keeps the
// dashboard visually native (same shadcn primitives + theme as core Hermes).

/* eslint-disable @typescript-eslint/no-explicit-any */
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

// Native UI components (shadcn primitives provided by the host).
export const C: any = SDK.components || {};
export const {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Badge,
  Button,
  Input,
  Label,
  Select,
  SelectOption,
  Separator,
  Tabs,
  TabsList,
  TabsTrigger,
  Checkbox,
} = C;

export const fetchJSON: (url: string, opts?: any) => Promise<any> =
  SDK.fetchJSON || ((u: string) => fetch(u).then((r) => r.json()));

export const buildWsUrl: ((path: string, params?: any) => Promise<string> | string) | undefined =
  SDK.buildWsUrl;

export const timeAgo: (ts: number) => string = (SDK.utils && SDK.utils.timeAgo) || String;
export const cn: (...a: any[]) => string =
  (SDK.utils && SDK.utils.cn) ||
  ((...a: any[]) => a.filter(Boolean).join(" "));

export function register(name: string, component: any): void {
  if (PLUGINS && typeof PLUGINS.register === "function") {
    PLUGINS.register(name, component);
  } else {
    // eslint-disable-next-line no-console
    console.error("[hermes_otel] dashboard plugin registry unavailable");
  }
}

export const API = "/api/plugins/hermes_otel";
export const sdkOk = Boolean(SDK && SDK.React && PLUGINS && PLUGINS.register);
