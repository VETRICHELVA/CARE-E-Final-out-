// Hand-written wrapper around the GENERATED `schema.d.ts` (never edit that file; run `make client`).
import { QueryClient } from "@tanstack/react-query";
import createClient from "openapi-fetch";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { components, paths } from "./schema";

export type { components, paths };
export type Schemas = components["schemas"];
export type Me = Schemas["MeOut"];

// A 401 from these means "bad credentials or session", never "access token expired".
const NO_REFRESH = /\/auth\/(login|refresh|logout)$/;

type Tokens = { access: string; refresh: string };
type AuthState = { tokens: Tokens | null };

/** Tokens live per browser tab: refresh tokens are single-use, so tabs sharing one would race. */
export const useAuth = create<AuthState>()(
  persist<AuthState>(() => ({ tokens: null }), {
    name: "care-e-auth",
    storage: createJSONStorage(() => sessionStorage),
  }),
);

const setTokens = (pair: Schemas["TokenPair"] | null) =>
  useAuth.setState({
    tokens: pair && { access: pair.access_token, refresh: pair.refresh_token },
  });

const accessToken = () => useAuth.getState().tokens?.access;

function withToken(request: Request): Request {
  const token = accessToken();
  if (token) request.headers.set("Authorization", `Bearer ${token}`);
  return request;
}

let refreshing: Promise<boolean> | undefined;

/** One refresh at a time: a second use of a refresh token makes the hub revoke the session. */
function refreshOnce(): Promise<boolean> {
  refreshing ??= (async () => {
    const refresh_token = useAuth.getState().tokens?.refresh;
    if (!refresh_token) return false;
    const { data } = await client.POST("/api/v1/auth/refresh", { body: { refresh_token } });
    setTokens(data ?? null);
    return data !== undefined;
  })().finally(() => (refreshing = undefined));
  return refreshing;
}

/** Adds the access token; on a 401 refreshes once and retries the request once. Exported for
 *  the AI service (hospital-web's copilot), which takes the same signed-in user's token. */
export async function authFetch(request: Request): Promise<Response> {
  const retry = request.clone();
  const sent = accessToken();
  const response = await fetch(withToken(request));
  if (response.status !== 401 || NO_REFRESH.test(new URL(request.url).pathname)) {
    return response;
  }
  // Another request may already have refreshed while this one was in flight.
  if (accessToken() === sent && !(await refreshOnce())) return response;
  return fetch(withToken(retry));
}

export const client = createClient<paths>({
  baseUrl: globalThis.location?.origin,
  fetch: authFetch,
});

/** The hub's error body: `{code, message, details}` (api-and-events.md, Conventions). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

type Result = { data?: unknown; error?: unknown; response: Response };

/** Returns `data`, or throws an ApiError carrying the hub's code and message. */
export async function unwrap<R extends Result>(call: Promise<R>): Promise<NonNullable<R["data"]>> {
  const { data, error, response } = await call;
  if (!response.ok) {
    const body = (error ?? {}) as {
      code?: string;
      message?: string;
      details?: Record<string, unknown>;
    };
    throw new ApiError(
      response.status,
      body.code ?? "internal_error",
      body.message ?? response.statusText,
      body.details,
    );
  }
  return data as NonNullable<R["data"]>;
}

export async function login(email: string, password: string): Promise<void> {
  setTokens(await unwrap(client.POST("/api/v1/auth/login", { body: { email, password } })));
}

/** Forgets the tokens first, so the user is signed out even if the hub can't be reached. */
export async function logout(): Promise<void> {
  const refresh_token = useAuth.getState().tokens?.refresh;
  setTokens(null);
  if (refresh_token) {
    await client.POST("/api/v1/auth/logout", { body: { refresh_token } }).catch(() => undefined);
  }
}

/** Client errors (4xx) are answers from the hub, not glitches: don't retry them. */
export const createQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failures, error) =>
          !(error instanceof ApiError && error.status < 500) && failures < 2,
      },
    },
  });

export {
  backoffMs,
  connectEventStream,
  EVENT_QUERIES,
  type EventEnvelope,
  invalidateFor,
  isStale,
  onHubEvent,
  type StreamOptions,
  useEventStream,
} from "./events";
