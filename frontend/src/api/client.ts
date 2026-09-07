/**
 * The single HTTP boundary. Nothing else in the app calls `fetch`.
 *
 * Everything true of *every* request lives here — the auth header, the error
 * envelope, the 401 redirect — so none of it can be forgotten at a call site.
 * Per-resource paths live in endpoints.ts.
 *
 * Resolve the backend origin at build time.
 *
 * Three inputs are supported, in priority order:
 *   1. VITE_API_BASE_URL / VITE_WS_BASE_URL — explicit, full origins.
 *   2. VITE_API_HOST — the backend host, injected by a managed host so the
 *      blueprint keeps working whatever name the platform assigns.
 *   3. localhost defaults for `docker compose` / `npm run dev`.
 *
 * "At build time" is not a detail: Vite inlines `import.meta.env` values into
 * the bundle, so these are frozen when the assets are built. Changing one means
 * rebuilding, not restarting.
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

// The localhost fallbacks are what make `npm run dev` work with no env file at
// all. Note there is no dev proxy — the browser calls this origin directly, so
// local development depends on the backend's CORS allow-list including
// localhost:5173.
const API_BASE =
  import.meta.env.VITE_API_BASE_URL ||
  (apiHost ? fromHost(apiHost, "https") : "http://localhost:8000");

export const WS_BASE =
  import.meta.env.VITE_WS_BASE_URL ||
  (apiHost ? fromHost(apiHost, "wss") : "ws://localhost:8000");

export const API_V1 = `${API_BASE}/api/v1`;

const TOKEN_KEY = "routeos_token";

/**
 * The JWT, in localStorage.
 *
 * localStorage rather than a cookie because the backend expects a bearer
 * header, not a session. Two consequences worth knowing: the token is readable
 * by any script on the page (so it is exposed to XSS in a way an HttpOnly
 * cookie would not be), and it survives a tab close — which is why
 * `auth.loadUser()` has to validate it on startup rather than trust it.
 */
export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

/**
 * A failed request, with the backend's error envelope unpacked.
 *
 * `code` is the stable machine-readable token ("ORDER_IMMUTABLE") and is safe
 * to branch on; `message` is prose for a human and is what the toasts show.
 */
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
  /** Send as application/x-www-form-urlencoded instead of JSON. Only /auth/login needs this. */
  form?: boolean;
}

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  // An absolute URL passes through untouched; anything else is relative to the
  // versioned API root.
  const url = new URL(path.startsWith("http") ? path : `${API_V1}${path}`);

  if (opts.params) {
    Object.entries(opts.params).forEach(([k, v]) => {
      // Empty string is dropped alongside null/undefined, which is what lets a
      // page pass its filter state straight through: an unselected dropdown is
      // "" and must be omitted, not sent as `status=` for the backend to try
      // to parse as an enum.
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    });
  }

  const headers: Record<string, string> = {};
  // Attached here, once, so no caller can forget it.
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (opts.form) {
    // /auth/login follows the OAuth2 password flow, which is specified as a
    // form post — that is also what makes the Authorize button in the
    // backend's /docs work.
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    body = new URLSearchParams(opts.body as Record<string, string>).toString();
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  const res = await fetch(url.toString(), { method: opts.method || "GET", headers, body });

  if (res.status === 401) {
    // Session is over: drop the dead token so the next startup does not retry it.
    tokenStore.clear();
    // ...but NOT for auth endpoints. A wrong password also returns 401, and
    // redirecting there would reload the page instead of letting Login render
    // "Incorrect email or password".
    //
    // A hard location change rather than router navigation, because this is
    // outside React and has no access to the router.
    if (!path.includes("/auth/")) window.location.href = "/login";
  }

  if (!res.ok) {
    // Sensible fallbacks first, in case the body is not JSON at all (a proxy
    // error page, a gateway timeout).
    let code = "ERROR";
    let message = res.statusText;
    let details: unknown;
    try {
      const data = await res.json();
      if (data.error) {
        // Our backend's envelope: {"error": {code, message, details}}.
        code = data.error.code;
        message = data.error.message;
        details = data.error.details;
      } else if (data.detail) {
        // FastAPI's own shape, which surfaces for anything our handlers do not
        // catch. `detail` may be a string or a structured validation object.
        message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      }
    } catch {
      /* ignore */
    }
    // Always an ApiError, so every caller has one shape to handle and a
    // `.message` worth showing.
    throw new ApiError(code, message, res.status, details);
  }

  // 204 No Content has an empty body; calling .json() on it would throw.
  if (res.status === 204) return undefined as T;
  // The cast is an assertion, not a validation — nothing checks at runtime that
  // the response matches T. See src/types/README.md.
  return (await res.json()) as T;
}
