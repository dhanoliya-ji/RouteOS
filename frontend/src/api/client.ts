/**
 * Resolve the backend origin at build time.
 *
 * Three inputs are supported, in priority order:
 *   1. VITE_API_BASE_URL / VITE_WS_BASE_URL — explicit, full origins.
 *   2. VITE_API_HOST — the backend host, injected by a managed host so the
 *      blueprint keeps working whatever name the platform assigns.
 *   3. localhost defaults for `docker compose` / `npm run dev`.
 */
const apiHost = import.meta.env.VITE_API_HOST?.trim();

function fromHost(host: string, secure: "https" | "wss"): string {
  // Tolerate a value that already carries a scheme.
  if (/^[a-z]+:\/\//i.test(host)) return host;

  // Render's `fromService.property: host` resolves to the peer's *service name*
  // ("routeos-backend-h5x6"), which is its internal DNS name — not a public
  // FQDN. This bundle runs in a browser, so it needs the public origin. A
  // dot-less value is therefore a Render service name; expand it. Anything
  // already containing a dot is treated as a real hostname and left alone.
  const fqdn = host.includes(".") ? host : `${host}.onrender.com`;

  // Remote hosts terminate TLS, so always use the secure scheme.
  return `${secure}://${fqdn}`;
}

const API_BASE =
  import.meta.env.VITE_API_BASE_URL ||
  (apiHost ? fromHost(apiHost, "https") : "http://localhost:8000");

export const WS_BASE =
  import.meta.env.VITE_WS_BASE_URL ||
  (apiHost ? fromHost(apiHost, "wss") : "ws://localhost:8000");

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
