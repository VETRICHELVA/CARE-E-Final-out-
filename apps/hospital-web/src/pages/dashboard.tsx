// Dashboard (apps-ai-iot.md, hospital-web; S08 part): open shortages by status, and incoming
// requests awaiting this hospital's response with live countdowns. S18 adds, for users who
// keep the stock (`inventory.edit`): predicted stock-outs, the expiry-risk count with each
// batch's "Offer N to the network" suggestion, and surplus other hospitals offer this one.
import type { ReactNode } from "react";
import { Link } from "react-router";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Countdown,
  EmptyState,
  ErrorState,
  Loading,
  StatusChip,
  useCan,
  useNow,
} from "@care-e/ui";
import {
  useForecasts,
  useIncomingSurplus,
  useProducts,
  useShortages,
  useSourceRequests,
} from "../api";
import { PageHeader } from "../components/page";
import { ANSWERABLE, EXPIRY_BAND_LABELS, OPEN_STATES, qty, SHORTAGE_READERS } from "../display";
import { matchLabel, StockoutDate, SyntheticBadge } from "./forecasts";
import { useCanEditInventory } from "./inventory";

function OpenShortages() {
  const shortages = useShortages();
  if (shortages.isPending) return <Loading label="Loading shortages…" />;
  if (shortages.isError) return <ErrorState error={shortages.error} />;
  const rows = shortages.data.pages.flatMap((p) => p.items);
  const counts = OPEN_STATES.map((state) => ({
    state,
    n: rows.filter((s) => s.status === state).length,
  })).filter((c) => c.n > 0);
  if (counts.length === 0) return <EmptyState title="No open shortages" />;
  return (
    <ul aria-label="Open shortages by status" className="grid gap-2">
      {counts.map(({ state, n }) => (
        <li key={state} className="flex items-center justify-between">
          <StatusChip state={state} />
          <span className="font-semibold" data-testid={`count-${state}`}>
            {n}
          </span>
        </li>
      ))}
    </ul>
  );
}

function AwaitingRequests() {
  const now = useNow();
  const requests = useSourceRequests({ direction: "incoming", status: ANSWERABLE });
  const products = useProducts();
  if (requests.isPending || products.isPending) return <Loading label="Loading requests…" />;
  if (requests.isError) return <ErrorState error={requests.error} />;
  if (products.isError) return <ErrorState error={products.error} />;
  const rows = requests.data.pages.flatMap((p) => p.items);
  if (rows.length === 0) return <EmptyState title="No requests awaiting your response" />;
  return (
    <ul aria-label="Incoming requests awaiting response" className="grid gap-3">
      {rows.map((r) => {
        const product = products.data.byId.get(r.product_id);
        return (
          <li key={r.id} className="flex items-start justify-between gap-3 text-sm">
            <div>
              <div className="font-medium">
                {qty(r.qty, product)} {product?.name ?? "Unknown product"}
              </div>
              <div className="text-xs text-muted-foreground">from {r.requester_org_name}</div>
            </div>
            <Countdown to={r.sla_deadline} now={now} />
          </li>
        );
      })}
    </ul>
  );
}

function ForecastSummary() {
  const forecasts = useForecasts();
  const products = useProducts();
  if (forecasts.isPending || products.isPending) return <Loading label="Loading forecasts…" />;
  if (forecasts.isError) return <ErrorState error={forecasts.error} />;
  if (products.isError) return <ErrorState error={products.error} />;
  if (forecasts.data.length === 0) return <EmptyState title="No forecasts yet" />;
  const stockouts = forecasts.data
    .filter((f) => f.stockout_date !== null)
    .sort((a, b) => (a.stockout_date ?? "").localeCompare(b.stockout_date ?? ""));
  const risks = forecasts.data.flatMap((f) => f.expiry_risks.map((r) => ({ f, r })));
  return (
    <div className="grid gap-3 text-sm">
      <div className="flex items-center justify-between">
        <span>Expiry-risk batches</span>
        <span className="font-semibold" data-testid="expiry-risk-count">
          {risks.length}
        </span>
      </div>
      {risks.length > 0 && (
        <ul aria-label="Expiry-risk suggestions" className="grid gap-1">
          {risks.map(({ f, r }) => {
            const product = products.data.byId.get(f.product_id);
            return (
              <li key={r.batch_id}>
                {r.surplus_post_id
                  ? `Offered to the network: ${product?.name ?? "Unknown product"}, batch ${r.batch_no}`
                  : `Offer ${qty(r.suggested_qty, product)} to the network: ${product?.name ?? "Unknown product"}, batch ${r.batch_no}`}{" "}
                <SyntheticBadge forecast={f} />
              </li>
            );
          })}
        </ul>
      )}
      {stockouts.length === 0 ? (
        <p className="text-muted-foreground">No stock-outs predicted.</p>
      ) : (
        <ul aria-label="Predicted stock-outs" className="grid gap-1">
          {stockouts.map((f) => (
            <li key={f.product_id} className="flex flex-wrap justify-between gap-2">
              <span className="font-medium">
                {products.data.byId.get(f.product_id)?.name ?? "Unknown product"}
              </span>
              <StockoutDate iso={f.stockout_date ?? ""} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SurplusOffers() {
  const offers = useIncomingSurplus();
  const products = useProducts();
  if (offers.isPending || products.isPending) return <Loading label="Loading surplus…" />;
  if (offers.isError) return <ErrorState error={offers.error} />;
  if (products.isError) return <ErrorState error={products.error} />;
  if (offers.data.length === 0) return <EmptyState title="No surplus offered to you" />;
  return (
    <ul aria-label="Surplus offered to you" className="grid gap-2 text-sm">
      {offers.data.map((o) => {
        const product = products.data.byId.get(o.product_id);
        return (
          <li key={o.id}>
            <div className="font-medium">
              {o.org_name} offers {qty(o.offered_qty, product)} {product?.name ?? ""}
            </div>
            <div className="text-xs text-muted-foreground">
              {EXPIRY_BAND_LABELS[o.expiry_band] ?? o.expiry_band} · {matchLabel(o.match)}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function DashCard({ title, to, children }: { title: string; to: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader className="flex items-center justify-between">
        <CardTitle>{title}</CardTitle>
        <Link to={to} className="text-sm text-primary hover:underline">
          View all
        </Link>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export function DashboardPage() {
  const canSeeShortages = useCan(SHORTAGE_READERS);
  const canRespond = useCan("source_request.respond");
  const canEditStock = useCanEditInventory();
  return (
    <>
      <PageHeader title="Dashboard" />
      {canSeeShortages || canRespond || canEditStock ? (
        <div className="grid gap-4 md:grid-cols-2">
          {canRespond && (
            <DashCard title="Incoming requests awaiting response" to="/requests">
              <AwaitingRequests />
            </DashCard>
          )}
          {canSeeShortages && (
            <DashCard title="Open shortages" to="/shortages">
              <OpenShortages />
            </DashCard>
          )}
          {canEditStock && (
            <DashCard title="Forecasts and expiry risk" to="/forecasts">
              <ForecastSummary />
            </DashCard>
          )}
          {canEditStock && (
            <DashCard title="Surplus offered to you" to="/forecasts">
              <SurplusOffers />
            </DashCard>
          )}
        </div>
      ) : (
        <EmptyState title="Nothing to show yet">
          Your dashboard cards arrive with later sections.
        </EmptyState>
      )}
    </>
  );
}
