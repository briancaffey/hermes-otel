// Installs a stand-in for window.__HERMES_PLUGIN_SDK__ / __HERMES_PLUGINS__ so
// the page components can be rendered under vitest. At runtime the host
// provides React and its shadcn primitives; here a local React and the
// plainest possible elements stand in. `fetchJSON` is a vi.fn the tests
// program per URL.
import React from "react";
import { vi } from "vitest";

const el =
  (tag: string, extra: Record<string, any> = {}) =>
  ({ children, className, ...rest }: any) =>
    React.createElement(tag, { className, ...extra, ...rest }, children);

const Select = ({ value, onValueChange, children, className }: any) =>
  React.createElement("select", { value, className, onChange: (e: any) => onValueChange?.(e.target.value) }, children);
const SelectOption = ({ value, children, disabled, title }: any) => React.createElement("option", { value, disabled, title }, children);

export const fetchJSON = vi.fn(async (url: string) => {
  throw new Error(`404: unmocked ${url}`);
});

export const registered: Record<string, any> = {};

(globalThis as any).window.__HERMES_PLUGIN_SDK__ = {
  sdkVersion: "test",
  React,
  hooks: {
    useState: React.useState,
    useEffect: React.useEffect,
    useCallback: React.useCallback,
    useMemo: React.useMemo,
    useRef: React.useRef,
    useContext: React.useContext,
    createContext: React.createContext,
  },
  components: {
    Card: el("div", { "data-c": "card" }),
    CardHeader: el("div"),
    CardTitle: el("div"),
    CardContent: el("div"),
    Badge: el("span", { "data-c": "badge" }),
    Button: el("button", { type: "button" }),
    Input: ({ value, onChange, ...rest }: any) => React.createElement("input", { value: value ?? "", onChange, ...rest }),
    Label: el("label"),
    Select,
    SelectOption,
    Checkbox: ({ checked, onCheckedChange, ...rest }: any) =>
      React.createElement("input", { type: "checkbox", checked: !!checked, onChange: (e: any) => onCheckedChange?.(e.target.checked), ...rest }),
  },
  fetchJSON,
  utils: { cn: (...a: any[]) => a.filter(Boolean).join(" "), timeAgo: String, isoTimeAgo: String },
};
(globalThis as any).window.__HERMES_PLUGINS__ = {
  register: (name: string, component: any) => {
    registered[name] = component;
  },
  registerSlot: () => undefined,
};
