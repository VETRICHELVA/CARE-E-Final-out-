// The copilot panel on every hospital-web screen (S13; apps-ai-iot.md, Copilot). It sends the
// question with the ids of the screen the user is on, and shows the answer with "Based on:"
// chips from the service's tool trace. It reads only; it never offers an action.
import { type FormEvent, useState } from "react";
import { matchPath, useLocation } from "react-router";
import { Badge, Button, Loading, Textarea, useCan } from "@care-e/ui";
import {
  type CopilotContext,
  CopilotError,
  type TraceEntry,
  useAskCopilot,
  useCopilotStatus,
} from "./copilot-api";
import { OrderMode } from "./order-panel";

/** Screen routes whose id the copilot gets as context. */
const SCREENS: { pattern: string; key: keyof CopilotContext; about: string }[] = [
  { pattern: "/shortages/:id", key: "shortage_id", about: "this shortage" },
  { pattern: "/deliveries/:id/*", key: "shipment_id", about: "this delivery" },
];

/** The current screen's ids, e.g. `{shortage_id}` on /shortages/:id. */
export function useScreenContext(): { context: CopilotContext; about: string | null } {
  const { pathname } = useLocation();
  for (const screen of SCREENS) {
    const id = matchPath(screen.pattern, pathname)?.params.id;
    if (id) return { context: { [screen.key]: id }, about: screen.about };
  }
  return { context: {}, about: null };
}

type Exchange = { question: string; answer?: string; error?: string; trace: TraceEntry[] };

/** One chip per distinct record read; reads the user may not see are marked as such. */
function BasedOn({ trace }: { trace: TraceEntry[] }) {
  const chips = [...new Map(trace.map((t) => [`${t.ok}:${t.label}`, t])).values()];
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs" data-testid="based-on">
      <span className="text-muted-foreground">Based on:</span>
      {chips.map((t) => (
        <Badge key={`${t.ok}:${t.label}`} variant={t.ok ? "secondary" : "outline"}>
          {t.label}
        </Badge>
      ))}
    </div>
  );
}

function Conversation({ about, context }: { about: string | null; context: CopilotContext }) {
  const ask = useAskCopilot();
  const [question, setQuestion] = useState("");
  const [log, setLog] = useState<Exchange[]>([]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = question.trim();
    if (!text || ask.isPending) return;
    setQuestion("");
    try {
      const out = await ask.mutateAsync({ question: text, context });
      setLog((l) => [...l, { question: text, answer: out.answer, trace: out.tool_trace }]);
    } catch (e) {
      const message = e instanceof Error ? e.message : "The copilot could not answer.";
      setLog((l) => [...l, { question: text, error: message, trace: [] }]);
    }
  };

  return (
    <>
      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-3 text-sm" aria-live="polite">
        {log.length === 0 && !ask.isPending && (
          <p className="text-muted-foreground">
            Ask why something happened{about ? ` to ${about}` : ""}, e.g. "Why was Hospital D
            rejected?"
          </p>
        )}
        {log.map((x, i) => (
          <div key={i} className="space-y-1.5" data-testid="exchange">
            <p className="font-medium">{x.question}</p>
            {x.answer !== undefined && <p data-testid="answer">{x.answer}</p>}
            {x.error && (
              <p role="alert" className="text-destructive">
                {x.error}
              </p>
            )}
            <BasedOn trace={x.trace} />
          </div>
        ))}
        {ask.isPending && <Loading label="Reading the hub…" />}
      </div>
      <form onSubmit={submit} className="space-y-2 border-t px-4 py-3">
        {about && <p className="text-xs text-muted-foreground">About {about}</p>}
        <Textarea
          aria-label="Question for the copilot"
          value={question}
          maxLength={2000}
          rows={2}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) void submit(e);
          }}
        />
        <div className="flex justify-end">
          <Button type="submit" size="sm" disabled={!question.trim() || ask.isPending}>
            Ask
          </Button>
        </div>
      </form>
    </>
  );
}

/** A button that opens the panel; the AI service is contacted only once it is open. "Order"
 *  mode (S17, chat ordering) shows only to users who may create shortages. */
export function CopilotPanel() {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"ask" | "order">("ask");
  const canOrder = useCan("shortage.create");
  const { context, about } = useScreenContext();
  const status = useCopilotStatus(open);
  const ordering = canOrder && mode === "order";

  if (!open)
    return (
      <Button className="fixed right-4 bottom-4 z-40 shadow-lg" onClick={() => setOpen(true)}>
        Ask copilot
      </Button>
    );

  const error = status.error instanceof CopilotError ? status.error : null;
  return (
    <section
      aria-label="Copilot"
      className="fixed right-4 bottom-4 z-40 flex max-h-[70svh] w-[min(26rem,calc(100vw-2rem))] flex-col rounded-xl border bg-card text-card-foreground shadow-lg"
    >
      <header className="flex items-start justify-between gap-2 border-b px-4 py-3">
        <div>
          <h2 className="font-semibold">Copilot</h2>
          <p className="text-xs text-muted-foreground">
            {ordering
              ? "Drafts a shortage for you to check. Nothing is sent until you click."
              : "Answers only from hub data. It cannot change anything."}
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
          Close
        </Button>
      </header>
      {canOrder && (
        <div className="flex gap-1 border-b px-4 py-2" role="group" aria-label="Copilot mode">
          {(["ask", "order"] as const).map((m) => (
            <Button
              key={m}
              size="sm"
              variant={mode === m ? "secondary" : "ghost"}
              aria-pressed={mode === m}
              onClick={() => setMode(m)}
            >
              {m === "ask" ? "Ask" : "Order"}
            </Button>
          ))}
        </div>
      )}
      {status.isPending ? (
        <Loading />
      ) : error || status.error ? (
        <p role="status" className="px-4 py-3 text-sm text-muted-foreground">
          {error?.message ?? "The copilot is not available right now."}
        </p>
      ) : ordering ? (
        <OrderMode />
      ) : (
        <Conversation about={about} context={context} />
      )}
    </section>
  );
}
