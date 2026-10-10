// Chat ordering, the copilot panel's "Order" mode (S17; apps-ai-iot.md, Chat ordering). The AI
// service turns one message into a draft; this card shows every field for the user to check
// and edit. Only the user's own click sends POST /shortages (as the user, source=CHAT): the
// service never creates anything. The shortfall is the hub's to compute, shown after saving.
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { z } from "zod";
import {
  Badge,
  Button,
  FieldError,
  Input,
  Loading,
  NativeSelect,
  StatusChip,
  Textarea,
} from "@care-e/ui";
import { type Shortage, useCreateShortage, useFacilities, useProducts } from "../api";
import { FormField } from "../components/page";
import { qty } from "../display";
import { useForm } from "../forms";
import { shortageSchema } from "../pages/new-shortage";
import { type ChatDraftReply, type ProductCandidate, useChatDraft } from "./copilot-api";

/** ISO time -> the `datetime-local` value for it in the browser's zone. */
export function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** The required-by time in full, e.g. "Friday, 9 October 2026 at 23:59 IST". */
export function fullDateTime(local: string): string | null {
  const d = new Date(local);
  if (!local || Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("en-IN", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZoneName: "short",
  });
}

const NOT_GIVEN = "Not in your message. Please fill it in.";
const DEFAULTED = "Not in your message: a default is filled in. Please check it.";
const DEFAULTABLE = new Set(["priority", "min_shelf_life_days", "qty_local_usable"]);

function hintFor(field: string, missing: Set<string>) {
  if (!missing.has(field)) return undefined;
  return (
    <span
      className="font-medium text-amber-700 dark:text-amber-400"
      data-testid={`missing-${field}`}
    >
      {DEFAULTABLE.has(field) ? DEFAULTED : NOT_GIVEN}
    </span>
  );
}

function OrderCard({ reply, onCancel }: { reply: ChatDraftReply; onCancel: () => void }) {
  const draft = reply.draft!;
  const products = useProducts();
  const facilities = useFacilities();
  const create = useCreateShortage();
  const [saved, setSaved] = useState<Shortage>();
  const missing = new Set(reply.missing_fields);
  const form = useForm({
    product_id: draft.product_id ?? "",
    facility_id: "",
    qty_required: draft.qty_required === null ? "" : String(draft.qty_required),
    qty_local_usable: String(draft.qty_local_usable),
    required_by: toLocalInput(draft.required_by),
    priority: draft.priority,
    min_shelf_life_days:
      draft.min_shelf_life_days === null ? "" : String(draft.min_shelf_life_days),
    notes: draft.notes ?? "",
  });
  const only = facilities.data?.length === 1 ? facilities.data[0]?.id : undefined;

  /** Sets the product. A shelf life the message didn't state follows the product: its
   *  default, as the hub returned it (a candidate from the search, or the catalog). */
  const pick = (productId: string, defaultDays: number | undefined) => {
    form.set("product_id", productId);
    if (missing.has("min_shelf_life_days"))
      form.set("min_shelf_life_days", defaultDays === undefined ? "" : String(defaultDays));
  };
  const choose = (c: ProductCandidate) => pick(c.product_id, c.default_min_shelf_life_days);

  const save = (status: "OPEN" | "DRAFT") => (event?: FormEvent) => {
    event?.preventDefault();
    const body = form.parse(
      only ? shortageSchema.extend({ facility_id: z.string() }) : shortageSchema,
    );
    if (!body) return;
    create.mutate(
      { ...body, facility_id: only ?? body.facility_id, source: "CHAT", status },
      { onSuccess: setSaved, onError: (e) => form.setErrors({ form: e.message }) },
    );
  };

  if (saved) {
    const product = products.data?.byId.get(saved.product_id);
    return (
      <div
        role="status"
        className="grid gap-2 rounded-md bg-primary/5 p-3"
        data-testid="order-saved"
      >
        <p className="text-sm">
          {saved.status === "DRAFT" ? "Saved as a draft" : "Shortage created"}: {product?.name}{" "}
          <StatusChip state={saved.status} />
        </p>
        <p className="text-sm text-muted-foreground">Shortfall, computed by the hub</p>
        <p className="text-xl font-semibold text-primary" data-testid="shortfall">
          {qty(saved.shortfall, product)}
        </p>
        <div className="flex gap-2">
          <Button size="sm" asChild>
            <Link to={`/shortages/${saved.id}`}>View shortage</Link>
          </Button>
          <Button size="sm" variant="outline" onClick={onCancel}>
            New order
          </Button>
        </div>
      </div>
    );
  }
  if (products.isPending || facilities.isPending) return <Loading />;

  const full = fullDateTime(form.values.required_by);
  const chosen = products.data?.byId.get(form.values.product_id);
  const shelfDefaulted = hintFor("min_shelf_life_days", missing);
  const shelfDefault = chosen && (
    <span data-testid="shelf-life-default">
      Product default: {chosen.default_min_shelf_life_days} days.
    </span>
  );
  return (
    <form
      onSubmit={save("OPEN")}
      noValidate
      className="grid gap-3 rounded-md border p-3"
      aria-label="Shortage draft"
    >
      <p className="text-xs text-muted-foreground">
        A draft from your message. Check every field: nothing is sent until you click.
      </p>
      {reply.product_candidates.length > 0 && (
        <div className="grid gap-1.5" data-testid="product-choices">
          <p className="text-sm font-medium">Which product?</p>
          <div className="flex flex-wrap gap-2">
            {reply.product_candidates.map((c) => (
              <Button
                key={c.product_id}
                type="button"
                size="sm"
                variant={form.values.product_id === c.product_id ? "default" : "outline"}
                aria-pressed={form.values.product_id === c.product_id}
                onClick={() => choose(c)}
              >
                {c.name} ({c.code})
              </Button>
            ))}
          </div>
        </div>
      )}
      <FormField
        id="product_id"
        label="Product"
        error={form.errors.product_id}
        hint={hintFor("product_id", missing)}
      >
        <NativeSelect
          {...form.bind("product_id")}
          onChange={(e) =>
            pick(
              e.target.value,
              products.data?.byId.get(e.target.value)?.default_min_shelf_life_days,
            )
          }
        >
          <option value="">Choose…</option>
          {products.data?.list.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.code})
            </option>
          ))}
        </NativeSelect>
      </FormField>
      {!only && (
        <FormField id="facility_id" label="Facility" error={form.errors.facility_id}>
          <NativeSelect {...form.bind("facility_id")}>
            <option value="">Choose…</option>
            {facilities.data?.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </NativeSelect>
        </FormField>
      )}
      <div className="grid grid-cols-2 gap-3">
        <FormField
          id="qty_required"
          label="Quantity required"
          error={form.errors.qty_required}
          hint={hintFor("qty_required", missing)}
        >
          <Input inputMode="numeric" {...form.bind("qty_required")} />
        </FormField>
        <FormField
          id="qty_local_usable"
          label="Usable stock on hand"
          error={form.errors.qty_local_usable}
          hint={hintFor("qty_local_usable", missing)}
        >
          <Input inputMode="numeric" {...form.bind("qty_local_usable")} />
        </FormField>
      </div>
      <FormField
        id="required_by"
        label="Required by"
        error={form.errors.required_by}
        hint={hintFor("required_by", missing)}
      >
        <Input type="datetime-local" {...form.bind("required_by")} />
      </FormField>
      {full && (
        <p className="text-sm" data-testid="required-by-full">
          <span className="text-muted-foreground">Required by </span>
          <span className="font-medium">{full}</span>
          {draft.required_by_text && (
            <span className="text-muted-foreground"> (you wrote "{draft.required_by_text}")</span>
          )}
        </p>
      )}
      <div className="grid grid-cols-2 gap-3">
        <FormField
          id="priority"
          label="Priority"
          error={form.errors.priority}
          hint={hintFor("priority", missing)}
        >
          <NativeSelect {...form.bind("priority")}>
            <option value="CRITICAL">Critical</option>
            <option value="ROUTINE">Routine</option>
          </NativeSelect>
        </FormField>
        <FormField
          id="min_shelf_life_days"
          label="Min shelf life (days)"
          error={form.errors.min_shelf_life_days}
          hint={
            shelfDefaulted || shelfDefault ? (
              <>
                {shelfDefaulted} {shelfDefault}
              </>
            ) : undefined
          }
        >
          <Input inputMode="numeric" {...form.bind("min_shelf_life_days")} />
        </FormField>
      </div>
      <FormField id="notes" label="Notes (optional)">
        <Textarea rows={2} {...form.bind("notes")} />
      </FormField>
      {reply.assumptions.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-muted-foreground" data-testid="assumptions">
          {reply.assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      )}
      {form.errors.form && <FieldError>{form.errors.form}</FieldError>}
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={create.isPending}
          onClick={() => save("DRAFT")()}
        >
          Save as draft
        </Button>
        <Button type="submit" size="sm" disabled={create.isPending}>
          Create shortage
        </Button>
      </div>
    </form>
  );
}

/** Order mode: a message in, a card to check out. */
export function OrderMode() {
  const draft = useChatDraft();
  const [message, setMessage] = useState("");
  const [reply, setReply] = useState<ChatDraftReply>();
  const [error, setError] = useState<string>();
  const [round, setRound] = useState(0);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = message.trim();
    if (!text || draft.isPending) return;
    setError(undefined);
    try {
      setReply(await draft.mutateAsync(text));
      setRound((r) => r + 1);
    } catch (e) {
      setReply(undefined);
      setError(e instanceof Error ? e.message : "The order could not be drafted.");
    }
  };
  const reset = () => {
    setReply(undefined);
    setMessage("");
  };

  return (
    <>
      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3 text-sm" aria-live="polite">
        {!reply && !draft.isPending && !error && (
          <p className="text-muted-foreground">
            Describe what you need, e.g. "need 850 SK-A by fri for ICU". You get a draft to check;
            nothing is created until you confirm it.
          </p>
        )}
        {draft.isPending && <Loading label="Drafting…" />}
        {error && (
          <p role="alert" className="text-destructive">
            {error}
          </p>
        )}
        {reply?.question && (
          <p role="status" className="rounded-md bg-muted p-2" data-testid="order-question">
            {reply.question}
          </p>
        )}
        {reply && reply.tool_trace.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted-foreground">Based on:</span>
            {reply.tool_trace.map((t, i) => (
              <Badge key={i} variant={t.ok ? "secondary" : "outline"}>
                {t.label}
              </Badge>
            ))}
          </div>
        )}
        {reply?.draft && <OrderCard key={round} reply={reply} onCancel={reset} />}
      </div>
      <form onSubmit={submit} className="space-y-2 border-t px-4 py-3">
        <Textarea
          aria-label="Order message"
          value={message}
          maxLength={2000}
          rows={2}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) void submit(e);
          }}
        />
        <div className="flex justify-end">
          <Button type="submit" size="sm" disabled={!message.trim() || draft.isPending}>
            Draft order
          </Button>
        </div>
      </form>
    </>
  );
}
