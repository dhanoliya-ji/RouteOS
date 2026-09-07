/**
 * Tests for the single HTTP boundary.
 *
 * Everything true of every request lives in client.ts — the auth header, the
 * error envelope, the 401 redirect — so a bug here affects the whole app. It is
 * also pure enough to test properly: stub `fetch`, and every branch is
 * reachable without a component or a server.
 *
 * The negative cases carry the weight. A request that fails to send is obvious
 * immediately; a request that silently sends the wrong thing is not.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest, tokenStore } from "./client";

/** Stub fetch with one JSON response, and hand back the spy to inspect. */
function stubFetch(body: unknown, init: { status?: number } = {}) {
  const status = init.status ?? 200;
  const spy = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: "Stubbed",
    json: async () => body,
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}

/** The URL the last call was made to, parsed. */
function calledUrl(spy: ReturnType<typeof vi.fn>) {
  return new URL(spy.mock.calls[0][0] as string);
}

/** The init object the last call passed. */
function calledInit(spy: ReturnType<typeof vi.fn>) {
  return spy.mock.calls[0][1] as RequestInit & { headers: Record<string, string> };
}

/**
 * Await a request that is expected to FAIL, and return its ApiError.
 *
 * Better than `.catch((e) => e)`: that types the result as unknown (so every
 * assertion needs a cast) and, worse, silently returns the RESPONSE if the
 * request unexpectedly succeeded — turning a real regression into a confusing
 * assertion failure several lines later. This fails loudly instead.
 */
async function rejection(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    return e as ApiError;
  }
  throw new Error("expected the request to reject, but it resolved");
}

describe("URL building", () => {
  it("prefixes a relative path with the versioned API root", async () => {
    const spy = stubFetch({});
    await apiRequest("/orders");
    expect(calledUrl(spy).pathname).toBe("/api/v1/orders");
  });

  it("leaves an absolute URL alone", async () => {
    const spy = stubFetch({});
    await apiRequest("http://elsewhere.test/custom");
    const url = calledUrl(spy);
    expect(url.host).toBe("elsewhere.test");
    expect(url.pathname).toBe("/custom");
  });

  it("appends query parameters", async () => {
    const spy = stubFetch({});
    await apiRequest("/orders", { params: { page: 2, status: "PENDING" } });
    const url = calledUrl(spy);
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.get("status")).toBe("PENDING");
  });

  it("drops empty, null and undefined parameters", async () => {
    /**
     * The behaviour that lets a page pass its filter state straight through.
     * An unselected dropdown is "", and sending `status=` would have the
     * backend try to parse an empty string as an enum and return a 422.
     */
    const spy = stubFetch({});
    await apiRequest("/orders", {
      params: { status: "", priority: undefined, depot_id: null, page: 1 },
    });
    const url = calledUrl(spy);
    expect(url.searchParams.has("status")).toBe(false);
    expect(url.searchParams.has("priority")).toBe(false);
    expect(url.searchParams.has("depot_id")).toBe(false);
    // ...while a real value still goes.
    expect(url.searchParams.get("page")).toBe("1");
  });

  it("keeps a boolean false, which is a real filter value", async () => {
    // `assigned=false` means "only PENDING". Dropping it because it is falsy
    // would silently change the query to "no filter".
    const spy = stubFetch({});
    await apiRequest("/orders", { params: { assigned: false } });
    expect(calledUrl(spy).searchParams.get("assigned")).toBe("false");
  });

  it("keeps a numeric zero", async () => {
    const spy = stubFetch({});
    await apiRequest("/orders", { params: { radius_km: 0 } });
    expect(calledUrl(spy).searchParams.get("radius_km")).toBe("0");
  });
});

describe("authentication header", () => {
  it("attaches the bearer token when one is stored", async () => {
    tokenStore.set("a-stored-token");
    const spy = stubFetch({});
    await apiRequest("/orders");
    expect(calledInit(spy).headers.Authorization).toBe("Bearer a-stored-token");
  });

  it("sends no Authorization header when signed out", async () => {
    const spy = stubFetch({});
    await apiRequest("/orders");
    expect(calledInit(spy).headers.Authorization).toBeUndefined();
  });
});

describe("request body encoding", () => {
  it("sends JSON by default", async () => {
    const spy = stubFetch({});
    await apiRequest("/orders", { method: "POST", body: { customer_name: "Aarav" } });
    const init = calledInit(spy);
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ customer_name: "Aarav" });
  });

  it("form-encodes when asked, for the OAuth2 login endpoint", async () => {
    /**
     * /auth/login follows the OAuth2 password flow, which is specified as a
     * form post — and is the only endpoint needing this.
     */
    const spy = stubFetch({ access_token: "t" });
    await apiRequest("/auth/login", {
      method: "POST",
      form: true,
      body: { username: "a@b.dev", password: "pw" },
    });
    const init = calledInit(spy);
    expect(init.headers["Content-Type"]).toBe("application/x-www-form-urlencoded");
    // URL-encoded, so the @ in the email is escaped.
    expect(init.body).toBe("username=a%40b.dev&password=pw");
  });

  it("sends no body or content type for a bare GET", async () => {
    const spy = stubFetch({});
    await apiRequest("/orders");
    const init = calledInit(spy);
    expect(init.body).toBeUndefined();
    expect(init.headers["Content-Type"]).toBeUndefined();
  });

  it("defaults the method to GET", async () => {
    const spy = stubFetch({});
    await apiRequest("/orders");
    expect(calledInit(spy).method).toBe("GET");
  });
});

describe("response handling", () => {
  it("returns the parsed body", async () => {
    stubFetch({ id: 7, order_number: "ORD-00007" });
    const order = await apiRequest<{ id: number; order_number: string }>("/orders/7");
    expect(order).toEqual({ id: 7, order_number: "ORD-00007" });
  });

  it("returns undefined for 204 rather than parsing an empty body", async () => {
    // Calling .json() on a No Content response throws, so this branch exists
    // to keep DELETE from failing on success.
    const spy = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      json: async () => {
        throw new Error(".json() must not be called on a 204");
      },
    });
    vi.stubGlobal("fetch", spy);
    await expect(apiRequest("/orders/7", { method: "DELETE" })).resolves.toBeUndefined();
  });
});

describe("error handling", () => {
  it("unwraps the backend's error envelope into an ApiError", async () => {
    stubFetch(
      { error: { code: "ORDER_IMMUTABLE", message: "Order cannot be modified", details: { id: 7 } } },
      { status: 409 },
    );
    const err = await rejection(apiRequest("/orders/7", { method: "PATCH", body: {} }));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("ORDER_IMMUTABLE");
    expect(err.message).toBe("Order cannot be modified");
    expect(err.status).toBe(409);
    expect(err.details).toEqual({ id: 7 });
  });

  it("falls back to FastAPI's own `detail` shape", async () => {
    // Reached for anything our handlers do not catch, so it must not produce
    // an empty message.
    stubFetch({ detail: "Not Found" }, { status: 404 });
    const err = await rejection(apiRequest("/nope"));
    expect(err.message).toBe("Not Found");
  });

  it("stringifies a structured `detail`", async () => {
    stubFetch({ detail: [{ loc: ["body", "weight_kg"], msg: "must be > 0" }] }, { status: 422 });
    const err = await rejection(apiRequest("/orders", { method: "POST", body: {} }));
    // Not [object Object] — the field information survives into the message
    // the toast will show.
    expect(err.message).toContain("weight_kg");
  });

  it("still throws an ApiError when the body is not JSON at all", async () => {
    /**
     * A proxy error page or a gateway timeout. The fallbacks matter here: a
     * caller must always get an ApiError with a usable message, never a
     * SyntaxError from the parse.
     */
    const spy = vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: async () => {
        throw new SyntaxError("Unexpected token < in JSON");
      },
    });
    vi.stubGlobal("fetch", spy);
    const err = await rejection(apiRequest("/orders"));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(502);
    expect(err.message).toBe("Bad Gateway");
    expect(err.code).toBe("ERROR");
  });

  it("is a real Error, so a thrown ApiError behaves normally", async () => {
    stubFetch({ error: { code: "X", message: "boom", details: {} } }, { status: 400 });
    const err = await rejection(apiRequest("/orders"));
    expect(err).toBeInstanceOf(Error);
    expect(String(err)).toContain("boom");
  });
});

describe("401 handling", () => {
  // window.location is replaced per test so the redirect can be observed
  // without navigating the test runner.
  let href: string;

  beforeEach(() => {
    href = "";
    vi.stubGlobal("location", {
      get href() {
        return href;
      },
      set href(v: string) {
        href = v;
      },
    });
  });

  it("clears the token and redirects on a 401", async () => {
    tokenStore.set("expired-token");
    stubFetch({ error: { code: "INVALID_TOKEN", message: "nope", details: {} } }, { status: 401 });

    await apiRequest("/orders").catch(() => {});

    expect(tokenStore.get()).toBeNull();
    expect(href).toBe("/login");
  });

  it("does NOT redirect for an auth endpoint", async () => {
    /**
     * The important half. A wrong password is also a 401, and redirecting
     * there would reload the page instead of letting Login render "Incorrect
     * email or password" — so the user would see the form blink and no
     * explanation.
     */
    stubFetch(
      { error: { code: "INVALID_CREDENTIALS", message: "Incorrect email or password", details: {} } },
      { status: 401 },
    );

    const err = await rejection(apiRequest("/auth/login", { method: "POST", form: true, body: {} }));

    expect(href).toBe("");
    // ...and the error still reaches the caller, which is what Login renders.
    expect(err.message).toBe("Incorrect email or password");
  });

  it("clears the token even for an auth endpoint", async () => {
    // The dead token goes regardless, so the next startup does not retry it.
    tokenStore.set("stale");
    stubFetch({ error: { code: "INVALID_TOKEN", message: "x", details: {} } }, { status: 401 });
    await apiRequest("/auth/me").catch(() => {});
    expect(tokenStore.get()).toBeNull();
  });
});

describe("tokenStore", () => {
  it("round-trips through localStorage under a namespaced key", async () => {
    expect(tokenStore.get()).toBeNull();
    tokenStore.set("abc");
    expect(tokenStore.get()).toBe("abc");
    // Namespaced, so it cannot collide with another app on the same origin.
    expect(localStorage.getItem("routeos_token")).toBe("abc");
    tokenStore.clear();
    expect(tokenStore.get()).toBeNull();
  });
});
