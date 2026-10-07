// Shortages (apps-ai-iot.md, hospital-web): the org's shortages with status chips.
import { useState } from "react";
import { Link } from "react-router";
import {
  Badge,
  Button,
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
  useCan,
} from "@care-e/ui";
import { useProducts, useShortages } from "../api";
import { LoadMore, PageHeader } from "../components/page";
import { PRIORITY_LABELS, qty } from "../display";
import { NewShortageDialog } from "./new-shortage";

export function PriorityBadge({ priority }: { priority: string }) {
  return (
    <Badge variant={priority === "CRITICAL" ? "destructive" : "secondary"}>
      {PRIORITY_LABELS[priority] ?? priority}
    </Badge>
  );
}

export function ShortagesPage() {
  const canCreate = useCan("shortage.create");
  const shortages = useShortages();
  const products = useProducts();
  const [creating, setCreating] = useState(false);

  let body;
  if (shortages.isPending || products.isPending) body = <Loading label="Loading shortages…" />;
  else if (shortages.isError) body = <ErrorState error={shortages.error} />;
  else if (products.isError) body = <ErrorState error={products.error} />;
  else {
    const rows = shortages.data.pages.flatMap((p) => p.items);
    body =
      rows.length === 0 ? (
        <EmptyState title="No shortages reported">
          {canCreate && "Report one with “New shortage”."}
        </EmptyState>
      ) : (
        <>
          <div className="rounded-lg border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Product</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead className="text-right">Shortfall</TableHead>
                  <TableHead>Required by</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Reported</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((s) => {
                  const product = products.data.byId.get(s.product_id);
                  return (
                    <TableRow key={s.id}>
                      <TableCell>
                        <Link
                          to={`/shortages/${s.id}`}
                          className="font-medium text-primary hover:underline"
                        >
                          {product?.name ?? "Unknown product"}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <PriorityBadge priority={s.priority} />
                      </TableCell>
                      <TableCell className="text-right">{qty(s.shortfall, product)}</TableCell>
                      <TableCell>{formatDateTime(s.required_by)}</TableCell>
                      <TableCell>
                        <StatusChip state={s.status} />
                      </TableCell>
                      <TableCell>{formatDateTime(s.created_at)}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <LoadMore {...shortages} />
        </>
      );
  }

  return (
    <>
      <PageHeader
        title="Shortages"
        description="What your hospital needs, and where the network has found it."
        actions={canCreate && <Button onClick={() => setCreating(true)}>New shortage</Button>}
      />
      {body}
      {creating && <NewShortageDialog onClose={() => setCreating(false)} />}
    </>
  );
}
