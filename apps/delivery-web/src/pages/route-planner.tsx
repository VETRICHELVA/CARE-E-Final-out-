// Route planner (apps-ai-iot.md, delivery-web `/plan`, S16): choose a driver, a vehicle and
// several unassigned shipments; the hub orders the stops (each pickup before its drop, every
// deadline, the cold-chain ride limit) and says why any shipment cannot fit. Apply assigns the
// ones that fit. Display only: the hub plans, checks and assigns; this page shows its answers.
import { type FormEvent, useId, useState } from "react";
import { Link } from "react-router";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ErrorState,
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  formatDateTime,
  Loading,
  NativeSelect,
  PageHeader,
  Textarea,
  toast,
  useCan,
  useProducts,
} from "@care-e/ui";
import {
  type RoutePlan,
  type Shipment,
  useApplyRoute,
  useDrivers,
  useOptimizeRoute,
  useUnassigned,
  useVehicles,
} from "../api";
import { PlanMap } from "../components/plan-map";
import { Flags } from "../components/shipment-summary";
import { MAX_ROUTE_SHIPMENTS, place, qty, SHIPMENT_ASSIGN, STOP_LABEL } from "../display";

function Choice({
  shipment,
  checked,
  disabled,
  onChange,
  unit,
}: {
  shipment: Shipment;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
  unit: ReturnType<typeof useProducts>["data"];
}) {
  return (
    <li className="border-b py-2 last:border-0">
      <label className="flex cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          className="mt-1 size-4"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="grid gap-1 text-sm">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{shipment.product_name}</span>
            <span>{qty(shipment.qty, unit?.byId.get(shipment.product_id))}</span>
            <Flags shipment={shipment} />
          </span>
          <span className="text-muted-foreground">
            {place(shipment.from_org_name, shipment.pickup)} to{" "}
            {place(shipment.to_org_name, shipment.drop)}
          </span>
          <span className="text-muted-foreground">
            Needed by {formatDateTime(shipment.required_by)}
          </span>
        </span>
      </label>
    </li>
  );
}

function PlanView({ plan, byId }: { plan: RoutePlan; byId: Map<string, Shipment> }) {
  const name = (id: string) => byId.get(id)?.product_name ?? "Shipment";
  return (
    <div className="grid gap-4">
      {plan.stops.length > 0 ? (
        <>
          <ol className="grid gap-2" aria-label="Stops">
            {plan.stops.map((stop) => (
              <li
                key={`${stop.shipment_id}-${stop.type}`}
                data-testid="stop"
                className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b pb-2 text-sm last:border-0"
              >
                <span className="w-6 font-semibold">{stop.seq}.</span>
                <Badge variant={stop.type === "PICKUP" ? "secondary" : "outline"}>
                  {STOP_LABEL[stop.type]}
                </Badge>
                <span className="font-medium">{stop.place}</span>
                <span className="text-muted-foreground">{name(stop.shipment_id)}</span>
                <span className="ml-auto">Planned {formatDateTime(stop.eta)}</span>
              </li>
            ))}
          </ol>
          <PlanMap stops={plan.stops} />
        </>
      ) : (
        <p className="text-sm text-muted-foreground">No shipment fits this route.</p>
      )}
      {plan.infeasible.length > 0 && (
        <section aria-label="Cannot fit" className="rounded-md border border-amber-300 p-3">
          <h3 className="mb-2 text-sm font-semibold">Cannot fit in this route</h3>
          <ul className="grid gap-2 text-sm">
            {plan.infeasible.map((item) => (
              <li key={item.shipment_id} data-testid="infeasible">
                <span className="font-medium">{name(item.shipment_id)}:</span> {item.reason}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export function RoutePlannerPage() {
  const id = useId();
  const canAssign = useCan(SHIPMENT_ASSIGN);
  const drivers = useDrivers(canAssign);
  const vehicles = useVehicles(canAssign);
  const unassigned = useUnassigned(canAssign);
  const products = useProducts();
  const optimize = useOptimizeRoute();
  const apply = useApplyRoute();
  const [driverId, setDriverId] = useState("");
  const [vehicleId, setVehicleId] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  // The plan, with the shipments as they were when it was asked for (so an applied plan still
  // names the shipments that have left the unassigned list since).
  const [result, setResult] = useState<{ plan: RoutePlan; byId: Map<string, Shipment> } | null>(
    null,
  );
  const [applied, setApplied] = useState(false);

  if (!canAssign)
    return (
      <>
        <PageHeader title="Route planner" />
        <ErrorState error={new Error("Only dispatchers can plan routes.")} />
      </>
    );
  if (drivers.isPending || vehicles.isPending || unassigned.isPending)
    return <Loading label="Loading your fleet and shipments…" />;
  if (drivers.isError) return <ErrorState error={drivers.error} />;
  if (vehicles.isError) return <ErrorState error={vehicles.error} />;
  if (unassigned.isError) return <ErrorState error={unassigned.error} />;

  const plan = result?.plan ?? null;
  const activeDrivers = drivers.data.filter((d) => d.active);
  const input = { driverId, vehicleId, shipmentIds: chosen };
  const ready = driverId !== "" && vehicleId !== "" && chosen.length > 0;
  const full = chosen.length >= MAX_ROUTE_SHIPMENTS;
  const fits = plan?.stops.filter((s) => s.type === "PICKUP").length ?? 0;
  const driverName = drivers.data.find((d) => d.id === driverId)?.name ?? "the driver";

  /** Any change to the choices makes a shown plan stale. */
  const changed = () => {
    setResult(null);
    setApplied(false);
    optimize.reset();
    apply.reset();
  };
  const toggle = (shipmentId: string, on: boolean) => {
    setChosen((ids) => (on ? [...ids, shipmentId] : ids.filter((x) => x !== shipmentId)));
    changed();
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    apply.reset();
    const byId = new Map(unassigned.data.map((s) => [s.id, s]));
    optimize.mutate(input, {
      onSuccess: (out) => {
        setResult({ plan: out, byId });
        setApplied(false);
      },
    });
  };
  const applyPlan = () =>
    apply.mutate(
      { ...input, reason: reason.trim() || undefined },
      {
        onSuccess: (out) => {
          const n = out.assigned_shipment_ids.length;
          toast.success(`Assigned ${n} shipment${n === 1 ? "" : "s"} to ${driverName}.`);
          setResult((r) => ({ plan: out, byId: r?.byId ?? new Map() }));
          setApplied(true);
          setChosen([]);
          setReason("");
        },
      },
    );

  return (
    <>
      <PageHeader
        title="Route planner"
        description="Choose a driver, a vehicle and the shipments to carry. The hub orders the stops so every pickup comes before its drop, every deadline is met and cold-chain stock stays within its ride limit."
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Driver, vehicle and shipments</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit}>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor={`${id}-driver`}>Driver</FieldLabel>
                  <NativeSelect
                    id={`${id}-driver`}
                    value={driverId}
                    onChange={(e) => {
                      setDriverId(e.target.value);
                      changed();
                    }}
                    required
                  >
                    <option value="">Choose a driver</option>
                    {activeDrivers.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name} ({d.phone})
                      </option>
                    ))}
                  </NativeSelect>
                  {activeDrivers.length === 0 && (
                    <FieldDescription>Your organization has no active drivers.</FieldDescription>
                  )}
                </Field>
                <Field>
                  <FieldLabel htmlFor={`${id}-vehicle`}>Vehicle</FieldLabel>
                  <NativeSelect
                    id={`${id}-vehicle`}
                    value={vehicleId}
                    onChange={(e) => {
                      setVehicleId(e.target.value);
                      changed();
                    }}
                    required
                  >
                    <option value="">Choose a vehicle</option>
                    {vehicles.data.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.reg_no}
                        {v.has_cold_chain ? " (cold chain)" : ""}
                      </option>
                    ))}
                  </NativeSelect>
                  <FieldDescription>
                    Cold-chain shipments need a cold-chain vehicle.
                  </FieldDescription>
                </Field>
                <fieldset className="grid gap-2">
                  <legend className="text-sm font-medium">
                    Shipments ({chosen.length} chosen)
                  </legend>
                  {unassigned.data.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      Nothing to dispatch. New shipments appear on the{" "}
                      <Link to="/" className="text-primary hover:underline">
                        dispatch board
                      </Link>{" "}
                      when a transfer or purchase is approved.
                    </p>
                  ) : (
                    <ul className="max-h-[28rem] overflow-y-auto">
                      {unassigned.data.map((s) => (
                        <Choice
                          key={s.id}
                          shipment={s}
                          unit={products.data}
                          checked={chosen.includes(s.id)}
                          disabled={full && !chosen.includes(s.id)}
                          onChange={(on) => toggle(s.id, on)}
                        />
                      ))}
                    </ul>
                  )}
                  {full && (
                    <FieldDescription>
                      A route takes at most {MAX_ROUTE_SHIPMENTS} shipments.
                    </FieldDescription>
                  )}
                </fieldset>
                {optimize.error && <FieldError>{optimize.error.message}</FieldError>}
                <Button type="submit" disabled={!ready || optimize.isPending}>
                  {optimize.isPending ? "Planning…" : "Plan route"}
                </Button>
              </FieldGroup>
            </form>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{applied ? "Planned route assigned" : "Plan"}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            {result === null ? (
              <p className="text-sm text-muted-foreground">
                Choose a driver, a vehicle and shipments, then plan the route.
              </p>
            ) : (
              <PlanView plan={result.plan} byId={result.byId} />
            )}
            {applied && (
              <p className="text-sm">
                {driverName} has these jobs now; follow them on the{" "}
                <Link to="/?status=ASSIGNED" className="text-primary hover:underline">
                  dispatch board
                </Link>
                .
              </p>
            )}
            {plan !== null && !applied && fits > 0 && (
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor={`${id}-reason`}>Reason (optional)</FieldLabel>
                  <Textarea
                    id={`${id}-reason`}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </Field>
                {apply.error && <FieldError>{apply.error.message}</FieldError>}
                <Button onClick={applyPlan} disabled={apply.isPending}>
                  {apply.isPending
                    ? "Assigning…"
                    : `Apply: assign ${fits} shipment${fits === 1 ? "" : "s"} to ${driverName}`}
                </Button>
              </FieldGroup>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
