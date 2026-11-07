import { useCallback, useEffect, useState } from "react";
import type { Results } from "./types";

export type Load = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; data: Results };

export function useResults(): Load & { retry: () => void } {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    setLoad({ state: "loading" });
    fetch("./results.json", { signal: ctl.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`results.json: HTTP ${r.status}`);
        return r.json() as Promise<Results>;
      })
      .then((data) => setLoad({ state: "ready", data }))
      .catch((e: Error) => {
        if (e.name !== "AbortError") setLoad({ state: "error", message: e.message });
      });
    return () => ctl.abort();
  }, [attempt]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...load, retry };
}
