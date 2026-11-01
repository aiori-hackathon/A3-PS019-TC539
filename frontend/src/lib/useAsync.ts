import { useCallback, useEffect, useRef, useState } from "react";
import { errText } from "../state/AppState";

export interface AsyncState<T> { data: T | null; error: string | null; loading: boolean; reload: () => void }

/** Runs `fn` when `key` changes; ignores results of superseded calls. */
export function useAsync<T>(fn: () => Promise<T>, key: unknown[], enabled = true): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (!enabled) { setLoading(false); return; }
    const mine = ++seq.current;
    setLoading(true);
    fnRef.current().then(
      (d) => { if (mine === seq.current) { setData(d); setError(null); setLoading(false); } },
      (e) => { if (mine === seq.current) { setError(errText(e)); setLoading(false); } },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...key, tick, enabled]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = window.setTimeout(() => setV(value), ms); return () => window.clearTimeout(t); }, [value, ms]);
  return v;
}
