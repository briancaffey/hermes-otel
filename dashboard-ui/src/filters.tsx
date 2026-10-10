// One search bar for every source (#183, #281). The same fields drive the live
// store's /live/traces query and a backend adapter's /traces/search; the
// selected source's `filters` capability (contract §1) says which fields it
// honours, and the bar says so on each field instead of silently ignoring it.
import { React, Input, Label, Select, SelectOption, Button, cn } from "./sdk";
import { TraceFilters, DEFAULT_FILTERS, KINDS, LOOKBACKS, FilterSupport, fieldSupport } from "./params";
export { TraceFilters, DEFAULT_FILTERS, liveParams, backendParams, isDefaultFilters } from "./params";

const SUPPORT_NOTE: Record<string, string> = {
  client: "applied after the backend answers: a page can come back short",
  none: "this source ignores this field",
};

function Field({
  label,
  children,
  className,
  support,
}: {
  label: string;
  children: any;
  className?: string;
  support?: "server" | "client" | "none" | "unknown";
}) {
  const note = support && SUPPORT_NOTE[support];
  const off = support === "none";
  return (
    <div className={cn("space-y-1", off ? "otel-field-off" : "", className)} title={note || undefined}>
      <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
        {support === "client" ? (
          <span className="otel-field-note"> · after fetch</span>
        ) : support === "none" ? (
          <span className="otel-field-note"> · ignored</span>
        ) : null}
      </Label>
      {children}
    </div>
  );
}

export function FilterBar({
  filters,
  onChange,
  onSubmit,
  backend,
  status,
  busy,
  support,
  hide,
}: {
  filters: TraceFilters;
  onChange: (f: TraceFilters) => void;
  onSubmit: () => void;
  backend: boolean;
  status?: any;
  busy?: boolean;
  /** which fields the selected source honours */
  support?: FilterSupport | null;
  /** fields a view does not use (the Sessions view keeps only lookback) */
  hide?: (keyof TraceFilters)[];
}) {
  const set = (k: keyof TraceFilters, v: any) => onChange({ ...filters, [k]: v });
  const sup = (k: keyof TraceFilters) => fieldSupport(support, k);
  const show = (k: keyof TraceFilters) => !hide || !hide.includes(k);
  const input = (k: keyof TraceFilters, placeholder: string, type = "text") => (
    <Input
      className="h-8"
      type={type}
      placeholder={placeholder}
      value={String((filters as any)[k] ?? "")}
      onChange={(e: any) => set(k, e.target.value)}
      aria-label={String(k)}
      min={type === "number" ? 0 : undefined}
    />
  );
  const lang: string = status?.query_lang_label || "";
  const rawLabel = !lang ? "native query" : /filter$/i.test(lang.trim()) ? lang : `${lang} query`;
  return (
    <form
      className="space-y-2"
      onSubmit={(e: any) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div className="otel-search-grid">
        {show("status") ? (
          <Field label="status" support={sup("status")}>
            <Select value={filters.status} onValueChange={(v: string) => set("status", v)} className="h-8" aria-label="status">
              <SelectOption value="">any</SelectOption>
              <SelectOption value="ok">ok</SelectOption>
              <SelectOption value="error">error</SelectOption>
            </Select>
          </Field>
        ) : null}
        {show("kind") ? (
          <Field label="kind" support={sup("kind")}>
            <Select value={filters.kind} onValueChange={(v: string) => set("kind", v)} className="h-8" aria-label="kind">
              <SelectOption value="">any</SelectOption>
              {KINDS.map((k) => (
                <SelectOption key={k} value={k}>
                  {k}
                </SelectOption>
              ))}
            </Select>
          </Field>
        ) : null}
        {show("tool") ? (
          <Field label="tool" support={sup("tool")}>
            {input("tool", "terminal")}
          </Field>
        ) : null}
        {show("model") ? (
          <Field label="model" support={sup("model")}>
            {input("model", backend ? "exact model name" : "substring")}
          </Field>
        ) : null}
        {show("session") ? (
          <Field label="session id" support={sup("session")}>
            {input("session", "20260920_0814…")}
          </Field>
        ) : null}
        {show("minDurationMs") ? (
          <Field label="min duration (ms)" support={sup("minDurationMs")}>
            {input("minDurationMs", "0", "number")}
          </Field>
        ) : null}
        {show("text") ? (
          <Field label="text" support={sup("text")}>
            {input("text", backend ? "in the prompt" : "anywhere in attributes")}
          </Field>
        ) : null}
        {show("traceId") ? (
          <Field label="trace id" support={sup("traceId")}>
            {input("traceId", "trace id")}
          </Field>
        ) : null}
        <Field label="lookback">
          <Select value={String(filters.lookback)} onValueChange={(v: string) => set("lookback", Number(v))} className="h-8" aria-label="lookback">
            {LOOKBACKS.map((l) => (
              <SelectOption key={l.hours} value={String(l.hours)}>
                {l.label}
              </SelectOption>
            ))}
          </Select>
        </Field>
      </div>
      {backend && (show("q") || show("service")) ? (
        <div className="otel-search-grid">
          {show("q") ? (
            <Field label={rawLabel} className="otel-span-2" support={sup("q")}>
              {input("q", status?.raw_placeholder || "")}
            </Field>
          ) : null}
          {show("service") ? (
            <Field label="service" support={sup("service")}>
              {input("service", "any")}
            </Field>
          ) : null}
          {show("rootsOnly") ? (
            <label
              className="otel-self-end inline-flex cursor-pointer items-center gap-1.5 pb-2 text-xs text-muted-foreground"
              title={sup("rootsOnly") === "client" ? SUPPORT_NOTE.client : "list whole turns, not every matching span"}
            >
              <input type="checkbox" checked={filters.rootsOnly} onChange={(e: any) => set("rootsOnly", e.target.checked)} />
              roots only{sup("rootsOnly") === "client" ? " · after fetch" : ""}
            </label>
          ) : null}
        </div>
      ) : null}
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" disabled={!!busy}>
          {busy ? "Searching…" : "Search"}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          title="clear every field; keeps the lookback and roots-only"
          onClick={() => onChange({ ...DEFAULT_FILTERS, lookback: filters.lookback, rootsOnly: filters.rootsOnly })}
        >
          Clear
        </Button>
      </div>
    </form>
  );
}
