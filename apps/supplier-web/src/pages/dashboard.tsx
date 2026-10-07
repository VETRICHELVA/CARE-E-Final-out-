// Dashboard (apps-ai-iot.md, supplier-web; S10 brief): new purchase orders, orders to
// dispatch, and offers not updated in 7 days (they fail the freshness gate).
import type { ReactNode } from "react";
import { Link } from "react-router";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  formatDateTime,
  Loading,
  PageHeader,
  useCan,
  useNow,
} from "@care-e/ui";
import { type PoStatus, useOffers, useProducts, usePurchaseOrders } from "../api";
import { isStaleOffer, NEW_ORDERS, PO_RESPOND, qty, TO_DISPATCH } from "../display";
import { StaleBadge } from "../components/stale-badge";

const SHOWN = 5;

function DashCard({
  title,
  to,
  count,
  children,
}: {
  title: string;
  to: string;
  count?: number;
  children: ReactNode;
}) {
  const id = title.toLowerCase().replace(/\W+/g, "-");
  return (
    <Card aria-labelledby={id} role="region">
      <CardHeader className="flex items-center justify-between">
        <CardTitle id={id}>
          {title}
          {count !== undefined && (
            <span className="ml-2 text-muted-foreground" data-testid={`${id}-count`}>
              {count}
            </span>
          )}
        </CardTitle>
        <Link to={to} className="text-sm text-primary hover:underline">
          View all
        </Link>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function OrderQueue({ status, empty }: { status: PoStatus; empty: string }) {
  const orders = usePurchaseOrders(status);
  const products = useProducts();
  if (orders.isPending || products.isPending) return <Loading label="Loading orders…" />;
  if (orders.isError) return <ErrorState error={orders.error} />;
  if (products.isError) return <ErrorState error={products.error} />;
  const rows = orders.data.pages.flatMap((p) => p.items);
  if (rows.length === 0) return <EmptyState title={empty} />;
  return (
    <ul className="grid gap-3">
      {rows.slice(0, SHOWN).map((po) => {
        const product = products.data.byId.get(po.product_id);
        return (
          <li key={po.id} className="flex items-start justify-between gap-3 text-sm">
            <div>
              <Link to={`/orders/${po.id}`} className="font-medium hover:underline">
                {qty(po.qty, product)} {product?.name ?? "Unknown product"}
              </Link>
              <div className="text-xs text-muted-foreground">for {po.to_org_name}</div>
            </div>
            <span className="text-xs text-muted-foreground">ETA {formatDateTime(po.eta)}</span>
          </li>
        );
      })}
      {rows.length > SHOWN && (
        <li className="text-xs text-muted-foreground">and {rows.length - SHOWN} more</li>
      )}
    </ul>
  );
}

function StaleOffers() {
  const now = useNow(60_000);
  const offers = useOffers();
  const products = useProducts();
  if (offers.isPending || products.isPending) return <Loading label="Loading offers…" />;
  if (offers.isError) return <ErrorState error={offers.error} />;
  if (products.isError) return <ErrorState error={products.error} />;
  const stale = offers.data.list.filter((o) => isStaleOffer(o.updated_at, now));
  if (stale.length === 0) return <EmptyState title="Every offer is up to date" />;
  return (
    <>
      <p className="mb-3 text-sm text-muted-foreground">
        Matching skips these until you update or re-confirm them.
      </p>
      <ul aria-label="Stale offers" className="grid gap-2">
        {stale.map((offer) => {
          const product = products.data.byId.get(offer.product_id);
          return (
            <li key={offer.id} className="flex items-center justify-between gap-3 text-sm">
              <span className="font-medium">{product?.name ?? "Unknown product"}</span>
              <StaleBadge offer={offer} now={now} />
            </li>
          );
        })}
      </ul>
    </>
  );
}

/** The count once loaded: the first page (200 orders), as the list shows. */
function useCount(status: PoStatus, enabled: boolean) {
  const orders = usePurchaseOrders(status, enabled);
  return orders.data?.pages.flatMap((p) => p.items).length;
}

export function DashboardPage() {
  const canRespond = useCan(PO_RESPOND);
  const newCount = useCount(NEW_ORDERS, canRespond);
  const dispatchCount = useCount(TO_DISPATCH, canRespond);
  return (
    <>
      <PageHeader title="Dashboard" />
      <div className="grid gap-4 md:grid-cols-2">
        {canRespond && (
          <>
            <DashCard
              title="New purchase orders"
              to={`/orders?status=${NEW_ORDERS}`}
              count={newCount}
            >
              <OrderQueue status={NEW_ORDERS} empty="No new purchase orders" />
            </DashCard>
            <DashCard
              title="Orders to dispatch"
              to={`/orders?status=${TO_DISPATCH}`}
              count={dispatchCount}
            >
              <OrderQueue status={TO_DISPATCH} empty="Nothing waiting to be dispatched" />
            </DashCard>
          </>
        )}
        <DashCard title="Offers not updated in 7 days" to="/offers">
          <StaleOffers />
        </DashCard>
      </div>
    </>
  );
}
