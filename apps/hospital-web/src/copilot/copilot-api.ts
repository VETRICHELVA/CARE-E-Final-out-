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

// ---- Chat ordering (S17) ----

/** The card's pre-filled fields. A draft only: nothing reaches the hub until the user clicks
 *  "Create shortage" or "Save as draft", which send POST /shortages as the user. */
export type ChatDraft = {
  product_id: string | null;
  product_code: string | null;
  product_name: string | null;
  unit: string | null;
  qty_required: number | null;
  qty_local_usable: number;
  /** ISO 8601 with the user's offset. */
  required_by: string | null;
  /** e.g. "Friday 9 October 2026, 23:59 (Asia/Kolkata)". */
  required_by_display: string | null;
  required_by_text: string | null;
  priority: "CRITICAL" | "ROUTINE";
  min_shelf_life_days: number | null;
  notes: string | null;
};

export type ProductCandidate = {
  product_id: string;
  code: string;
  name: string;
  unit: string;
  default_min_shelf_life_days: number;
  score: number;
};

export type ChatDraftReply = {
  draft: ChatDraft | null;
  /** Fields the message did not give, defaulted ones included; the card highlights them. */
  missing_fields: string[];
  /** Set when the product is ambiguous: the user picks; the service never does. */
  product_candidates: ProductCandidate[];
  question: string | null;
  assumptions: string[];
  tool_trace: TraceEntry[];
};

/** The browser's IANA zone, which relative dates ("by Friday") are resolved in. */
export const userTimeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

/** POST /chat/draft: a pre-filled shortage card from one message. */
export function useChatDraft() {
  return useMutation({
    mutationFn: async (message: string) =>
      (await call("/chat/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, user_tz: userTimeZone() }),
      })) as ChatDraftReply,
  });
}
