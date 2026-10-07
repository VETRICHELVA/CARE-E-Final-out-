// Inventory (apps-ai-iot.md, hospital-web): batches with every quantity column. Transferable is
// the hub's figure (business-rules.md §2): emphasized, read-only, never computed here.
import { useState } from "react";
import {
  Button,
  can,
  EmptyState,
  ErrorState,
  formatDateTime,
  Loading,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useMe,
} from "@care-e/ui";
import { type Batch, useBatches, useProducts } from "../api";
import { LoadMore, PageHeader } from "../components/page";
import { formatDate, qty } from "../display";
import { EditBatchDialog, ImportDialog, VerifyBatchDialog } from "./inventory-dialogs";

const TRANSFERABLE_HINT = "Computed by the hub from the batch; it cannot be edited.";

/** Batch writes need `inventory.edit` in a HOSPITAL org (api-and-events.md, S04). */
export function useCanEditInventory() {
  const me = useMe().data;
  return can(me, "inventory.edit") && me?.org.type === "HOSPITAL";
}

export function InventoryPage() {
  const canEdit = useCanEditInventory();
  const batches = useBatches();
  const products = useProducts();
  const [editing, setEditing] = useState<Batch>();
  const [verifying, setVerifying] = useState<Batch>();
  const [importing, setImporting] = useState(false);

  const header = (
    <PageHeader
      title="Inventory"
      description="Only transferable stock is ever offered to the network."
      actions={canEdit && <Button onClick={() => setImporting(true)}>Import CSV</Button>}
    />
  );

  let body;
  if (batches.isPending || products.isPending) body = <Loading label="Loading batches…" />;
  else if (batches.isError) body = <ErrorState error={batches.error} />;
  else if (products.isError) body = <ErrorState error={products.error} />;
  else {
    const rows = batches.data.pages.flatMap((p) => p.items);
    body =
      rows.length === 0 ? (
        <EmptyState title="No batches yet">
          {canEdit ? "Import a CSV file to add your stock." : "Your store has not added stock yet."}
        </EmptyState>
      ) : (
        <>
          <div className="rounded-lg border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Product</TableHead>
                  <TableHead>Batch</TableHead>
                  <TableHead className="text-right">On hand</TableHead>
                  <TableHead className="text-right">Reserved</TableHead>
                  <TableHead className="text-right">Allocated</TableHead>
                  <TableHead className="text-right">Safety</TableHead>
                  <TableHead className="text-right">Quarantined</TableHead>
                  <TableHead
                    className="bg-primary/10 text-right font-semibold text-primary"
                    title={TRANSFERABLE_HINT}
                  >
                    Transferable
                  </TableHead>
                  <TableHead>Expiry</TableHead>
                  <TableHead>Last verified</TableHead>
                  {canEdit && <TableHead className="sr-only">Actions</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((b) => {
                  const product = products.data.byId.get(b.product_id);
                  return (
                    <TableRow key={b.id}>
                      <TableCell>
                        <div className="font-medium">{product?.name ?? "Unknown product"}</div>
                        <div className="text-xs text-muted-foreground">{product?.code}</div>
                      </TableCell>
                      <TableCell>{b.batch_no}</TableCell>
                      <TableCell className="text-right">{qty(b.on_hand, product)}</TableCell>
                      <TableCell className="text-right">{qty(b.reserved, product)}</TableCell>
                      <TableCell className="text-right">{qty(b.allocated, product)}</TableCell>
                      <TableCell className="text-right">{qty(b.safety_stock, product)}</TableCell>
                      <TableCell className="text-right">{qty(b.quarantined, product)}</TableCell>
                      <TableCell
                        className="bg-primary/5 text-right font-semibold text-primary"
                        title={TRANSFERABLE_HINT}
                        aria-readonly
                        data-testid="transferable"
                      >
                        {qty(b.transferable, product)}
                      </TableCell>
                      <TableCell>{formatDate(b.expiry_date)}</TableCell>
                      <TableCell>
                        {b.last_verified_at ? formatDateTime(b.last_verified_at) : "Never"}
                      </TableCell>
                      {canEdit && (
                        <TableCell className="space-x-1 text-right">
                          <Button size="sm" variant="outline" onClick={() => setEditing(b)}>
                            Edit
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => setVerifying(b)}>
                            Verify
                          </Button>
                        </TableCell>
                      )}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <LoadMore {...batches} />
        </>
      );
  }

  return (
    <>
      {header}
      {body}
      {editing && (
        <EditBatchDialog
          batch={editing}
          product={products.data?.byId.get(editing.product_id)}
          onClose={() => setEditing(undefined)}
        />
      )}
      {verifying && (
        <VerifyBatchDialog
          batch={verifying}
          product={products.data?.byId.get(verifying.product_id)}
          onClose={() => setVerifying(undefined)}
        />
      )}
      {importing && <ImportDialog onClose={() => setImporting(false)} />}
    </>
  );
}
