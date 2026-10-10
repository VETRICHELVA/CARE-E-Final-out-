// Delivery detail (S15 brief, hospital-web): one inbound shipment with its live status, ETA,
// carrier and status history, and its cold-chain panel (readings, the product's band and the
// hub's cold-chain events, refreshed by `coldchain.*` events). A delivered shipment links to the
// Receive screen for `receipt.record` holders. Display only: the hub decides everything shown.
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "@care-e/api-client";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ColdChainPanel,
  ColdChainStateBadge,
  EmptyState,
  ErrorState,
  formatDateTime,
  Loading,
  StatusChip,
  useCan,
  useMe,
} from "@care-e/ui";
import { useProducts, useShipment } from "../api";
import { PageHeader } from "../components/page";
import { qty } from "../display";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[9rem_1fr] sm:gap-2">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  );
}

const none = <span className="text-muted-foreground">Not yet</span>;

export function DeliveryDetailPage() {
  const id = useParams().id ?? "";
  const me = useMe().data;
  const canReceive = useCan("receipt.record");
  const query = useShipment(id);
  const products = useProducts();
  const back = (
    <Link to="/deliveries" className="text-sm text-muted-foreground hover:underline">
      ← Deliveries
    </Link>
  );

  if (query.isPending) return <Loading label="Loading delivery…" />;
  if (query.isError) {
    if (query.error instanceof ApiError && [403, 404].includes(query.error.status))
      return (
        <div className="grid gap-4">
          {back}
          <EmptyState title="Delivery not available">{query.error.message}</EmptyState>
        </div>
      );
    return <ErrorState error={query.error} />;
  }

  const s = query.data;
  const product = products.data?.byId.get(s.product_id);
  const ours = s.to_org_id === me?.org.id;
  const receivable = ours && canReceive && ["DELIVERED", "RECONCILED"].includes(s.status);
  const showColdChain = s.requires_cold_chain || s.device_id !== null || s.coldchain !== null;

  return (
    <div className="grid gap-4">
      {back}
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {s.product_name} <StatusChip state={s.status} />
            <ColdChainStateBadge summary={s.coldchain} />
          </span>
        }
        description={`${qty(s.qty, product)} from ${s.from_org_name}`}
        actions={
          receivable && (
            <Link
              to={`/deliveries/${s.id}/receive`}
              className="text-sm font-medium text-primary hover:underline"
            >
              {s.status === "DELIVERED" ? "Receive" : "View receipt"}
            </Link>
          )
        }
      />
      {s.inspection_note_required && (
        <p
          role="alert"
          className="rounded-md bg-status-danger-bg p-3 text-sm font-medium text-status-danger"
          data-testid="excursion-notice"
        >
          A cold-chain excursion is on record for this shipment. Its receipt needs an inspection
          note.
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardContent>
            <dl className="grid gap-3">
              <Row label="From">{s.from_org_name}</Row>
              <Row label="Required by">{formatDateTime(s.required_by)}</Row>
              <Row label="ETA">
                {s.eta ? (
                  <span data-testid="eta">{formatDateTime(s.eta)}</span>
                ) : s.planned_eta ? (
                  `${formatDateTime(s.planned_eta)} (planned)`
                ) : (
                  none
                )}
              </Row>
              <Row label="Carrier">{s.carrier_org_name ?? none}</Row>
              <Row label="Driver">{s.driver?.name ?? none}</Row>
              <Row label="Vehicle">
                {s.vehicle
                  ? `${s.vehicle.reg_no}${s.vehicle.has_cold_chain ? " (cold chain)" : ""}`
                  : none}
              </Row>
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Status history</CardTitle>
          </CardHeader>
          <CardContent>
            {s.status_history.length === 0 ? (
              <p className="text-sm text-muted-foreground">No changes recorded yet.</p>
            ) : (
              <ol className="grid gap-2" aria-label="Status history">
                {s.status_history.map((h, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-2 text-sm">
                    <StatusChip state={h.to_status} />
                    <span className="text-muted-foreground">{formatDateTime(h.at)}</span>
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>
      </div>
      {showColdChain && <ColdChainPanel shipmentId={s.id} />}
    </div>
  );
}
