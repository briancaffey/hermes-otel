// One search bar for every source (#183). The same fields drive the live
// store's /live/traces query and a backend adapter's /traces/search; each
// side translates what it can and ignores the rest.
import { React, Input, Label, Select, SelectOption, Button, cn } from "./sdk";
import { TraceFilters, DEFAULT_FILTERS, KINDS } from "./params";
export { TraceFilters, DEFAULT_FILTERS, liveParams, backendParams, isDefaultFilters } from "./params";

function Field({ label, children, className }: { label: string; children: any; className?: string }) {
  return (
    <div className={cn("space-y-1", className)}>
      <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</Label>
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
}: {
  filters: TraceFilters;
  onChange: (f: TraceFilters) => void;
  onSubmit: () => void;
  backend: boolean;
  status?: any;
  busy?: boolean;
}) {
  const set = (k: keyof TraceFilters, v: any) => onChange({ ...filters, [k]: v });
  const input = (k: keyof TraceFilters, placeholder: string, type = "text") => (
    <Input
      className="h-8"
      type={type}
      placeholder={placeholder}
      value={String((filters as any)[k] ?? "")}
      onChange={(e: any) => set(k, e.target.value)}
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
        <Field label="status">
          <Select value={filters.status} onValueChange={(v: string) => set("status", v)} className="h-8">
            <SelectOption value="">any</SelectOption>
            <SelectOption value="ok">ok</SelectOption>
            <SelectOption value="error">error</SelectOption>
          </Select>
        </Field>
        <Field label="kind">
          <Select value={filters.kind} onValueChange={(v: string) => set("kind", v)} className="h-8">
            <SelectOption value="">any</SelectOption>
            {KINDS.map((k) => (
              <SelectOption key={k} value={k}>
                {k}
              </SelectOption>
            ))}
          </Select>
        </Field>
        <Field label="tool">{input("tool", "terminal")}</Field>
        <Field label="model">{input("model", backend ? "exact model name" : "substring")}</Field>
        <Field label="session id">{input("session", "20260920_0814…")}</Field>
        <Field label="min duration (ms)">{input("minDurationMs", "0", "number")}</Field>
        <Field label="text">{input("text", backend ? "in the prompt" : "anywhere in attributes")}</Field>
        <Field label="trace id">{input("traceId", "32 hex chars")}</Field>
        <Field label="lookback">
          <Select value={String(filters.lookback)} onValueChange={(v: string) => set("lookback", Number(v))} className="h-8">
            <SelectOption value="0.25">15m</SelectOption>
            <SelectOption value="1">1h</SelectOption>
            <SelectOption value="6">6h</SelectOption>
            <SelectOption value="24">24h</SelectOption>
            <SelectOption value="72">3d</SelectOption>
            <SelectOption value="168">7d</SelectOption>
            <SelectOption value="720">30d</SelectOption>
          </Select>
        </Field>
      </div>
      {backend ? (
        <div className="otel-search-grid">
          <Field label={rawLabel} className="otel-span-2">
            {input("q", status?.raw_placeholder || "")}
          </Field>
          <Field label="service">{input("service", "any")}</Field>
          <label className="otel-self-end inline-flex cursor-pointer items-center gap-1.5 pb-2 text-xs text-muted-foreground">
            <input type="checkbox" checked={filters.rootsOnly} onChange={(e: any) => set("rootsOnly", e.target.checked)} />
            roots only
          </label>
        </div>
      ) : null}
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" disabled={!!busy}>
          {busy ? "Searching…" : "Search"}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={() => onChange({ ...DEFAULT_FILTERS, lookback: filters.lookback, rootsOnly: filters.rootsOnly })}>
          Clear
        </Button>
      </div>
    </form>
  );
}
