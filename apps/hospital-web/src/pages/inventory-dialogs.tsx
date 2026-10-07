import { type FormEvent, useState } from "react";
import { z } from "zod";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  FieldError,
  Input,
  Loading,
  NativeSelect,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Textarea,
  toast,
} from "@care-e/ui";
import type { Schemas } from "@care-e/api-client";
import {
  type Batch,
  type Product,
  useFacilities,
  useImportBatches,
  useUpdateBatch,
  useVerifyBatch,
} from "../api";
import { FormField } from "../components/page";
import { qty } from "../display";
import { count, optionalText, paiseToRupees, rupees, useForm } from "../forms";

type DialogProps = { onClose: () => void };

function FormDialog({
  title,
  description,
  onClose,
  children,
}: DialogProps & { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  );
}

// ---- Edit ----

const QTY_FIELDS = ["on_hand", "reserved", "allocated", "safety_stock", "quarantined"] as const;
const QTY_LABELS: Record<(typeof QTY_FIELDS)[number], string> = {
  on_hand: "On hand",
  reserved: "Reserved",
  allocated: "Allocated",
  safety_stock: "Safety stock",
  quarantined: "Quarantined",
};

const editSchema = z.object({
  batch_no: z
    .string()
    .trim()
    .min(1, "Enter a batch number.")
    .max(64, "Use 64 characters or fewer."),
  on_hand: count("the quantity on hand"),
  reserved: count("the reserved quantity"),
  allocated: count("the allocated quantity"),
  safety_stock: count("the safety stock"),
  quarantined: count("the quarantined quantity"),
  expiry_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Choose an expiry date."),
  unit_cost_paise: rupees,
  reason: optionalText,
});

/** Edits a batch's recorded figures. Transferable is not an input: the hub recomputes it. */
export function EditBatchDialog({
  batch,
  product,
  onClose,
}: DialogProps & { batch: Batch; product: Product | undefined }) {
  const update = useUpdateBatch();
  const form = useForm({
    batch_no: batch.batch_no,
    on_hand: String(batch.on_hand),
    reserved: String(batch.reserved),
    allocated: String(batch.allocated),
    safety_stock: String(batch.safety_stock),
    quarantined: String(batch.quarantined),
    expiry_date: batch.expiry_date,
    unit_cost_paise: paiseToRupees(batch.unit_cost_paise),
    reason: "",
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = form.parse(editSchema);
    if (!parsed) return;
    const { reason, ...fields } = parsed;
    // Send only what changed, so a stale form can't overwrite another user's edit.
    const changed = Object.fromEntries(
      Object.entries(fields).filter(([k, v]) => batch[k as keyof typeof fields] !== v),
    ) as Schemas["BatchUpdate"];
    if (Object.keys(changed).length === 0) {
      form.setErrors({ form: "Nothing has changed." });
      return;
    }
    update.mutate(
      { id: batch.id, body: { ...changed, reason } },
      {
        onSuccess: () => {
          toast.success(`Batch ${batch.batch_no} updated.`);
          onClose();
        },
        onError: (e) => form.setErrors({ form: e.message }),
      },
    );
  };

  return (
    <FormDialog
      title={`Edit batch ${batch.batch_no}`}
      description={product?.name}
      onClose={onClose}
    >
      <form onSubmit={submit} noValidate className="grid gap-4">
        <p className="rounded-md bg-primary/5 p-3 text-sm">
          Transferable now:{" "}
          <span className="font-semibold text-primary">{qty(batch.transferable, product)}</span>.
          The hub recomputes it when you save.
        </p>
        <FormField id="batch_no" label="Batch number" error={form.errors.batch_no}>
          <Input {...form.bind("batch_no")} />
        </FormField>
        <div className="grid grid-cols-2 gap-4">
          {QTY_FIELDS.map((f) => (
            <FormField key={f} id={f} label={QTY_LABELS[f]} error={form.errors[f]}>
              <Input inputMode="numeric" {...form.bind(f)} />
            </FormField>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-4">
          <FormField id="expiry_date" label="Expiry date" error={form.errors.expiry_date}>
            <Input type="date" {...form.bind("expiry_date")} />
          </FormField>
          <FormField id="unit_cost_paise" label="Unit cost (₹)" error={form.errors.unit_cost_paise}>
            <Input inputMode="decimal" {...form.bind("unit_cost_paise")} />
          </FormField>
        </div>
        <FormField id="reason" label="Reason (optional)">
          <Textarea {...form.bind("reason")} />
        </FormField>
        {form.errors.form && <FieldError>{form.errors.form}</FieldError>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={update.isPending}>
            {update.isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </form>
    </FormDialog>
  );
}

// ---- Verify ----

const verifySchema = z.object({
  method: z.enum(["MANUAL", "SCAN"], "Choose how you counted."),
  counted_qty: count("the counted quantity"),
  reason: optionalText,
});

/** Records a physical count. The hub decides what the count changes. */
export function VerifyBatchDialog({
  batch,
  product,
  onClose,
}: DialogProps & { batch: Batch; product: Product | undefined }) {
  const verify = useVerifyBatch();
  const form = useForm({ method: "", counted_qty: "", reason: "" });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body = form.parse(verifySchema);
    if (!body) return;
    verify.mutate(
      { id: batch.id, body },
      {
        onSuccess: () => {
          toast.success(`Batch ${batch.batch_no} verified.`);
          onClose();
        },
        onError: (e) => form.setErrors({ form: e.message }),
      },
    );
  };

  return (
    <FormDialog
      title={`Verify batch ${batch.batch_no}`}
      description={`${product?.name ?? "Unknown product"}: recorded on hand ${qty(batch.on_hand, product)}.`}
      onClose={onClose}
    >
      <form onSubmit={submit} noValidate className="grid gap-4">
        <FormField id="method" label="Count method" error={form.errors.method}>
          <NativeSelect {...form.bind("method")}>
            <option value="">Choose…</option>
            <option value="MANUAL">Manual count</option>
            <option value="SCAN">Barcode scan</option>
          </NativeSelect>
        </FormField>
        <FormField
          id="counted_qty"
          label="Counted quantity"
          error={form.errors.counted_qty}
          hint="A count that differs from the recorded on hand replaces it."
        >
          <Input inputMode="numeric" {...form.bind("counted_qty")} />
        </FormField>
        <FormField id="reason" label="Reason (optional)">
          <Textarea {...form.bind("reason")} />
        </FormField>
        {form.errors.form && <FieldError>{form.errors.form}</FieldError>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={verify.isPending}>
            {verify.isPending ? "Saving…" : "Record count"}
          </Button>
        </DialogFooter>
      </form>
    </FormDialog>
  );
}

// ---- CSV import ----

const importSchema = z.object({
  facility_id: z.string().min(1, "Choose a facility."),
  reason: optionalText,
});

/** Uploads a CSV; the hub inserts each valid row and reports the others by line. */
export function ImportDialog({ onClose }: DialogProps) {
  const facilities = useFacilities();
  const upload = useImportBatches();
  const form = useForm({ facility_id: "", reason: "" });
  const [file, setFile] = useState<File>();
  const [result, setResult] = useState<Schemas["ImportResult"]>();

  // One facility: nothing to choose.
  const only = facilities.data?.length === 1 ? facilities.data[0]?.id : undefined;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = form.parse(
      only ? importSchema.extend({ facility_id: z.string() }) : importSchema,
    );
    if (!parsed) return;
    if (!file) {
      form.setErrors({ file: "Choose a CSV file." });
      return;
    }
    upload.mutate(
      { facilityId: only ?? parsed.facility_id, file, reason: parsed.reason },
      { onSuccess: setResult, onError: (e) => form.setErrors({ form: e.message }) },
    );
  };

  if (result) {
    return (
      <FormDialog title="Import finished" onClose={onClose}>
        <p className="text-sm" role="status">
          Imported {result.inserted} {result.inserted === 1 ? "batch" : "batches"}.
        </p>
        {result.errors.length > 0 && (
          <div className="grid gap-2">
            <p className="text-sm font-medium text-destructive">
              {result.errors.length} {result.errors.length === 1 ? "row was" : "rows were"} not
              imported:
            </p>
            <div className="max-h-64 overflow-y-auto rounded-md border">
              <Table aria-label="Rows not imported">
                <TableHeader>
                  <TableRow>
                    <TableHead>Line</TableHead>
                    <TableHead>Problem</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {result.errors.map((e) => (
                    <TableRow key={e.line}>
                      <TableCell>{e.line}</TableCell>
                      <TableCell className="whitespace-normal">{e.message}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}
        <DialogFooter>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </FormDialog>
    );
  }

  return (
    <FormDialog
      title="Import batches from CSV"
      description="Columns: product_code, batch_no, on_hand, expiry_date (YYYY-MM-DD), unit_cost_paise; optional reserved, allocated, safety_stock, quarantined. Up to 1 MB."
      onClose={onClose}
    >
      {facilities.isPending ? (
        <Loading label="Loading facilities…" />
      ) : (
        <form onSubmit={submit} noValidate className="grid gap-4">
          {facilities.isError && <FieldError>{facilities.error.message}</FieldError>}
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
          <FormField id="file" label="CSV file" error={form.errors.file}>
            <Input
              id="file"
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setFile(e.target.files?.[0])}
            />
          </FormField>
          <FormField id="reason" label="Reason (optional)">
            <Textarea {...form.bind("reason")} />
          </FormField>
          {form.errors.form && <FieldError>{form.errors.form}</FieldError>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={upload.isPending}>
              {upload.isPending ? "Importing…" : "Import"}
            </Button>
          </DialogFooter>
        </form>
      )}
    </FormDialog>
  );
}
