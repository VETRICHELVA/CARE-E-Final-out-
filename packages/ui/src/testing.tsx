// Test harness for the apps' Vitest suites (moved from hospital-web in S10): a fake hub behind
// `fetch`, and a renderer with a signed-in user. Import from "@care-e/ui/testing", in tests only.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { vi } from "vitest";
import { type Me, useAuth } from "@care-e/api-client";

type Reply = unknown;
type Handler = (call: Call) => Reply | Promise<Reply>;
export type Call = {
  method: string;
  path: string;
  url: URL;
  body: string | undefined;
  headers: Headers;
};

/** `{code, message}` with an HTTP status, as the hub sends errors. */
export const hubError = (status: number, code: string, message: string) =>
  Response.json({ code, message, details: {} }, { status });

const toRegex = (pattern: string) => new RegExp(`^${pattern.replace(/\{[^}]+\}/g, "[^/]+")}$`);

/** Routes like `"GET /api/v1/shortages/{id}"` → a JSON reply, a Response, or a handler. */
export function fakeHub(routes: Record<string, Reply | Handler>) {
  const calls: Call[] = [];
  const table = Object.entries(routes).map(([key, reply]) => ({ re: toRegex(key), reply }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const call: Call = {
        method: request.method,
        path: url.pathname,
        url,
        body: request.method === "GET" ? undefined : await request.text(),
        headers: request.headers,
      };
      calls.push(call);
      const route = table.find((r) => r.re.test(`${call.method} ${call.path}`));
      if (!route) return hubError(404, "not_found", `No fake for ${call.method} ${call.path}`);
      const out = typeof route.reply === "function" ? await route.reply(call) : route.reply;
      return out instanceof Response ? out : Response.json(out);
    }),
  );
  return {
    calls,
    /** The calls made to `method path` (exact path). */
    to: (method: string, path: string) =>
      calls.filter((c) => c.method === method && c.path === path),
  };
}

/** Shows where a link took the user. */
function Where() {
  const location = useLocation();
  return <p data-testid="location">{location.pathname}</p>;
}

/** Renders `ui` at `path` (matched by `route`) as `me`, with `/auth/me` already cached. */
export function renderAs(
  me: Me,
  ui: ReactNode,
  { path = "/", route = path }: { path?: string; route?: string } = {},
) {
  useAuth.setState({ tokens: { access: "a", refresh: "r" } });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(["/api/v1/auth/me"], me);
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={route} element={ui} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return queryClient;
}
