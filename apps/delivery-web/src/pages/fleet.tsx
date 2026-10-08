// Fleet (apps-ai-iot.md, delivery-web `/fleet`): this org's drivers, vehicles and cold boxes
// (battery, last seen, the shipment each rides with), with device assignment (S14).
import type { ReactNode } from "react";
import { Link } from "react-router";
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ErrorState,
  formatDateTime,
  Loading,
  PageHeader,
  useCan,
  useNow,
} from "@care-e/ui";
import {
  type Device,
  type Shipment,
  useDevices,
  useDrivers,
  useOnTheWay,
  useVehicles,
} from "../api";
import { AttachDeviceToShipmentPicker, TakeOffDevice } from "../components/device-actions";
import { ColdChainBadge } from "../components/shipment-summary";
import { ago, SHIPMENT_ASSIGN } from "../display";

function Section({
  title,
  query,
  empty,
  children,
}: {
  title: string;
  query: { isPending: boolean; isError: boolean; error: unknown; data?: unknown[] };
  empty: string;
  children: ReactNode;
}) {
  let body: ReactNode = children;
  if (query.isPending) body = <Loading label={`Loading ${title.toLowerCase()}…`} />;
  else if (query.isError) body = <ErrorState error={query.error} />;
  else if (query.data?.length === 0)
    body = <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  );
}

const label = (s: Shipment) => `${s.product_name} to ${s.to_org_name}`;

function DeviceRow({
  device,
  onTheWay,
  now,
}: {
  device: Device;
  onTheWay: Shipment[];
  now: number;
}) {
  const carrying = device.assigned_shipment_id;
  const shipment = onTheWay.find((s) => s.id === carrying);
  const shipmentName = shipment ? label(shipment) : "its shipment";
  return (
    <li
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b py-3 last:border-0"
      data-testid={`device-${device.device_id}`}
    >
      <div className="min-w-32">
        <div className="font-medium">{device.device_id}</div>
        <div className="text-xs text-muted-foreground">
          {device.type === "COLD_BOX" ? "Cold box" : device.type}
        </div>
      </div>
      <div className="text-sm">
        <span className="text-muted-foreground">Battery </span>
        <span data-testid="battery">
          {device.battery_level !== null ? `${device.battery_level}%` : "Unknown"}
        </span>
      </div>
      <div className="text-sm">
        <span className="text-muted-foreground">Last seen </span>
        {device.last_seen ? (
          <span data-testid="last-seen" title={formatDateTime(device.last_seen)}>
            {ago(device.last_seen, now)}
          </span>
        ) : (
          <span data-testid="last-seen">Never</span>
        )}
      </div>
      <div className="text-sm" data-testid="device-shipment">
        {carrying ? (
          <Link to={`/shipments/${carrying}`} className="text-primary hover:underline">
            {shipment ? label(shipment) : "On a shipment"}
          </Link>
        ) : (
          <span className="text-muted-foreground">Not on a shipment</span>
        )}
      </div>
      <div className="ml-auto flex flex-wrap gap-2">
        {carrying ? (
          <TakeOffDevice device={device} shipmentLabel={shipmentName} />
        ) : (
          <AttachDeviceToShipmentPicker
            device={device}
            shipments={onTheWay
              .filter((s) => s.device_id === null)
              .map((s) => ({
                value: s.id,
                label: `${label(s)} (${s.driver?.name ?? "no driver"})`,
              }))}
          />
        )}
      </div>
    </li>
  );
}

export function FleetPage() {
  const canAssign = useCan(SHIPMENT_ASSIGN);
  const drivers = useDrivers(canAssign);
  const vehicles = useVehicles(canAssign);
  const devices = useDevices(canAssign);
  const onTheWay = useOnTheWay(canAssign);
  const now = useNow(60_000);

  if (!canAssign)
    return (
      <>
        <PageHeader title="Fleet" />
        <ErrorState error={new Error("Only dispatchers can see the fleet.")} />
      </>
    );

  return (
    <>
      <PageHeader title="Fleet" description="Your drivers, vehicles and cold boxes." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Drivers" query={drivers} empty="No drivers yet.">
          <ul>
            {drivers.data?.map((d) => (
              <li
                key={d.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b py-2 last:border-0"
                data-testid={`driver-${d.name}`}
              >
                <span className="font-medium">{d.name}</span>
                <a href={`tel:${d.phone}`} className="text-sm text-primary hover:underline">
                  {d.phone}
                </a>
                <Badge variant={d.active ? "secondary" : "outline"} className="ml-auto">
                  {d.active ? "Active" : "Inactive"}
                </Badge>
              </li>
            ))}
          </ul>
        </Section>
        <Section title="Vehicles" query={vehicles} empty="No vehicles yet.">
          <ul>
            {vehicles.data?.map((v) => (
              <li
                key={v.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b py-2 last:border-0"
                data-testid={`vehicle-${v.reg_no}`}
              >
                <span className="font-medium">{v.reg_no}</span>
                <span className="ml-auto">
                  {v.has_cold_chain ? (
                    <ColdChainBadge />
                  ) : (
                    <span className="text-sm text-muted-foreground">No cold chain</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      </div>
      <div className="mt-4">
        <Section title="Cold boxes" query={devices} empty="No cold boxes registered.">
          <ul>
            {devices.data?.map((d) => (
              <DeviceRow key={d.id} device={d} onTheWay={onTheWay.data ?? []} now={now} />
            ))}
          </ul>
        </Section>
      </div>
    </>
  );
}
