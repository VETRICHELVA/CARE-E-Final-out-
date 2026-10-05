// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, client, login, unwrap, useAuth } from "./index";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const pair = (n: number) => ({
  access_token: `access-${n}`,
  refresh_token: `refresh-${n}`,
  token_type: "bearer",
  expires_in: 900,
});

/** A fake hub: /auth/me accepts only `access-2`; /auth/refresh accepts only `refresh-1`. */
function fakeHub() {
  const calls: string[] = [];
  const fetch = vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    calls.push(`${path} ${request.headers.get("Authorization") ?? "-"}`);
    if (path.endsWith("/auth/refresh")) {
      const { refresh_token } = (await request.json()) as { refresh_token: string };
      return refresh_token === "refresh-1"
        ? json(200, pair(2))
        : json(401, { code: "unauthenticated", message: "Refresh token was revoked." });
    }
    if (path.endsWith("/auth/login")) {
      return json(401, { code: "invalid_credentials", message: "Email or password is incorrect." });
    }
    return request.headers.get("Authorization") === "Bearer access-2"
      ? json(200, { ok: true })
      : json(401, { code: "unauthenticated", message: "Access token expired." });
  });
  vi.stubGlobal("fetch", fetch);
  return calls;
}

beforeEach(() => useAuth.setState({ tokens: { access: "access-1", refresh: "refresh-1" } }));
afterEach(() => vi.unstubAllGlobals());

describe("api client auth", () => {
  it("sends the access token, refreshes once on 401 and retries", async () => {
    const calls = fakeHub();
    const { data } = await client.GET("/api/v1/auth/me");
    expect(data).toEqual({ ok: true });
    expect(calls).toEqual([
      "/api/v1/auth/me Bearer access-1",
      "/api/v1/auth/refresh Bearer access-1",
      "/api/v1/auth/me Bearer access-2",
    ]);
    expect(useAuth.getState().tokens).toEqual({ access: "access-2", refresh: "refresh-2" });
  });

  it("shares one refresh between parallel 401s", async () => {
    const calls = fakeHub();
    const results = await Promise.all([
      client.GET("/api/v1/auth/me"),
      client.GET("/api/v1/auth/me"),
    ]);
    expect(results.map((r) => r.response.status)).toEqual([200, 200]);
    expect(calls.filter((c) => c.includes("/auth/refresh"))).toHaveLength(1);
  });

  it("signs out when the refresh fails", async () => {
    useAuth.setState({ tokens: { access: "access-1", refresh: "stale" } });
    fakeHub();
    const { response } = await client.GET("/api/v1/auth/me");
    expect(response.status).toBe(401);
    expect(useAuth.getState().tokens).toBeNull();
  });

  it("never refreshes on a failed login and surfaces the hub's message", async () => {
    const calls = fakeHub();
    const error = await login("x@y.z", "wrong").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 401, code: "invalid_credentials" });
    expect(calls).toHaveLength(1);
  });

  it("unwrap returns data on success", async () => {
    fakeHub();
    useAuth.setState({ tokens: { access: "access-2", refresh: "refresh-2" } });
    await expect(unwrap(client.GET("/api/v1/auth/me"))).resolves.toEqual({ ok: true });
  });
});
