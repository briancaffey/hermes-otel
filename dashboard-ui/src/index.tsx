// The tab's shell (#287): a tablist, every page mounted once and hidden when
// inactive (so filters, results and open cards survive a tab switch), one
// source provider, an error boundary per page, and the colour scheme the
// host theme implies (dist/style.css reads `data-otel-scheme`).
import { React, useState, useEffect, useMemo, useContext, createContext, useTheme, register, sdkOk, cn } from "./sdk";
import { LivePage } from "./live";
import { TracesPage } from "./traces";
import { MetricsPage } from "./metrics";
import { LogsPage } from "./logs";
import { SettingsPage } from "./settings";
import { IconActivity, IconList, IconChart, IconSettings, IconScrollText, IconAlert } from "./icons";
import { readNav, writeNav, clearOtherTabs, NAV_EVENT, TAB_KEYS } from "./nav";
import { SourceProvider } from "./source";
import { localTimezone } from "./lib";

const TABS = [
  { id: "live", label: "Live", Icon: IconActivity, Page: LivePage },
  { id: "traces", label: "Traces", Icon: IconList, Page: TracesPage },
  { id: "metrics", label: "Metrics", Icon: IconChart, Page: MetricsPage },
  { id: "logs", label: "Logs", Icon: IconScrollText, Page: LogsPage },
  { id: "settings", label: "Settings", Icon: IconSettings, Page: SettingsPage },
];

/** Whether the page is the active tab; pages pause polling when it is not. */
const ActiveContext: any = createContext ? createContext<boolean>(true) : null;
export function useActive(): boolean {
  if (!ActiveContext || !useContext) return true;
  const v = useContext(ActiveContext);
  return v == null ? true : (v as boolean);
}

/** A page that throws renders its error and a reset, not a blank tab. */
class PageBoundaryImpl extends (React.Component as any) {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error) {
    console.error("[hermes_otel] page crashed", error);
  }
  render() {
    const { error } = this.state as { error: Error | null };
    const { name, children } = this.props as { name: string; children: any };
    if (!error) return children;
    return (
      <div role="alert" className="otel-error-banner">
        <IconAlert size={14} className="shrink-0" />
        <div className="min-w-0 space-y-1">
          <div>The {name} tab hit an error while rendering.</div>
          <pre className="otel-pre otel-error-detail">{String(error?.stack || error)}</pre>
          <button type="button" className="otel-btn" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      </div>
    );
  }
}

// React comes from the host at runtime, so the class is typed loosely for JSX.
const PageBoundary: any = PageBoundaryImpl;

/** The scheme the host theme implies, from the background token's luminance. */
export function schemeOf(background: string): "light" | "dark" {
  const m = /^#?([0-9a-f]{6})$/i.exec(background.trim()) || null;
  let rgb: number[] | null = null;
  if (m) rgb = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
  else {
    const n = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/i.exec(background);
    // Chromium serialises a color-mix() result as color(srgb r g b) with 0..1 floats,
    // which is what the host's theme variables compute to (#289).
    const c = /color\(\s*srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/i.exec(background);
    if (n) rgb = [Number(n[1]), Number(n[2]), Number(n[3])];
    else if (c) rgb = [Number(c[1]) * 255, Number(c[2]) * 255, Number(c[3]) * 255];
    else {
      const o = /oklch\(\s*([\d.]+%?)/i.exec(background);
      if (o) {
        const l = o[1].endsWith("%") ? Number(o[1].slice(0, -1)) / 100 : Number(o[1]);
        return l > 0.6 ? "light" : "dark";
      }
      const h = /hsla?\(\s*[\d.]+[,\s]+[\d.]+%?[,\s]+([\d.]+)%/i.exec(background);
      if (h) return Number(h[1]) > 60 ? "light" : "dark";
    }
  }
  if (!rgb) return "dark";
  const lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255;
  return lum > 0.6 ? "light" : "dark";
}

function useScheme(): "light" | "dark" {
  const theme = useTheme ? useTheme() : undefined;
  const name = theme?.themeName || "";
  return useMemo(() => {
    try {
      // The page's own background is what the host's theme painted; the
      // variable probe below is the fallback for a host whose body is
      // transparent (the host defines --background, not --color-background,
      // so the variable alone never detected the light theme, #289).
      const painted = getComputedStyle(document.body).backgroundColor;
      if (painted && !/rgba\(\s*0,\s*0,\s*0,\s*0\)|transparent/i.test(painted)) return schemeOf(painted);
      const probe = document.createElement("div");
      probe.style.background = "var(--background, var(--color-background))";
      probe.style.display = "none";
      document.body.appendChild(probe);
      const bg = getComputedStyle(probe).backgroundColor;
      probe.remove();
      return schemeOf(bg);
    } catch {
      return "dark";
    }
  }, [name]);
}

function OtelDashboard() {
  const [tab, setTabState] = useState(() => {
    const t = readNav().tab;
    return TABS.some((x) => x.id === t) ? (t as string) : "live";
  });
  const setTab = (id: string) => {
    setTabState(id);
    // The other tabs' keys leave the URL; shared keys (trace, session) stay
    // so a link from one tab to another keeps its target (nav.ts).
    writeNav({ tab: id, ...clearOtherTabs(id) });
  };
  // The Logs tab (or a session link) asks for the Traces tab with a trace/session.
  useEffect(() => {
    const onNav = (e: any) => {
      const d = e.detail || {};
      if (d.tab && TABS.some((t) => t.id === d.tab)) setTabState(d.tab);
    };
    window.addEventListener(NAV_EVENT, onNav);
    return () => window.removeEventListener(NAV_EVENT, onNav);
  }, []);
  // A page mounts the first time its tab is shown and stays mounted after,
  // so hidden pages never fetch before anyone has looked at them.
  const [visited, setVisited] = useState<Record<string, boolean>>(() => ({ [tab]: true }));
  useEffect(() => {
    setVisited((v) => (v[tab] ? v : { ...v, [tab]: true }));
  }, [tab]);
  const scheme = useScheme();
  const tz = useMemo(() => localTimezone(), []);
  const onKey = (e: any, i: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const j = e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : (i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length;
    setTab(TABS[j].id);
    (e.currentTarget.parentElement?.children[j] as any)?.focus?.();
  };
  return (
    <div className="otel-root space-y-4" data-otel-scheme={scheme}>
      <div className="otel-tabs flex items-center gap-1 border-b border-border" role="tablist" aria-label="OTel views">
        {TABS.map((t, i) => {
          const on = t.id === tab;
          const Icon = t.Icon;
          return (
            <button
              key={t.id}
              role="tab"
              id={`otel-tab-${t.id}`}
              aria-selected={on}
              aria-controls={`otel-panel-${t.id}`}
              tabIndex={on ? 0 : -1}
              onClick={() => setTab(t.id)}
              onKeyDown={(e: any) => onKey(e, i)}
              className={cn(
                "otel-tab inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors",
                on ? "otel-tab-active text-foreground" : "text-muted-foreground hover:text-foreground"
              )}
            >
              <Icon size={15} />
              {t.label}
            </button>
          );
        })}
        <span className="ml-auto pr-1 font-mono text-[11px] text-muted-foreground" title="absolute times are shown in this timezone">
          {tz ? `${tz} · ` : ""}hermes-otel
        </span>
      </div>
      <SourceProvider>
        {TABS.map((t) => {
          const on = t.id === tab;
          const Page = t.Page;
          const body = (
            <PageBoundary name={t.label}>
              <Page />
            </PageBoundary>
          );
          return (
            <div key={t.id} role="tabpanel" id={`otel-panel-${t.id}`} aria-labelledby={`otel-tab-${t.id}`} hidden={!on}>
              {!visited[t.id] && !on ? null : ActiveContext ? <ActiveContext.Provider value={on}>{body}</ActiveContext.Provider> : body}
            </div>
          );
        })}
      </SourceProvider>
    </div>
  );
}

// Keys a tab owns, exported for the pages' URL writers.
export { TAB_KEYS };

if (sdkOk) {
  register("hermes_otel", OtelDashboard);
} else {
  console.error("[hermes_otel] dashboard SDK unavailable — not registering");
}
