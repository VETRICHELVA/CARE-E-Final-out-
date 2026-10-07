// Purchase order detail (apps-ai-iot.md, supplier-web): one order as the hub returns it, with
// only the actions its state allows.
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "@care-e/api-client";
import {
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  formatDateTime,
  formatMoney,
  Loading,
  PageHeader,
  StatusChip,
} from "@care-e/ui";
import { useProducts, usePurchaseOrder } from "../api";
import { PoActions } from "../components/po-actions";
import { ProductName } from "../components/product-cell";
import { actionsFor, qty } from "../display";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[12rem_1fr]">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

const back = (
  <Link to="/orders" className="text-sm text-primary hover:underline">
    All purchase orders
  </Link>
);

export function OrderDetailPage() {
  const { id = "" } = useParams();
  const order = usePurchaseOrder(id);
  const products = useProducts();

  if (order.isPending || products.isPending) return <Loading label="Loading order…" />;
  if (order.isError) {
    if (order.error instanceof ApiError && order.error.status === 404)
      return (
        <EmptyState title="Purchase order not found">
          It may have been sent to another organization. {back}
        </EmptyState>
      );
    return <ErrorState error={order.error} />;
  }
  if (products.isError) return <ErrorState error={products.error} />;

  const po = order.data;
  const product = products.data.byId.get(po.product_id);
  const closed = actionsFor(po.status).length === 0;
  return (
    <>
      <div className="mb-2">{back}</div>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            Order for {po.to_org_name} <StatusChip state={po.status} />
          </span>
        }
        description={`Received ${formatDateTime(po.created_at)}`}
        actions={<PoActions po={po} product={product} />}
      />
      <Card>
        <CardContent>
          <dl className="grid gap-3">
            <Row label="Product">
              <ProductName product={product} />
            </Row>
            <Row label="Quantity">
              <span className="font-semibold">{qty(po.qty, product)}</span>
            </Row>
            <Row label="Unit price">{formatMoney(po.unit_price_paise)}</Row>
            <Row label="Buying hospital">{po.to_org_name}</Row>
            <Row label="Delivery ETA">{formatDateTime(po.eta)}</Row>
            <Row label="Last updated">{formatDateTime(po.updated_at)}</Row>
            <Row label="Shipment">
              {po.shipment_id ? (
                <span data-testid="shipment">Created ({po.shipment_id.slice(0, 8)})</span>
              ) : (
                <span className="text-muted-foreground">Created when you mark it dispatched</span>
              )}
            </Row>
          </dl>
        </CardContent>
      </Card>
      {closed && (
        <p className="mt-3 text-sm text-muted-foreground" data-testid="no-actions">
          Nothing left for you to do on this order.
        </p>
      )}
    </>
  );
}
