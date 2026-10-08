// Network demand (apps-ai-iot.md, supplier-web): open shortfall totals for this supplier's
// products, aggregated by the hub across hospitals. The hub sends no hospital identities, so
// the page has none to show.
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  PageHeader,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useNow,
} from "@care-e/ui";
import { Link } from "react-router";
import { useDemand, useOffers, useProducts } from "../api";
import { ProductCell } from "../components/product-cell";
import { qty } from "../display";
import { StaleBadge } from "../components/stale-badge";

export function DemandPage() {
  const now = useNow(60_000);
  const demand = useDemand();
  const offers = useOffers();
  const products = useProducts();

  let body;
  if (demand.isPending || offers.isPending || products.isPending)
    body = <Loading label="Loading network demand…" />;
  else if (demand.isError) body = <ErrorState error={demand.error} />;
  else if (offers.isError) body = <ErrorState error={offers.error} />;
  else if (products.isError) body = <ErrorState error={products.error} />;
  else if (demand.data.length === 0)
    body = (
      <EmptyState title="You don't offer any products yet">
        Demand shows for the products you offer.{" "}
        <Link to="/offers" className="text-primary hover:underline">
          Add an offer
        </Link>
      </EmptyState>
    );
  else
    body = (
      <div className="rounded-lg border bg-card">
        <Table aria-label="Network demand">
          <TableHeader>
            <TableRow>
              <TableHead>Product</TableHead>
              <TableHead className="text-right">Open shortfall in the network</TableHead>
              <TableHead className="text-right">Your available quantity</TableHead>
              <TableHead>Your offer</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {demand.data.map((row) => {
              const product = products.data.byId.get(row.product_id);
              const offer = offers.data.byProduct.get(row.product_id);
              return (
                <TableRow key={row.product_id} data-testid={`demand-${product?.code}`}>
                  <ProductCell product={product} />
                  <TableCell className="text-right font-semibold">
                    {row.open_shortfall_qty > 0 ? (
                      qty(row.open_shortfall_qty, product)
                    ) : (
                      <span className="font-normal text-muted-foreground">No open demand</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {offer ? qty(offer.available_qty, product) : "—"}
                  </TableCell>
                  <TableCell>{offer && <StaleBadge offer={offer} now={now} />}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    );

  return (
    <>
      <PageHeader
        title="Network demand"
        description="Open shortages for the products you offer, totalled across hospitals. Which hospitals are short stays private."
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => void demand.refetch()}
            disabled={demand.isFetching}
          >
            {demand.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />
      {body}
    </>
  );
}
