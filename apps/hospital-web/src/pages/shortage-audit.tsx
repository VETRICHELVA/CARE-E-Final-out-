// Audit tab on shortage detail (apps-ai-iot.md, hospital-web; S12): the org's own audit rows
// for the shortage and the records made for it (source requests, the current recommendation,
// purchase orders, shipments), newest first. Shown as recorded: a USER reason is the user's own
// words, a SYSTEM one the hub's; a row mirrored from another org (business-rules.md §10) is that
// org's action, not the system's. Needs `audit.read`.
import type { ReactNode } from "react";
import { Badge, EmptyState, ErrorState, formatDateTime, Loading, useMe } from "@care-e/ui";
import {
  type AuditRow,
  type Shortage,
  type ShipmentDetail,
  type SourceRequest,
  useAuditTrail,
  useCurrentRecommendationId,
  useLatestRun,
  useRecommendation,
  useShipmentDetails,
  useShortageRecordRows,
} from "../api";
import { RecordedReason } from "../components/page";
import { ENTITY_LABELS, isMirrored, statusLabel } from "../display";
import { useShortageRequests } from "./source-requests-panel";

const str = (v: unknown) => (typeof v === "string" ? v : undefined);

/** What happened, from the row's action and its before/after status. */
function describe(row: AuditRow) {
  const what = ENTITY_LABELS[row.entity] ?? row.entity;
  const from = row.before?.status;
  const to = row.after?.status;
  if (row.action === `${row.entity}.created`)
    return to ? `${what} created (${statusLabel(to)})` : `${what} created`;
  if (row.action === `${row.entity}.status_changed` && from && to)
    return `${what}: ${statusLabel(from)} → ${statusLabel(to)}`;
  return `${what}: ${row.action}`;
}

type Context = {
  requests: Map<string, SourceRequest>;
  shipments: Map<string, ShipmentDetail>;
  orderSupplier: Map<string, string>;
  orgNames: Map<string, string>;
};

/** The org whose user wrote a mirrored row: the source answering a request, the supplier
 *  acting on its order or dispatching it, or the carrier moving a shipment. */
function actingOrg(row: AuditRow, ctx: Context): string {
  let orgId: string | undefined | null;
  if (row.entity === "source_request") orgId = ctx.requests.get(row.entity_id)?.source_org_id;
  else if (row.entity === "purchase_order") orgId = ctx.orderSupplier.get(row.entity_id);
  else if (row.entity === "shipment")
    orgId =
      row.action === "shipment.created"
        ? str(row.after?.from_org_id)
        : ctx.shipments.get(row.entity_id)?.carrier_org_id;
  return (orgId && ctx.orgNames.get(orgId)) || "Another organization";
}

function Who({ row, ctx }: { row: AuditRow; ctx: Context }) {
  const me = useMe().data;
  let who: ReactNode;
  if (row.actor_id !== null)
    who = row.actor_id === me?.user.id ? "You" : `${me?.org.name ?? "Your organization"} user`;
  else if (isMirrored(row))
    who = (
      <>
        {actingOrg(row, ctx)}{" "}
        <Badge variant="outline" className="ml-1">
          Other organization
        </Badge>
      </>
    );
  else who = "System";
  return (
    <span data-testid="audit-actor" className="font-medium">
      {who}
    </span>
  );
}

export function ShortageAudit({ shortage }: { shortage: Shortage }) {
  const requests = useShortageRequests(shortage.id);
  const run = useLatestRun(shortage.id);
  const current = useCurrentRecommendationId(shortage.id, run.data?.id);
  const rec = useRecommendation(current.id);
  const related = useShortageRecordRows(shortage.id);
  const shipmentIds = related.shipments.map((row) => row.entity_id);
  const shipments = useShipmentDetails(shipmentIds);
  const requestRows = requests.data?.pages.flatMap((p) => p.items) ?? [];

  const trail = useAuditTrail([
    { entity: "shortage", id: shortage.id },
    ...requestRows.map((r) => ({ entity: "source_request", id: r.id })),
    ...(current.id ? [{ entity: "recommendation", id: current.id }] : []),
    ...related.orders.map((row) => ({ entity: "purchase_order", id: row.entity_id })),
    ...shipmentIds.map((id) => ({ entity: "shipment", id })),
  ]);

  const error = requests.error ?? run.error ?? current.error ?? related.error ?? trail.error;
  if (error) return <ErrorState error={error} />;
  if (
    requests.isPending ||
    run.isPending ||
    current.isPending ||
    related.isPending ||
    trail.isPending
  )
    return <Loading label="Loading audit trail…" />;
  if (trail.rows.length === 0) return <EmptyState title="No audit rows yet" />;

  // Names of the other orgs involved, from what the hub already showed this org.
  const orgNames = new Map<string, string>();
  for (const r of requestRows) orgNames.set(r.source_org_id, r.source_org_name);
  for (const line of [...(rec.data?.lines ?? []), ...(rec.data?.alternatives ?? [])])
    orgNames.set(line.source_org_id, line.source_org_name);
  for (const s of shipments) {
    orgNames.set(s.from_org_id, s.from_org_name);
    if (s.carrier_org_id && s.carrier_org_name) orgNames.set(s.carrier_org_id, s.carrier_org_name);
  }
  const ctx: Context = {
    requests: new Map(requestRows.map((r) => [r.id, r])),
    shipments: new Map(shipments.map((s) => [s.id, s])),
    orderSupplier: new Map(
      related.orders.flatMap((row) => {
        const supplier = str(row.after?.supplier_org_id);
        return supplier ? [[row.entity_id, supplier] as const] : [];
      }),
    ),
    orgNames,
  };

  return (
    <ol aria-label="Audit trail" className="divide-y rounded-md border">
      {trail.rows.map((row) => (
        <li
          key={row.id}
          data-testid="audit-row"
          data-reason-source={row.reason_source}
          className="grid gap-1 p-3 text-sm sm:grid-cols-[11rem_1fr]"
        >
          <time dateTime={row.ts} className="text-xs text-muted-foreground">
            {formatDateTime(row.ts)}
          </time>
          <div className="grid gap-1">
            <div>
              <span className="font-medium" data-testid="audit-action">
                {describe(row)}
              </span>
              <span className="text-muted-foreground"> · by </span>
              <Who row={row} ctx={ctx} />
            </div>
            <RecordedReason reason={row.reason} source={row.reason_source} />
          </div>
        </li>
      ))}
    </ol>
  );
}
