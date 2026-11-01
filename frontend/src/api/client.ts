// The only network layer. It sends the client header the backend requires for state-changing
// requests and turns every error body into an ApiError with a stable code.

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

const UNSAFE = new Set(["POST", "PUT", "DELETE", "PATCH"]);

async function request<T>(method: string, url: string, init: { json?: unknown; form?: FormData; signal?: AbortSignal } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (UNSAFE.has(method)) headers["X-FieldLedger-Client"] = "ui";
  let body: BodyInit | undefined;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  } else if (init.form) {
    body = init.form;
  }
  let res: Response;
  try {
    res = await fetch(url, { method, headers, body, credentials: "same-origin", signal: init.signal });
  } catch (err) {
    if ((err as { name?: string }).name === "AbortError") throw err;
    throw new ApiError("network", "Cannot reach the FieldLedger server. Check that it is still running.", 0);
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }
  if (!res.ok) {
    const e = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(e?.code ?? "internal_error", e?.message ?? `The server returned status ${res.status}.`, res.status);
  }
  return data as T;
}

export const api = {
  get: <T>(url: string, signal?: AbortSignal) => request<T>("GET", url, { signal }),
  post: <T>(url: string, json?: unknown) => request<T>("POST", url, json === undefined ? {} : { json }),
  put: <T>(url: string, json: unknown) => request<T>("PUT", url, { json }),
  del: <T>(url: string) => request<T>("DELETE", url),
  upload: <T>(url: string, form: FormData) => request<T>("POST", url, { form }),
};

export function exportUrl(scanId: string, format: string): string {
  return `/api/scans/${encodeURIComponent(scanId)}/export/${format}`;
}
export const REVIEWS_EXPORT_URL = "/api/workspace/reviews/export";

export function findingsQuery(params: Record<string, string | number | string[] | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) continue;
    if (Array.isArray(v)) v.forEach((x) => q.append(k, x));
    else q.set(k, String(v));
  }
  return q.toString();
}
