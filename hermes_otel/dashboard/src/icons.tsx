// Inline lucide-style SVG icons (the host doesn't expose lucide to plugins).
// Stroke-width + shapes match Hermes' shadcn-aligned visuals.
import { React } from "./sdk";
import { Kind } from "./lib";

/* eslint-disable @typescript-eslint/no-explicit-any */
function svg(size: number | undefined, className: string | undefined, children: any) {
  return (
    <svg
      width={size || 16}
      height={size || 16}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className || ""}
      aria-hidden
    >
      {children}
    </svg>
  );
}
type IP = { size?: number; className?: string };

export const IconZap = (p: IP) => svg(p.size, p.className, <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />);
export const IconWrench = (p: IP) =>
  svg(p.size, p.className, <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />);
export const IconTerminal = (p: IP) =>
  svg(p.size, p.className, [<polyline key="a" points="4 17 10 11 4 5" />, <line key="b" x1={12} x2={20} y1={19} y2={19} />]);
export const IconClock = (p: IP) =>
  svg(p.size, p.className, [<circle key="a" cx={12} cy={12} r={10} />, <polyline key="b" points="12 6 12 12 16 14" />]);
export const IconActivity = (p: IP) => svg(p.size, p.className, <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />);
export const IconChevronRight = (p: IP) => svg(p.size, p.className, <path d="m9 18 6-6-6-6" />);
export const IconCoins = (p: IP) =>
  svg(p.size, p.className, [
    <circle key="a" cx={8} cy={8} r={6} />,
    <path key="b" d="M18.09 10.37A6 6 0 1 1 10.34 18" />,
    <path key="c" d="M7 6h1v4" />,
    <path key="d" d="m16.71 13.88.7.71-2.82 2.82" />,
  ]);
export const IconSparkles = (p: IP) =>
  svg(p.size, p.className, <path d="M9.94 14.06 7 21l-2.94-6.94L-.94 12 7 9.06 9.94 3l2.94 6.06L19.94 12zM18 5l1 2.5L21.5 8 19 9l-1 2.5L17 9l-2.5-1L17 7z" />);
export const IconShield = (p: IP) => svg(p.size, p.className, <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />);
export const IconUsers = (p: IP) =>
  svg(p.size, p.className, [
    <path key="a" d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />,
    <circle key="b" cx={9} cy={7} r={4} />,
    <path key="c" d="M22 21v-2a4 4 0 0 0-3-3.87" />,
    <path key="d" d="M16 3.13a4 4 0 0 1 0 7.75" />,
  ]);
export const IconChart = (p: IP) =>
  svg(p.size, p.className, [<path key="a" d="M3 3v16a2 2 0 0 0 2 2h16" />, <path key="b" d="m19 9-5 5-4-4-3 3" />]);
export const IconList = (p: IP) =>
  svg(p.size, p.className, [
    <line key="a" x1={8} x2={21} y1={6} y2={6} />,
    <line key="b" x1={8} x2={21} y1={12} y2={12} />,
    <line key="c" x1={8} x2={21} y1={18} y2={18} />,
    <line key="d" x1={3} x2={3.01} y1={6} y2={6} />,
    <line key="e" x1={3} x2={3.01} y1={12} y2={12} />,
    <line key="f" x1={3} x2={3.01} y1={18} y2={18} />,
  ]);

// Category → icon + Tailwind text-color, by root span name (trace cards).
export const CATEGORY: Record<string, { Icon: (p: IP) => any; color: string; label: string | null }> = {
  llm: { Icon: IconZap, color: "text-sky-400", label: "llm" },
  tool: { Icon: IconWrench, color: "text-amber-400", label: "tool" },
  agent: { Icon: IconTerminal, color: "text-emerald-400", label: "agent" },
  cron: { Icon: IconClock, color: "text-violet-400", label: "cron" },
  skill: { Icon: IconSparkles, color: "text-emerald-300", label: "skill" },
  approval: { Icon: IconShield, color: "text-pink-400", label: "approval" },
  subagent: { Icon: IconUsers, color: "text-violet-400", label: "subagent" },
  other: { Icon: IconActivity, color: "text-muted-foreground", label: null },
};

export function kindIcon(kind: Kind): (p: IP) => any {
  return (CATEGORY[kind] || CATEGORY.other).Icon;
}
export function categorize(rootName: string) {
  const n = (rootName || "").toLowerCase();
  if (n.startsWith("api.") || n.startsWith("llm.")) return CATEGORY.llm;
  if (n.startsWith("skill.")) return CATEGORY.skill;
  if (n.startsWith("approval")) return CATEGORY.approval;
  if (n.startsWith("subagent")) return CATEGORY.subagent;
  if (n.startsWith("tool.")) return CATEGORY.tool;
  if (n === "agent" || n.startsWith("agent.")) return CATEGORY.agent;
  if (n === "cron" || n.startsWith("cron")) return CATEGORY.cron;
  return CATEGORY.other;
}
