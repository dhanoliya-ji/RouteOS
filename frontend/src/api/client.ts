const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
export const WS_BASE = import.meta.env.VITE_WS_BASE_URL || "ws://localhost:8000";
export const API_V1 = `${API_BASE}/api/v1`;

const TOKEN_KEY = "routeos_token";

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

export class ApiError extends Error {
  code: string;
  status: number;
  details: unknown;
  constructor(code: string, message: string, status: number, details?: unknown) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  params?: Record<string, string | number | boolean | undefined | null>;
  form?: boolean;
}

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const url = new URL(path.startsWith("http") ? path : `${API_V1}${path}`);
  if (opts.params) {
    Object.entries(opts.params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    });
  }

  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (opts.form) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    body = new URLSearchParams(opts.body as Record<string, string>).toString();
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  const res = await fetch(url.toString(), { method: opts.method || "GET", headers, body });

  if (res.status === 401) {
    tokenStore.clear();
    if (!path.includes("/auth/")) window.location.href = "/login";
  }

  if (!res.ok) {
    let code = "ERROR";
    let message = res.statusText;
    let details: unknown;
    try {
      const data = await res.json();
      if (data.error) {
        code = data.error.code;
        message = data.error.message;
        details = data.error.details;
      } else if (data.detail) {
        message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      }
    } catch {
      /* ignore */
    }
    throw new ApiError(code, message, res.status, details);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
