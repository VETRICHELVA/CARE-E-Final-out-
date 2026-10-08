// Purchase orders (apps-ai-iot.md, supplier-web): the orders hospitals' approved BUYs sent to
// this supplier, filterable by status, with only the actions the order's state allows.
import { Link, useSearchParams } from "react-router";
import {
  Button,
  EmptyState,
  ErrorState,
  formatDateTime,
  formatMoney,
  LoadMore,
  Loading,
  PageHeader,
  STATUS,
  StatusChip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@care-e/ui";
import { type PoStatus, useProducts, usePurchaseOrders } from "../api";
import { PoActions } from "../components/po-actions";
import { ProductCell } from "../components/product-cell";
import { PO_STATUSES, qty } from "../display";

/** `?status=` from the URL, if it is a PurchaseOrder state. */
function useStatusFilter(): [PoStatus | undefined, (s: PoStatus | undefined) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("status");
  const status = PO_STATUSES.find((s) => s === raw);
  return [status, (next) => setParams(next ? { status: next } : {}, { replace: true })];
}

function Filter({
  status,
  onChange,
}: {
  status: PoStatus | undefined;
  onChange: (s: PoStatus | undefined) => void;
}) {
  const options: [PoStatus | undefined, string][] = [
    [undefined, "All"],
    ...PO_STATUSES.map((s): [PoStatus, string] => [s, STATUS[s]?.label ?? s]),
  ];
  return (
    <div role="group" aria-label="Filter by status" className="mb-3 flex flex-wrap gap-1">
      {options.map(([value, label]) => (
        <Button
          key={label}
          size="sm"
          variant={status === value ? "default" : "outline"}
          aria-pressed={status === value}
          onClick={() => onChange(value)}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}

export function OrdersPage() {
  const [status, setStatus] = useStatusFilter();
  const orders = usePurchaseOrders(status);
  const products = useProducts();

  let body;
  if (orders.isPending || products.isPending) body = <Loading label="Loading orders…" />;
  else if (orders.isError) body = <ErrorState error={orders.error} />;
  else if (products.isError) body = <ErrorState error={products.error} />;
  else {
    const rows = orders.data.pages.flatMap((p) => p.items);
    body =
      rows.length === 0 ? (
        <EmptyState
          title={
            status ? `No ${STATUS[status]?.label.toLowerCase()} orders` : "No purchase orders yet"
          }
        >
          Orders arrive here when a hospital approves buying from you.
        </EmptyState>
      ) : (
        <>
          <div className="rounded-lg border bg-card">
            <Table aria-label="Purchase orders">
              <TableHeader>
                <TableRow>
                  <TableHead>Product</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Unit price</TableHead>
                  <TableHead>Hospital</TableHead>
                  <TableHead>Delivery ETA</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Received</TableHead>
                  <TableHead className="sr-only">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((po) => {
                  const product = products.data.byId.get(po.product_id);
                  return (
                    <TableRow key={po.id} data-testid={`order-${po.id}`}>
                      <ProductCell product={product} />
                      <TableCell className="text-right font-semibold">
                        {qty(po.qty, product)}
                      </TableCell>
                      <TableCell className="text-right">
                        {formatMoney(po.unit_price_paise)}
                      </TableCell>
                      <TableCell>{po.to_org_name}</TableCell>
                      <TableCell>{formatDateTime(po.eta)}</TableCell>
                      <TableCell>
                        <StatusChip state={po.status} />
                      </TableCell>
                      <TableCell>{formatDateTime(po.created_at)}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap items-center justify-end gap-2">
                          <PoActions po={po} product={product} />
                          <Link
                            to={`/orders/${po.id}`}
                            className="text-sm text-primary hover:underline"
                          >
                            Details
                          </Link>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <LoadMore {...orders} />
        </>
      );
  }

  return (
    <>
      <PageHeader
        title="Purchase orders"
        description="Acknowledge new orders, mark them dispatched when they leave, or reject them."
      />
      <Filter status={status} onChange={setStatus} />
      {body}
    </>
  );
}
