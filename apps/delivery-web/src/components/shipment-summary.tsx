// What every shipment card shows: product, quantity, pickup → drop, deadline and flags.
import type { ReactNode } from "react";
import { Badge, ColdChainStateBadge, formatDateTime, type Product, StatusChip } from "@care-e/ui";
import type { Shipment } from "../api";
import { place, qty } from "../display";

export function ColdChainBadge() {
  return (
    <Badge variant="outline" className="border-sky-300 bg-sky-50 text-sky-800">
      Cold chain
    </Badge>
  );
}

/** Cold chain, priority and, once the hub has recorded one, the newest cold-chain event (S15). */
export function Flags({
  shipment,
}: {
  shipment: Pick<Shipment, "requires_cold_chain" | "priority"> &
    Partial<Pick<Shipment, "coldchain">>;
}) {
  return (
    <>
      {shipment.requires_cold_chain && <ColdChainBadge />}
      {shipment.priority === "CRITICAL" && <Badge variant="destructive">Critical</Badge>}
      <ColdChainStateBadge summary={shipment.coldchain ?? null} />
    </>
  );
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="w-16 shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

export function ShipmentSummary({
  shipment,
  product,
  showStatus = true,
}: {
  shipment: Shipment;
  product: Product | undefined;
  showStatus?: boolean;
}) {
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{shipment.product_name}</span>
        <span className="font-semibold">{qty(shipment.qty, product)}</span>
        {showStatus && <StatusChip state={shipment.status} />}
        <Flags shipment={shipment} />
      </div>
      <dl className="grid gap-1 text-sm">
        <Line label="Pickup">{place(shipment.from_org_name, shipment.pickup)}</Line>
        <Line label="Drop">{place(shipment.to_org_name, shipment.drop)}</Line>
        <Line label="Needed by">{formatDateTime(shipment.required_by)}</Line>
        {shipment.eta ? (
          <Line label="ETA">{formatDateTime(shipment.eta)}</Line>
        ) : (
          shipment.planned_eta && (
            <Line label="Planned">{formatDateTime(shipment.planned_eta)}</Line>
          )
        )}
      </dl>
    </div>
  );
}
