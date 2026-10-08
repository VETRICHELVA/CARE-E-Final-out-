// Shipment detail (apps-ai-iot.md, delivery-web `/shipments/:id`): the hub's route on a map,
// ETA, status history and the driver's live position (refreshed by `shipment.location`
// events), plus the cold box riding with it. Actions appear only for who may take them.
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "@care-e/api-client";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  formatDateTime,
  Loading,
  PageHeader,
  StatusChip,
  toast,
  useCan,
  useMe,
  useProducts,
} from "@care-e/ui";
import { type ShipmentDetail, useDevices, useMyJobs, useShipment, useUnassign } from "../api";
import { AssignDialog } from "../components/assign-dialog";
import { AttachDeviceToShipment, TakeOffDevice } from "../components/device-actions";
import { RouteMap } from "../components/route-map";
import { Flags } from "../components/shipment-summary";
import { StepButton } from "../components/step-button";
import {
  ARRIVED,
  formatKm,
  place,
  qty,
  ROUTE_SOURCE,
  SHIPMENT_ASSIGN,
  SHIPMENT_UPDATE_STATUS,
} from "../display";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[9rem_1fr] sm:gap-2">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  );
}

const none = <span className="text-muted-foreground">Not yet</span>;

function ColdBox({ shipment, canAssign }: { shipment: ShipmentDetail; canAssign: boolean }) {
  const devices = useDevices(canAssign);
  const label = `${shipment.product_name} to ${shipment.to_org_name}`;
  if (!canAssign)
    return (
      <p className="text-sm" data-testid="cold-box">
        {shipment.device_id ? "A cold box rides with this shipment." : "No cold box is attached."}
      </p>
    );
  if (devices.isPending) return <Loading label="Loading cold boxes…" />;
  if (devices.isError) return <ErrorState error={devices.error} />;
  const device = devices.data.find((d) => d.id === shipment.device_id);
  const arrived = ARRIVED.includes(shipment.status);
  return (
    <div className="flex flex-wrap items-center gap-3">
      <p className="text-sm" data-testid="cold-box">
        {device
          ? `${device.device_id}${device.battery_level !== null ? `, battery ${device.battery_level}%` : ""}`
          : shipment.device_id
            ? "A cold box of another organization rides with this shipment."
            : "No cold box is attached."}
      </p>
      <div className="ml-auto flex flex-wrap gap-2">
        {device && <TakeOffDevice device={device} shipmentLabel={label} />}
        {!shipment.device_id && !arrived && (
          <AttachDeviceToShipment
            shipmentId={shipment.id}
            shipmentLabel={label}
            devices={devices.data}
          />
        )}
      </div>
    </div>
  );
}

function Unassign({ shipment }: { shipment: ShipmentDetail }) {
  const unassign = useUnassign();
  return (
    <ConfirmDialog
      trigger="Unassign"
      title={`Unassign ${shipment.driver?.name ?? "the driver"}?`}
      description="The route and ETA are cleared, and the shipment goes back on every logistics partner's dispatch board."
      confirmLabel="Unassign"
      destructive
      onConfirm={async (reason) => {
        await unassign.mutateAsync({ id: shipment.id, reason });
        toast.success("Shipment unassigned.");
      }}
    />
  );
}

export function ShipmentDetailPage() {
  const { id = "" } = useParams();
  const me = useMe().data;
  const canAssign = useCan(SHIPMENT_ASSIGN);
  const isDriver = useCan(SHIPMENT_UPDATE_STATUS);
  const query = useShipment(id);
  const products = useProducts();
  const jobs = useMyJobs(isDriver);
  const back = (
    <Link
      to={canAssign || !isDriver ? "/" : "/driver"}
      className="text-sm text-primary hover:underline"
    >
      {canAssign || !isDriver ? "Dispatch board" : "My jobs"}
    </Link>
  );

  if (query.isPending) return <Loading label="Loading shipment…" />;
  if (query.isError) {
    if (query.error instanceof ApiError && [403, 404].includes(query.error.status))
      return (
        <EmptyState title="Shipment not available">
          {query.error.message} {back}
        </EmptyState>
      );
    return <ErrorState error={query.error} />;
  }

  const s = query.data;
  const product = products.data?.byId.get(s.product_id);
  const mine = jobs.data?.some((j) => j.id === s.id) ?? false;
  const carrierIsUs = s.carrier_org_id !== null && s.carrier_org_id === me?.org.id;
  const actions = (
    <>
      {canAssign && s.status === "CREATED" && <AssignDialog shipment={s} />}
      {canAssign && s.status === "ASSIGNED" && carrierIsUs && <Unassign shipment={s} />}
    </>
  );

  return (
    <>
      <div className="mb-2">{back}</div>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {s.product_name} <StatusChip state={s.status} /> <Flags shipment={s} />
          </span>
        }
        description={`${qty(s.qty, product)}, ${s.from_org_name} to ${s.to_org_name}`}
        actions={actions}
      />
      {isDriver && mine && (
        <div className="mb-4 max-w-md">
          <StepButton shipment={s} product={product} />
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
        <Card className="overflow-hidden py-0">
          <RouteMap shipment={s} />
        </Card>
        <Card>
          <CardContent>
            <dl className="grid gap-3">
              <Row label="Pickup">{place(s.from_org_name, s.pickup)}</Row>
              <Row label="Drop">{place(s.to_org_name, s.drop)}</Row>
              <Row label="Needed by">{formatDateTime(s.required_by)}</Row>
              <Row label="Planned ETA">{s.planned_eta ? formatDateTime(s.planned_eta) : none}</Row>
              <Row label="ETA">
                {s.eta ? <span data-testid="eta">{formatDateTime(s.eta)}</span> : none}
              </Row>
              <Row label="Route">
                {s.route_distance_km !== null ? (
                  <span data-testid="route">
                    {formatKm(s.route_distance_km)}
                    {s.route_provider ? `, ${ROUTE_SOURCE[s.route_provider]}` : ""}
                  </span>
                ) : (
                  "Planned when a driver is assigned"
                )}
              </Row>
              <Row label="Driver">{s.driver?.name ?? none}</Row>
              <Row label="Vehicle">
                {s.vehicle
                  ? `${s.vehicle.reg_no}${s.vehicle.has_cold_chain ? " (cold chain)" : ""}`
                  : none}
              </Row>
              <Row label="Carrier">{s.carrier_org_name ?? none}</Row>
              <Row label="Live position">
                {s.last_location ? (
                  <span data-testid="live-position">
                    {s.last_location.lat.toFixed(5)}, {s.last_location.lng.toFixed(5)} at{" "}
                    {formatDateTime(s.last_location.ts)}
                  </span>
                ) : (
                  "No position shared yet"
                )}
              </Row>
            </dl>
          </CardContent>
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Cold box</CardTitle>
          </CardHeader>
          <CardContent>
            <ColdBox shipment={s} canAssign={canAssign} />
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
    </>
  );
}
