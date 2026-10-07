// Catalog and offers (apps-ai-iot.md, supplier-web): per product this org's price, lead time
// and available quantity, with when it was last updated; inline edit for `po.respond` users.
// The hub stamps `updated_at` on every save, so saving unchanged values re-confirms an offer.
import { type FormEvent, useId, useState } from "react";
import {
  Button,
  EmptyState,
  ErrorState,
  FieldError,
  formatDateTime,
  formatMoney,
  Input,
  Loading,
  PageHeader,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  toast,
  useCan,
  useNow,
} from "@care-e/ui";
import { type Offer, type Product, useOffers, useProducts, usePutOffer } from "../api";
import { ProductCell } from "../components/product-cell";
import { StaleBadge } from "../components/stale-badge";
import { formatHours, isStaleOffer, PO_RESPOND, qty } from "../display";
import { type OfferDraft, type OfferErrors, paiseToRupees, parseOffer } from "../forms";

const draftOf = (offer: Offer | undefined): OfferDraft => ({
  price: offer ? paiseToRupees(offer.unit_price_paise) : "",
  leadTime: offer ? String(offer.lead_time_hours) : "",
  available: offer ? String(offer.available_qty) : "",
});

function NumberField({
  label,
  value,
  error,
  onChange,
  prefix,
  form,
}: {
  label: string;
  value: string;
  error: string | undefined;
  onChange: (v: string) => void;
  prefix?: string;
  form: string;
}) {
  const id = useId();
  return (
    <div className="grid gap-1">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <div className="flex items-center gap-1">
        {prefix && <span className="text-sm text-muted-foreground">{prefix}</span>}
        <Input
          id={id}
          form={form}
          inputMode="decimal"
          className="h-8 w-28 text-right"
          value={value}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
      {error && <FieldError id={`${id}-error`}>{error}</FieldError>}
    </div>
  );
}

/** One product's row while it is being edited: a small form across the cells. */
function EditRow({
  product,
  offer,
  onDone,
}: {
  product: Product;
  offer: Offer | undefined;
  onDone: () => void;
}) {
  const put = usePutOffer();
  const [draft, setDraft] = useState(() => draftOf(offer));
  const [errors, setErrors] = useState<OfferErrors>({});
  const [hubError, setHubError] = useState<string>();
  const formId = useId();
  const set = (field: keyof OfferDraft) => (value: string) =>
    setDraft((d) => ({ ...d, [field]: value }));

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setHubError(undefined);
    const parsed = parseOffer(product.id, draft);
    if (parsed.errors) return setErrors(parsed.errors);
    setErrors({});
    try {
      await put.mutateAsync(parsed.body);
      toast.success(`${product.name}: offer saved.`);
      onDone();
    } catch (e) {
      setHubError(e instanceof Error ? e.message : "Something went wrong.");
    }
  };

  return (
    <TableRow data-testid={`offer-${product.code}`} aria-label={`Editing ${product.name}`}>
      <ProductCell product={product} />
      <TableCell className="text-right align-top">
        <form id={formId} onSubmit={(e) => void save(e)} />
        <NumberField
          label="Unit price (₹)"
          prefix="₹"
          value={draft.price}
          error={errors.price}
          onChange={set("price")}
          form={formId}
        />
      </TableCell>
      <TableCell className="text-right align-top">
        <NumberField
          label="Lead time (hours)"
          value={draft.leadTime}
          error={errors.leadTime}
          onChange={set("leadTime")}
          form={formId}
        />
      </TableCell>
      <TableCell className="text-right align-top">
        <NumberField
          label={`Available quantity (${product.unit})`}
          value={draft.available}
          error={errors.available}
          onChange={set("available")}
          form={formId}
        />
      </TableCell>
      <TableCell className="align-top">{hubError && <FieldError>{hubError}</FieldError>}</TableCell>
      <TableCell className="align-top">
        <div className="flex justify-end gap-2">
          <Button size="sm" type="submit" form={formId} disabled={put.isPending}>
            {put.isPending ? "Saving…" : "Save"}
          </Button>
          <Button size="sm" variant="outline" onClick={onDone} disabled={put.isPending}>
            Cancel
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

/** Saves the offer unchanged: the hub refreshes `updated_at`, so it passes the freshness gate. */
function ConfirmCurrent({ product, offer }: { product: Product; offer: Offer }) {
  const put = usePutOffer();
  const confirm = async () => {
    try {
      await put.mutateAsync({
        product_id: offer.product_id,
        unit_price_paise: offer.unit_price_paise,
        lead_time_hours: offer.lead_time_hours,
        available_qty: offer.available_qty,
      });
      toast.success(`${product.name}: offer confirmed as current.`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Something went wrong.");
    }
  };
  return (
    <Button size="sm" variant="outline" onClick={() => void confirm()} disabled={put.isPending}>
      Still current
    </Button>
  );
}

function ViewRow({
  product,
  offer,
  now,
  canEdit,
  onEdit,
}: {
  product: Product;
  offer: Offer | undefined;
  now: number;
  canEdit: boolean;
  onEdit: () => void;
}) {
  return (
    <TableRow data-testid={`offer-${product.code}`}>
      <ProductCell product={product} />
      {offer ? (
        <>
          <TableCell className="text-right">{formatMoney(offer.unit_price_paise)}</TableCell>
          <TableCell className="text-right">{formatHours(offer.lead_time_hours)}</TableCell>
          <TableCell className="text-right font-semibold">
            {qty(offer.available_qty, product)}
          </TableCell>
          <TableCell>
            <div className="flex flex-wrap items-center gap-2">
              <span>{formatDateTime(offer.updated_at)}</span>
              <StaleBadge offer={offer} now={now} />
            </div>
          </TableCell>
        </>
      ) : (
        <TableCell colSpan={4} className="text-muted-foreground">
          Not offered
        </TableCell>
      )}
      <TableCell>
        {canEdit && (
          <div className="flex justify-end gap-2">
            {offer && isStaleOffer(offer.updated_at, now) && (
              <ConfirmCurrent product={product} offer={offer} />
            )}
            <Button size="sm" variant={offer ? "outline" : "default"} onClick={onEdit}>
              {offer ? "Edit" : "Offer"}
            </Button>
          </div>
        )}
      </TableCell>
    </TableRow>
  );
}

export function OffersPage() {
  const now = useNow(60_000);
  const canEdit = useCan(PO_RESPOND);
  const offers = useOffers();
  const products = useProducts();
  const [editing, setEditing] = useState<string>();
  const [showAll, setShowAll] = useState(false);

  let body;
  if (offers.isPending || products.isPending) body = <Loading label="Loading offers…" />;
  else if (offers.isError) body = <ErrorState error={offers.error} />;
  else if (products.isError) body = <ErrorState error={products.error} />;
  else {
    const byProduct = offers.data.byProduct;
    const byName = (a: Product, b: Product) => a.name.localeCompare(b.name);
    const offered = products.data.list.filter((p) => byProduct.has(p.id)).sort(byName);
    const others = products.data.list.filter((p) => !byProduct.has(p.id)).sort(byName);
    const rows = showAll ? [...offered, ...others] : offered;
    body =
      rows.length === 0 ? (
        <EmptyState title="You don't offer any products yet">
          {canEdit && "Show the whole catalog to add your first offer."}
        </EmptyState>
      ) : (
        <div className="rounded-lg border bg-card">
          <Table aria-label="Offers">
            <TableHeader>
              <TableRow>
                <TableHead>Product</TableHead>
                <TableHead className="text-right">Unit price</TableHead>
                <TableHead className="text-right">Lead time</TableHead>
                <TableHead className="text-right">Available</TableHead>
                <TableHead>Last updated</TableHead>
                <TableHead className="sr-only">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((product) =>
                editing === product.id ? (
                  <EditRow
                    key={product.id}
                    product={product}
                    offer={byProduct.get(product.id)}
                    onDone={() => setEditing(undefined)}
                  />
                ) : (
                  <ViewRow
                    key={product.id}
                    product={product}
                    offer={byProduct.get(product.id)}
                    now={now}
                    canEdit={canEdit}
                    onEdit={() => setEditing(product.id)}
                  />
                ),
              )}
            </TableBody>
          </Table>
        </div>
      );
  }

  return (
    <>
      <PageHeader
        title="Catalog and offers"
        description="Matching uses an offer only if it was updated in the last 7 days. Saving re-confirms it."
        actions={
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={showAll}
              onChange={(e) => setShowAll(e.target.checked)}
            />
            Show products you don't offer
          </label>
        }
      />
      {body}
    </>
  );
}
