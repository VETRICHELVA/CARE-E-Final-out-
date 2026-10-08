// The copilot's calls to the AI service (S13; services/ai-service/README.md), reached through
// the `/ai` dev proxy. The service is not the hub, so it has no generated client; requests go
// through the api-client's `authFetch`, which sends the signed-in user's hub token (refreshing
// it once on a 401). The service only reads the hub, as this user.
import { useMutation, useQuery } from "@tanstack/react-query";
import { authFetch } from "@care-e/api-client";

export const AI_BASE = "/ai";

export type CopilotContext = {
  shortage_id?: string;
  recommendation_id?: string;
  shipment_id?: string;
};

export type TraceEntry = {
  tool: string;
  input: Record<string, unknown>;
  ok: boolean;
  /** The "Based on:" chip, e.g. "match run #3". */
  label: string;
  result: unknown;
  from_context: boolean;
};

export type CopilotAnswer = { answer: string; tool_trace: TraceEntry[] };

/** Why the copilot cannot answer: no key on the service, the service is down, or another error. */
export class CopilotError extends Error {
  constructor(
    readonly kind: "not_configured" | "unreachable" | "failed",
    message: string,
  ) {
    super(message);
  }
}

export const NOT_CONFIGURED = "AI is not configured.";
const UNREACHABLE = "The AI service is not reachable right now.";

async function call(path: string, init?: RequestInit): Promise<unknown> {
  let response: Response;
  try {
    response = await authFetch(new Request(new URL(AI_BASE + path, location.origin), init));
  } catch {
    throw new CopilotError("unreachable", UNREACHABLE);
  }
  const body: unknown = await response.json().catch(() => null);
  const error = (body ?? {}) as { error?: string; message?: string };
  if (error.error === "ai_not_configured") throw new CopilotError("not_configured", NOT_CONFIGURED);
  if (!response.ok) {
    // A proxy with nothing behind it answers 5xx without our JSON.
    if (body === null) throw new CopilotError("unreachable", UNREACHABLE);
    throw new CopilotError(
      "failed",
      error.message ?? `The AI service returned ${response.status}.`,
    );
  }
  return body;
}

/** Whether the service has a model configured; fetched only while the panel is open. */
export function useCopilotStatus(enabled: boolean) {
  return useQuery({
    queryKey: ["/ai/status"],
    queryFn: async () => {
      const status = (await call("/status")) as { configured: boolean; model: string | null };
      if (!status.configured) throw new CopilotError("not_configured", NOT_CONFIGURED);
      return status;
    },
    enabled,
    retry: false,
    staleTime: 60_000,
  });
}

/** POST /copilot/ask: an answer built only from hub reads, with the reads it made. */
export function useAskCopilot() {
  return useMutation({
    mutationFn: async (body: { question: string; context: CopilotContext }) =>
      (await call("/copilot/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })) as CopilotAnswer,
  });
}
