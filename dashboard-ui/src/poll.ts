// Interval polling that stops while the browser tab is hidden, while the
// plugin tab is not the active one (pages stay mounted, #287), and fires once
// when it becomes visible again (#188).
import { useEffect } from "./sdk";

export function usePolling(fn: () => void, ms: number, enabled: boolean): void {
  useEffect(() => {
    if (!enabled) return;
    const tick = () => {
      if (!document.hidden) fn();
    };
    const id = setInterval(tick, ms);
    const onVisible = () => {
      if (!document.hidden) fn();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [fn, ms, enabled]);
}
