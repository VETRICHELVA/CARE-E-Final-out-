// Deliveries (apps-ai-iot.md, hospital-web; S12): shipments on their way to this hospital, with
// the hub's live status and ETA. Read-only: drivers move shipments, and receiving is its own
// screen. `shipment.*` events refresh the list (no polling).
import { Link } from "react-router";
import {
  Badge,
  Countdown,
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
  useMe,
  useNow,
} from "@care-e/ui";
import { type Product, type Shipment, useProducts, useShipments } from "../api";
import { LoadMore, PageHeader } from "../components/page";
import { IN_MOTION, qty, SHORTAGE_READERS } from "../display";

/** The hub's ETA once a carrier has the shipment, else the plan's; delivered ones show when. */
function Eta({ shipment, now }: { shipment: Shipment; now: number }) {
  if (!IN_MOTION.has(shipment.status)) {
    const at = shipment.drop?.actual_at;
    return at ? <span>Delivered {formatDateTime(at)}</span> : <span>—</span>;
  }
  const eta = shipment.eta ?? shipment.planned_eta;
  if (!eta) return <span className="text-muted-foreground">Not known yet</span>;
  return (
    <div>
      <div>{formatDateTime(eta)}</div>
      <div className="text-xs text-muted-foreground">
        {shipment.eta ? (
          <Countdown to={eta} now={now} passed="ETA passed" />
        ) : (
          "Planned (no carrier yet)"
        )}
      </div>
    </div>
  );
}

function Carrier({ shipment }: { shipment: Shipment }) {
  if (!shipment.carrier_org_name)
    return <span className="text-muted-foreground">Not assigned yet</span>;
  return (
    <div>
      <div>{shipment.carrier_org_name}</div>
      {shipment.driver && (
        <div className="text-xs text-muted-foreground">
          {shipment.driver.name}
          {shipment.vehicle && ` · ${shipment.vehicle.reg_no}`}
        </div>
      )}
    </div>
  );
}

function Row({
  shipment,
  product,
  now,
  canReadShortages,
}: {
  shipment: Shipment;
  product: Product | undefined;
  now: number;
  canReadShortages: boolean;
}) {
  return (
    <TableRow data-testid={`shipment-${shipment.id}`}>
      <TableCell>
        <div className="font-medium">{shipment.product_name}</div>
        <div className="text-xs text-muted-foreground">{shipment.product_code}</div>
      </TableCell>
      <TableCell className="text-right">{qty(shipment.qty, product)}</TableCell>
      <TableCell>{shipment.from_org_name}</TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusChip state={shipment.status} />
          {shipment.requires_cold_chain && <Badge variant="outline">Cold chain</Badge>}
        </div>
      </TableCell>
      <TableCell>
        <Carrier shipment={shipment} />
      </TableCell>
      <TableCell>
        <Eta shipment={shipment} now={now} />
      </TableCell>
      <TableCell>{formatDateTime(shipment.required_by)}</TableCell>
      {canReadShortages && (
        <TableCell>
          <Link to={`/shortages/${shipment.shortage_id}`} className="text-primary hover:underline">
            Shortage
          </Link>
        </TableCell>
      )}
    </TableRow>
  );
}

export function DeliveriesPage() {
  const me = useMe().data;
  const list = useShipments();
  const products = useProducts();
  const now = useNow();
  const canReadShortages = useCan(SHORTAGE_READERS);

  let body;
  if (list.isPending) body = <Loading label="Loading deliveries…" />;
  else if (list.isError) body = <ErrorState error={list.error} />;
  else {
    // The hub lists every shipment the org is part of; this screen is the inbound ones.
    const rows = list.data.pages.flatMap((p) => p.items).filter((s) => s.to_org_id === me?.org.id);
    body =
      rows.length === 0 ? (
        <EmptyState title="No deliveries to your hospital">
          A delivery appears here once a transfer is approved or a supplier dispatches an order.
        </EmptyState>
      ) : (
        <Table aria-label="Deliveries">
          <TableHeader>
            <TableRow>
              <TableHead>Product</TableHead>
              <TableHead className="text-right">Qty</TableHead>
              <TableHead>From</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Carrier</TableHead>
              <TableHead>ETA</TableHead>
              <TableHead>Required by</TableHead>
              {canReadShortages && <TableHead>For</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((s) => (
              <Row
                key={s.id}
                shipment={s}
                product={products.data?.byId.get(s.product_id)}
                now={now}
                canReadShortages={canReadShortages}
              />
            ))}
          </TableBody>
        </Table>
      );
  }

  return (
    <div className="grid gap-4">
      <PageHeader
        title="Deliveries"
        description="Shipments to your hospital, with live status and ETA."
      />
      {body}
      {list.isSuccess && <LoadMore {...list} />}
    </div>
  );
}
