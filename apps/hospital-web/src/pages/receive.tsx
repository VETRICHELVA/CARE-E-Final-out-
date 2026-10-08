// Receive (apps-ai-iot.md, hospital-web; S12): what actually arrived on a delivered shipment.
// Expected is the hub's (the shipment's qty) and read-only. The form checks the figures'
// shape and business-rules §9's invariants before sending, but the hub decides: its 400s are
// shown as they come, and the reconciliation outcome it returns is shown as is (nothing here is
// computed from the figures). Needs `receipt.record`; only the receiving org may record one.
import { type FormEvent, type ReactNode, useState } from "react";
import { Link, useParams } from "react-router";
import { z } from "zod";
import { ApiError } from "@care-e/api-client";
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  FieldError,
  formatDateTime,
  Input,
  Loading,
  NativeSelect,
  StatusChip,
  Textarea,
  toast,
  useCan,
  useMe,
} from "@care-e/ui";
import {
  type Product,
  type Receipt,
  type ShipmentDetail,
  useProducts,
  useRecordReceipt,
  useShipment,
  useShortage,
} from "../api";
import { FormField, PageHeader } from "../components/page";
import { CONDITION_LABELS, qty, SHORTAGE_READERS } from "../display";
import { count, optionalText, useForm } from "../forms";

const CONDITIONS = ["GOOD", "DAMAGED", "TEMPERATURE_ISSUE"] as const;

/** The receipt form for a shipment of `expected` units. business-rules §9: accepted + rejected
 *  = received, received ≤ expected; the hub needs an expiry date for accepted stock, and an
 *  inspection note while a cold-chain excursion is open (`inspection_note_required`). */
export function receiptSchema(expected: number, noteRequired: boolean) {
  return z
    .object({
      received: count("the quantity received"),
      accepted: count("the quantity accepted"),
      rejected: count("the quantity rejected"),
      condition: z.enum(CONDITIONS, "Choose the condition."),
      expiry_date: optionalText,
      batch_no: optionalText.refine(
        (s) => s === undefined || s.length <= 64,
        "Use at most 64 characters.",
      ),
      inspection_note: optionalText,
      reason: optionalText,
    })
    .superRefine((v, ctx) => {
      if (v.received > expected)
        ctx.addIssue({
          code: "custom",
          path: ["received"],
          message: `Received cannot be more than the ${expected} expected.`,
        });
      if (v.accepted + v.rejected !== v.received)
        ctx.addIssue({
          code: "custom",
          path: ["rejected"],
          message: "Accepted and rejected must add up to the quantity received.",
        });
      if (v.accepted > 0 && !v.expiry_date)
        ctx.addIssue({
          code: "custom",
          path: ["expiry_date"],
          message: "Enter the expiry date of the accepted stock.",
        });
      if (noteRequired && !v.inspection_note)
        ctx.addIssue({
          code: "custom",
          path: ["inspection_note"],
          message: "Enter an inspection note: this shipment had a cold-chain excursion.",
        });
    });
}

/** Hub 400 `details.reason` → the field it is about. */
const REASON_FIELDS: Record<string, string> = {
  expiry_date_required: "expiry_date",
  inspection_note_required: "inspection_note",
};

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium">{children}</dd>
    </div>
  );
}

/** The residual shortage the hub opened, for users who may read shortages. */
function ResidualShortage({ id, product }: { id: string; product: Product | undefined }) {
  const residual = useShortage(id);
  return (
    <p className="text-sm" data-testid="residual">
      {residual.data ? (
        <>
          A residual shortage of{" "}
          <span className="font-medium">{qty(residual.data.shortfall, product)}</span> was opened (
          <StatusChip state={residual.data.status} />
          ).{" "}
        </>
      ) : (
        "A residual shortage was opened for the rest. "
      )}
      <Link to={`/shortages/${id}`} className="font-medium text-primary hover:underline">
        View residual shortage
      </Link>
    </p>
  );
}

/** What was recorded and how the hub reconciled it. */
function Outcome({ receipt, product }: { receipt: Receipt; product: Product | undefined }) {
  const canReadShortages = useCan(SHORTAGE_READERS);
  const rec = receipt.reconciliation;
  let outcome: ReactNode;
  if (!rec)
    outcome = (
      <p className="text-sm text-muted-foreground" data-testid="outcome">
        Not reconciled yet: the hub reconciles the shortage once every shipment for it has been
        received.
      </p>
    );
  else if (rec.outcome === "CONFIRMED")
    outcome = (
      <p
        className="rounded-md bg-status-success-bg p-3 text-sm font-medium text-status-success"
        data-testid="outcome"
      >
        Reconciled: the shortage is resolved.
      </p>
    );
  else
    outcome = (
      <div className="grid gap-2">
        <p
          className="rounded-md bg-status-warning-bg p-3 text-sm font-medium text-status-warning"
          data-testid="outcome"
        >
          Reconciled: the shortage is partially resolved. This delivery was{" "}
          {qty(rec.discrepancy, product)} short of the {qty(rec.expected, product)} expected.
        </p>
        {rec.residual_shortage_id &&
          (canReadShortages ? (
            <ResidualShortage id={rec.residual_shortage_id} product={product} />
          ) : (
            <p className="text-sm" data-testid="residual">
              A residual shortage was opened for the rest; your organization&apos;s shortage
              managers can see it.
            </p>
          ))}
      </div>
    );
  return (
    <Card data-testid="receipt-outcome">
      <CardHeader>
        <CardTitle>Receipt recorded</CardTitle>
        <CardDescription>{formatDateTime(receipt.ts)}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <Fact label="Expected">{qty(receipt.expected, product)}</Fact>
          <Fact label="Received">{qty(receipt.received, product)}</Fact>
          <Fact label="Accepted">{qty(receipt.accepted, product)}</Fact>
          <Fact label="Rejected">{qty(receipt.rejected, product)}</Fact>
          <Fact label="Condition">{CONDITION_LABELS[receipt.condition] ?? receipt.condition}</Fact>
          {receipt.inspection_note && (
            <Fact label="Inspection note">{receipt.inspection_note}</Fact>
          )}
        </dl>
        {outcome}
      </CardContent>
    </Card>
  );
}

function ReceiptForm({
  shipment,
  product,
  onSaved,
}: {
  shipment: ShipmentDetail;
  product: Product | undefined;
  onSaved: (receipt: Receipt) => void;
}) {
  const record = useRecordReceipt(shipment);
  const noteRequired = shipment.inspection_note_required;
  const form = useForm({
    received: "",
    accepted: "",
    rejected: "",
    condition: "",
    expiry_date: "",
    batch_no: "",
    inspection_note: "",
    reason: "",
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body = form.parse(receiptSchema(shipment.qty, noteRequired));
    if (!body) return;
    record.mutate(body, {
      onSuccess: (receipt) => {
        toast.success("Receipt recorded.");
        onSaved(receipt);
      },
      onError: (e) => {
        const reason = e instanceof ApiError ? e.details.reason : undefined;
        const field = typeof reason === "string" ? REASON_FIELDS[reason] : undefined;
        form.setErrors({ [field ?? "form"]: e.message });
      },
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Record what arrived</CardTitle>
        <CardDescription>
          Count what was received, then split it into accepted and rejected.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} noValidate className="grid gap-4" aria-label="Receipt">
          <dl>
            <Fact label="Expected (from the shipment)">
              <span data-testid="expected">{qty(shipment.qty, product)}</span>
            </Fact>
          </dl>
          <div className="grid gap-4 sm:grid-cols-3">
            <FormField id="received" label="Received" error={form.errors.received}>
              <Input inputMode="numeric" {...form.bind("received")} />
            </FormField>
            <FormField id="accepted" label="Accepted" error={form.errors.accepted}>
              <Input inputMode="numeric" {...form.bind("accepted")} />
            </FormField>
            <FormField id="rejected" label="Rejected" error={form.errors.rejected}>
              <Input inputMode="numeric" {...form.bind("rejected")} />
            </FormField>
          </div>
          <FormField id="condition" label="Condition" error={form.errors.condition}>
            <NativeSelect {...form.bind("condition")}>
              <option value="">Choose…</option>
              {CONDITIONS.map((c) => (
                <option key={c} value={c}>
                  {CONDITION_LABELS[c]}
                </option>
              ))}
            </NativeSelect>
          </FormField>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              id="expiry_date"
              label="Expiry date of accepted stock"
              error={form.errors.expiry_date}
              hint="Required when anything is accepted: it becomes a new batch in your inventory."
            >
              <Input type="date" {...form.bind("expiry_date")} />
            </FormField>
            <FormField
              id="batch_no"
              label="Batch number (optional)"
              error={form.errors.batch_no}
              hint="Leave blank for the hub's own number."
            >
              <Input {...form.bind("batch_no")} />
            </FormField>
          </div>
          <FormField
            id="inspection_note"
            label={noteRequired ? "Inspection note (required)" : "Inspection note (optional)"}
            error={form.errors.inspection_note}
            hint={
              noteRequired
                ? "This shipment had a cold-chain excursion: describe what you inspected."
                : undefined
            }
          >
            <Textarea {...form.bind("inspection_note")} aria-required={noteRequired || undefined} />
          </FormField>
          <FormField id="reason" label="Reason (optional)">
            <Textarea {...form.bind("reason")} />
          </FormField>
          {form.errors.form && <FieldError>{form.errors.form}</FieldError>}
          <div className="flex justify-end">
            <Button type="submit" disabled={record.isPending}>
              {record.isPending ? "Recording…" : "Record receipt"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export function ReceivePage() {
  const id = useParams().id ?? "";
  const canReceive = useCan("receipt.record");
  const me = useMe().data;
  const shipment = useShipment(id, canReceive);
  const products = useProducts();
  // The receipt the hub just returned, until the shipment's refetch carries it.
  const [saved, setSaved] = useState<Receipt>();

  const back = (
    <Link to="/deliveries" className="text-sm text-muted-foreground hover:underline">
      ← Deliveries
    </Link>
  );
  if (!canReceive)
    return (
      <div className="grid gap-4">
        {back}
        <EmptyState title="You can't record receipts">
          Receipts are recorded by your hospital&apos;s receivers.
        </EmptyState>
      </div>
    );
  if (shipment.isPending) return <Loading label="Loading delivery…" />;
  if (shipment.isError) return <ErrorState error={shipment.error} />;

  const s = shipment.data;
  const product = products.data?.byId.get(s.product_id);
  const receipt = saved ?? s.receipt;
  let body: ReactNode;
  if (s.to_org_id !== me?.org.id)
    body = (
      <EmptyState title="This shipment is not coming to your hospital">
        Only the receiving organization records a receipt.
      </EmptyState>
    );
  else if (receipt) body = <Outcome receipt={receipt} product={product} />;
  else if (s.status !== "DELIVERED")
    body = (
      <EmptyState title="Not delivered yet">
        A receipt can be recorded once the driver marks the shipment delivered.
      </EmptyState>
    );
  else body = <ReceiptForm shipment={s} product={product} onSaved={setSaved} />;

  return (
    <div className="grid gap-4">
      {back}
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            Receive {s.product_name} <StatusChip state={s.status} />
          </span>
        }
        description={`${qty(s.qty, product)} from ${s.from_org_name}`}
      />
      {body}
    </div>
  );
}
