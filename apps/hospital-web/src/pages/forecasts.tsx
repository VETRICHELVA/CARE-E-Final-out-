// Forecasts and surplus (apps-ai-iot.md, hospital-web; S18): predicted stock-outs, reorder
// suggestions, expiry-risk batches with "Offer to network", surplus other hospitals offer this
// one, and this hospital's own posts. Every figure is the hub's (statistics run in the hub, not
// here and not in an LLM); the app only shows them and sends the user's choices.
import type { ReactNode } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  formatDateTime,
  Loading,
  StatusChip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  toast,
} from "@care-e/ui";
import {
  type ExpiryRisk,
  type Forecast,
  type Product,
  useCreateSurplus,
  useForecasts,
  useIncomingSurplus,
  useProducts,
  useRunForecasts,
  useSurplusPosts,
  useWithdrawSurplus,
} from "../api";
import { LoadMore, PageHeader } from "../components/page";
import { daysUntil, EXPIRY_BAND_LABELS, formatDate, qty, WITHDRAW_FROM } from "../display";
import { useCanEditInventory } from "./inventory";

const SYNTHETIC_HINT =
  "Learned from synthetic (seeded) consumption history: no consumption has been reported yet.";

type Products = { byId: Map<string, Product> };

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export function SyntheticBadge({ forecast }: { forecast: Pick<Forecast, "synthetic_history"> }) {
  if (!forecast.synthetic_history) return null;
  return (
    <Badge variant="outline" title={SYNTHETIC_HINT} data-testid="synthetic">
      Synthetic history
    </Badge>
  );
}

/** "14 Oct 2026 (in 4 days)" from the hub's date. */
export function StockoutDate({ iso }: { iso: string }) {
  const days = daysUntil(iso);
  const when = days <= 0 ? "today" : days === 1 ? "tomorrow" : `in ${days} days`;
  return (
    <span data-testid="stockout">
      {formatDate(iso)} ({when})
    </span>
  );
}

function Stockouts({ forecasts, products }: { forecasts: Forecast[]; products: Products }) {
  const rows = forecasts
    .filter((f) => f.stockout_date !== null || (f.reorder?.qty ?? 0) > 0)
    .sort((a, b) => (a.stockout_date ?? "9999").localeCompare(b.stockout_date ?? "9999"));
  if (rows.length === 0)
    return (
      <EmptyState title="No stock-outs predicted">Usable stock lasts the forecast.</EmptyState>
    );
  return (
    <Table aria-label="Predicted stock-outs">
      <TableHeader>
        <TableRow>
          <TableHead>Product</TableHead>
          <TableHead className="text-right">Usable stock</TableHead>
          <TableHead>Predicted stock-out</TableHead>
          <TableHead>Reorder suggestion</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((f) => {
          const product = products.byId.get(f.product_id);
          return (
            <TableRow key={f.product_id}>
              <TableCell>
                <div className="font-medium">{product?.name ?? "Unknown product"}</div>
                <SyntheticBadge forecast={f} />
              </TableCell>
              <TableCell className="text-right">{qty(f.usable_stock, product)}</TableCell>
              <TableCell>
                {f.stockout_date ? <StockoutDate iso={f.stockout_date} /> : "Not in the forecast"}
              </TableCell>
              <TableCell data-testid="reorder">
                {f.reorder
                  ? `${qty(f.reorder.qty, product)} (lead time ${f.reorder.lead_time_days} d)`
                  : "—"}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function OfferButton({ risk, product }: { risk: ExpiryRisk; product: Product | undefined }) {
  const create = useCreateSurplus();
  if (risk.surplus_post_id) return <Badge variant="secondary">Offered</Badge>;
  if (risk.suggested_qty <= 0)
    return <span className="text-muted-foreground">None transferable</span>;
  return (
    <ConfirmDialog
      trigger="Offer to network"
      title={`Offer ${qty(risk.suggested_qty, product)} to the network?`}
      description={`Batch ${risk.batch_no}. Other hospitals see the quantity, an expiry band and your location, never the batch or its expiry date. The hub offers no more than the batch's transferable stock.`}
      confirmLabel="Offer to network"
      onConfirm={async (reason) => {
        await create.mutateAsync({ batch_id: risk.batch_id, qty: risk.suggested_qty, reason });
        toast.success("Offered to the network.");
      }}
    />
  );
}

function ExpiryRisks({
  forecasts,
  products,
  canEdit,
}: {
  forecasts: Forecast[];
  products: Products;
  canEdit: boolean;
}) {
  const rows = forecasts.flatMap((f) => f.expiry_risks.map((r) => ({ f, r })));
  if (rows.length === 0)
    return (
      <EmptyState title="No batches at expiry risk">Forecast use covers every batch.</EmptyState>
    );
  return (
    <Table aria-label="Expiry-risk batches">
      <TableHeader>
        <TableRow>
          <TableHead>Product</TableHead>
          <TableHead>Batch</TableHead>
          <TableHead>Expiry</TableHead>
          <TableHead className="text-right">Forecast use before expiry</TableHead>
          <TableHead className="text-right">Excess</TableHead>
          <TableHead className="text-right">Transferable</TableHead>
          {canEdit && <TableHead className="sr-only">Actions</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map(({ f, r }) => {
          const product = products.byId.get(f.product_id);
          return (
            <TableRow key={r.batch_id}>
              <TableCell>
                <div className="font-medium">{product?.name ?? "Unknown product"}</div>
                <SyntheticBadge forecast={f} />
              </TableCell>
              <TableCell>{r.batch_no}</TableCell>
              <TableCell>{formatDate(r.expiry_date)}</TableCell>
              <TableCell className="text-right">
                {qty(Math.round(r.usage_before_expiry), product)}
              </TableCell>
              <TableCell className="text-right font-semibold" data-testid="excess">
                {qty(r.excess, product)}
              </TableCell>
              <TableCell className="text-right">{qty(r.transferable, product)}</TableCell>
              {canEdit && (
                <TableCell className="text-right">
                  <OfferButton risk={r} product={product} />
                </TableCell>
              )}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export function matchLabel(m: { kind: string; stockout_date: string | null }) {
  if (m.kind === "SHORTAGE") return "Matches your open shortage";
  return m.stockout_date
    ? `Matches your predicted stock-out on ${formatDate(m.stockout_date)}`
    : "Matches your predicted stock-out";
}

function IncomingSurplus({ products }: { products: Products }) {
  const offers = useIncomingSurplus();
  if (offers.isPending) return <Loading label="Loading surplus…" />;
  if (offers.isError) return <ErrorState error={offers.error} />;
  if (offers.data.length === 0)
    return (
      <EmptyState title="No surplus offered to you">Matches appear here automatically.</EmptyState>
    );
  return (
    <ul aria-label="Surplus offered to you" className="grid gap-3">
      {offers.data.map((o) => {
        const product = products.byId.get(o.product_id);
        return (
          <li key={o.id} className="flex flex-wrap items-start justify-between gap-2 text-sm">
            <div>
              <div className="font-medium">
                {o.org_name} offers {qty(o.offered_qty, product)} {product?.name ?? ""}
              </div>
              <div className="text-xs text-muted-foreground">
                {EXPIRY_BAND_LABELS[o.expiry_band] ?? o.expiry_band} · {o.location.facility_name} ·{" "}
                {matchLabel(o.match)}
              </div>
            </div>
            <StatusChip state={o.status} />
          </li>
        );
      })}
    </ul>
  );
}

function OwnPosts({ products }: { products: Products }) {
  const posts = useSurplusPosts();
  const withdraw = useWithdrawSurplus();
  if (posts.isPending) return <Loading label="Loading your surplus…" />;
  if (posts.isError) return <ErrorState error={posts.error} />;
  const rows = posts.data.pages.flatMap((p) => p.items);
  if (rows.length === 0) return <EmptyState title="You have not offered any surplus" />;
  return (
    <>
      <Table aria-label="Your surplus posts">
        <TableHeader>
          <TableRow>
            <TableHead>Product</TableHead>
            <TableHead className="text-right">Posted</TableHead>
            <TableHead className="text-right">Offered now</TableHead>
            <TableHead>Expiry</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Posted at</TableHead>
            <TableHead className="sr-only">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((p) => {
            const product = products.byId.get(p.product_id);
            return (
              <TableRow key={p.id}>
                <TableCell className="font-medium">{product?.name ?? "Unknown product"}</TableCell>
                <TableCell className="text-right">{qty(p.qty, product)}</TableCell>
                <TableCell
                  className="text-right font-semibold"
                  title="Never more than the batch's transferable stock now (hub-computed)."
                  data-testid="offered"
                >
                  {qty(p.offered_qty, product)}
                </TableCell>
                <TableCell>{formatDate(p.expiry_date)}</TableCell>
                <TableCell>
                  <StatusChip state={p.status} />
                </TableCell>
                <TableCell>{formatDateTime(p.created_at)}</TableCell>
                <TableCell className="text-right">
                  {WITHDRAW_FROM.has(p.status) && (
                    <ConfirmDialog
                      trigger="Withdraw"
                      title="Withdraw this surplus post?"
                      description="Other hospitals stop seeing it, and it is never matched again."
                      confirmLabel="Withdraw"
                      destructive
                      onConfirm={(reason) => withdraw.mutateAsync({ id: p.id, reason })}
                    />
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <LoadMore {...posts} />
    </>
  );
}

function RunButton() {
  const run = useRunForecasts();
  return (
    <Button
      variant="outline"
      disabled={run.isPending}
      onClick={() =>
        run.mutate(undefined, {
          onSuccess: (r) => toast.success(`Forecast ${r.series} products.`),
          onError: (e) => toast.error(e.message),
        })
      }
    >
      {run.isPending ? "Forecasting…" : "Run forecast now"}
    </Button>
  );
}

export function ForecastsPage() {
  const canEdit = useCanEditInventory();
  const forecasts = useForecasts();
  const products = useProducts();

  let body;
  if (forecasts.isPending || products.isPending) body = <Loading label="Loading forecasts…" />;
  else if (forecasts.isError) body = <ErrorState error={forecasts.error} />;
  else if (products.isError) body = <ErrorState error={products.error} />;
  else if (forecasts.data.length === 0)
    body = (
      <EmptyState title="No forecasts yet">
        Forecasts run every night from consumption history.
      </EmptyState>
    );
  else {
    const generated = forecasts.data.map((f) => f.generated_at).sort()[0];
    body = (
      <>
        <p className="text-sm text-muted-foreground" data-testid="generated">
          Forecast {generated ? formatDateTime(generated) : ""} with{" "}
          {[...new Set(forecasts.data.map((f) => f.model_version))].join(", ")}.
        </p>
        <Section title="Predicted stock-outs and reorder suggestions">
          <Stockouts forecasts={forecasts.data} products={products.data} />
        </Section>
        <Section title="Expiry-risk batches">
          <ExpiryRisks forecasts={forecasts.data} products={products.data} canEdit={canEdit} />
        </Section>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Forecasts and surplus"
        description="Statistical forecasts of your consumption, computed by the hub."
        actions={canEdit && <RunButton />}
      />
      <div className="grid gap-4">
        {body}
        {canEdit && products.data && (
          <>
            <Section title="Surplus offered to you">
              <IncomingSurplus products={products.data} />
            </Section>
            <Section title="Your surplus posts">
              <OwnPosts products={products.data} />
            </Section>
          </>
        )}
      </div>
    </>
  );
}
