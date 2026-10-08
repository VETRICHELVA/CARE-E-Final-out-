// Dispatch board (apps-ai-iot.md, delivery-web `/`): unassigned shipments to give a driver and
// a vehicle, with a status filter to follow the ones already on the road. A driver-only user
// is sent to their jobs.
import { Link, Navigate, useSearchParams } from "react-router";
import {
  Button,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  LoadMore,
  Loading,
  PageHeader,
  STATUS,
  useCan,
  useProducts,
} from "@care-e/ui";
import { type ShipmentStatus, useShipments } from "../api";
import { AssignDialog } from "../components/assign-dialog";
import { ShipmentSummary } from "../components/shipment-summary";
import { SHIPMENT_ASSIGN, SHIPMENT_STATUSES, SHIPMENT_UPDATE_STATUS } from "../display";

const BOARD: ShipmentStatus = "CREATED";

const asStatus = (value: string | null): ShipmentStatus =>
  SHIPMENT_STATUSES.find((s) => s === value) ?? BOARD;

export function DispatchPage() {
  const canAssign = useCan(SHIPMENT_ASSIGN);
  const isDriver = useCan(SHIPMENT_UPDATE_STATUS);
  const [params, setParams] = useSearchParams();
  const status = asStatus(params.get("status"));
  const shipments = useShipments(status);
  const products = useProducts();

  if (!canAssign && isDriver) return <Navigate to="/driver" replace />;

  const filters = (
    <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Status">
      {SHIPMENT_STATUSES.map((s) => (
        <Button
          key={s}
          size="sm"
          variant={s === status ? "default" : "outline"}
          aria-pressed={s === status}
          onClick={() => setParams(s === BOARD ? {} : { status: s })}
        >
          {s === BOARD ? "Unassigned" : STATUS[s]?.label}
        </Button>
      ))}
    </div>
  );

  let body;
  if (shipments.isPending) body = <Loading label="Loading shipments…" />;
  else if (shipments.isError) body = <ErrorState error={shipments.error} />;
  else {
    const items = shipments.data.pages.flatMap((p) => p.items);
    body =
      items.length === 0 ? (
        <EmptyState title={status === BOARD ? "Nothing to dispatch" : "No shipments here"}>
          {status === BOARD
            ? "New shipments appear here as soon as a transfer or purchase is approved."
            : "Shipments in this state appear here."}
        </EmptyState>
      ) : (
        <>
          <ul className="grid gap-3 md:grid-cols-2">
            {items.map((shipment) => (
              <li key={shipment.id} data-testid={`shipment-${shipment.id}`}>
                <Card className="h-full py-4">
                  <CardContent className="grid gap-3 px-4">
                    <ShipmentSummary
                      shipment={shipment}
                      product={products.data?.byId.get(shipment.product_id)}
                      showStatus={status !== BOARD}
                    />
                    {shipment.driver && (
                      <p className="text-sm text-muted-foreground">
                        {shipment.driver.name}
                        {shipment.vehicle ? `, ${shipment.vehicle.reg_no}` : ""}
                      </p>
                    )}
                    <div className="flex flex-wrap items-center gap-2">
                      {canAssign && shipment.status === "CREATED" && (
                        <AssignDialog shipment={shipment} />
                      )}
                      <Link
                        to={`/shipments/${shipment.id}`}
                        className="ml-auto text-sm text-primary hover:underline"
                      >
                        Details
                      </Link>
                    </div>
                  </CardContent>
                </Card>
              </li>
            ))}
          </ul>
          <LoadMore
            hasNextPage={shipments.hasNextPage}
            isFetchingNextPage={shipments.isFetchingNextPage}
            fetchNextPage={shipments.fetchNextPage}
          />
        </>
      );
  }

  return (
    <>
      <PageHeader
        title="Dispatch board"
        description="Shipments waiting for a driver and a vehicle, and the ones on their way."
      />
      {filters}
      {body}
    </>
  );
}
