// "New shortage" (apps-ai-iot.md, hospital-web). The form checks shape only; the hub computes
// the shortfall and decides whether there is anything to source, and we show its answer.
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
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
  StatusChip,
  Textarea,
} from "@care-e/ui";
import { type Shortage, useCreateShortage, useFacilities, useProducts } from "../api";
import { FormField } from "../components/page";
import { qty } from "../display";
import { count, optionalCount, optionalText, useForm } from "../forms";

const schema = z.object({
  product_id: z.string().min(1, "Choose a product."),
  facility_id: z.string().min(1, "Choose a facility."),
  qty_required: count("the quantity required"),
  qty_local_usable: count("the usable quantity you still have (0 if none)"),
  required_by: z
    .string()
    .min(1, "Choose when you need it by.")
    .refine((s) => !Number.isNaN(new Date(s).getTime()), "Choose a valid date and time.")
    // datetime-local is the user's own zone; the hub takes UTC.
    .transform((s) => new Date(s).toISOString()),
  priority: z.enum(["CRITICAL", "ROUTINE"], "Choose a priority."),
  min_shelf_life_days: optionalCount,
  notes: optionalText,
});

export function NewShortageDialog({ onClose }: { onClose: () => void }) {
  const products = useProducts();
  const facilities = useFacilities();
  const create = useCreateShortage();
  const [saved, setSaved] = useState<Shortage>();
  const form = useForm({
    product_id: "",
    facility_id: "",
    qty_required: "",
    qty_local_usable: "",
    required_by: "",
    priority: "",
    min_shelf_life_days: "",
    notes: "",
  });

  const only = facilities.data?.length === 1 ? facilities.data[0]?.id : undefined;
  const product = products.data?.byId.get(form.values.product_id);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body = form.parse(only ? schema.extend({ facility_id: z.string() }) : schema);
    if (!body) return;
    create.mutate(
      { ...body, facility_id: only ?? body.facility_id },
      { onSuccess: setSaved, onError: (e) => form.setErrors({ form: e.message }) },
    );
  };

  let content;
  if (saved) {
    const savedProduct = products.data?.byId.get(saved.product_id);
    content = (
      <>
        <div role="status" className="grid gap-2 rounded-md bg-primary/5 p-4">
          <p className="text-sm text-muted-foreground">Shortfall, computed by the hub</p>
          <p className="text-2xl font-semibold text-primary" data-testid="shortfall">
            {qty(saved.shortfall, savedProduct)}
          </p>
          <p className="text-sm">
            {savedProduct?.name}: <StatusChip state={saved.status} />
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
          <Button asChild>
            <Link to={`/shortages/${saved.id}`}>View shortage</Link>
          </Button>
        </DialogFooter>
      </>
    );
  } else if (products.isPending || facilities.isPending) {
    content = <Loading />;
  } else {
    content = (
      <form onSubmit={submit} noValidate className="grid gap-4">
        {products.isError && <FieldError>{products.error.message}</FieldError>}
        {facilities.isError && <FieldError>{facilities.error.message}</FieldError>}
        <FormField id="product_id" label="Product" error={form.errors.product_id}>
          <NativeSelect {...form.bind("product_id")}>
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
        <div className="grid grid-cols-2 gap-4">
          <FormField id="qty_required" label="Quantity required" error={form.errors.qty_required}>
            <Input inputMode="numeric" {...form.bind("qty_required")} />
          </FormField>
          <FormField
            id="qty_local_usable"
            label="Usable stock on hand"
            error={form.errors.qty_local_usable}
          >
            <Input inputMode="numeric" {...form.bind("qty_local_usable")} />
          </FormField>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <FormField id="required_by" label="Required by" error={form.errors.required_by}>
            <Input type="datetime-local" {...form.bind("required_by")} />
          </FormField>
          <FormField id="priority" label="Priority" error={form.errors.priority}>
            <NativeSelect {...form.bind("priority")}>
              <option value="">Choose…</option>
              <option value="CRITICAL">Critical</option>
              <option value="ROUTINE">Routine</option>
            </NativeSelect>
          </FormField>
        </div>
        <FormField
          id="min_shelf_life_days"
          label="Minimum shelf life (days)"
          error={form.errors.min_shelf_life_days}
          hint={
            product
              ? `Leave blank for the product's default of ${product.default_min_shelf_life_days} days.`
              : "Leave blank for the product's default."
          }
        >
          <Input inputMode="numeric" {...form.bind("min_shelf_life_days")} />
        </FormField>
        <FormField id="notes" label="Notes (optional)">
          <Textarea {...form.bind("notes")} />
        </FormField>
        {form.errors.form && <FieldError>{form.errors.form}</FieldError>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Reporting…" : "Report shortage"}
          </Button>
        </DialogFooter>
      </form>
    );
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{saved ? "Shortage reported" : "New shortage"}</DialogTitle>
          {!saved && (
            <DialogDescription>
              The hub works out the shortfall and starts looking for transferable stock.
            </DialogDescription>
          )}
        </DialogHeader>
        {content}
      </DialogContent>
    </Dialog>
  );
}
