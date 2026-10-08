// Assign a driver and a vehicle to an unassigned shipment (apps-ai-iot.md, Dispatch board).
// Only this org's active drivers are offered, and for a cold-chain shipment only cold-chain
// vehicles. The hub checks both again: its refusal (e.g. a vehicle whose cold chain was
// removed since the list loaded) is shown in its own words.
import { type FormEvent, useId, useState } from "react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  ErrorState,
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  Loading,
  NativeSelect,
  Textarea,
  toast,
} from "@care-e/ui";
import { type Shipment, useAssign, useDrivers, useVehicles } from "../api";
import { vehiclesFor } from "../display";

function AssignForm({ shipment, onDone }: { shipment: Shipment; onDone: () => void }) {
  const id = useId();
  const drivers = useDrivers();
  const vehicles = useVehicles();
  const assign = useAssign();
  const [driverId, setDriverId] = useState("");
  const [vehicleId, setVehicleId] = useState("");
  const [reason, setReason] = useState("");

  if (drivers.isPending || vehicles.isPending) return <Loading label="Loading your fleet…" />;
  if (drivers.isError) return <ErrorState error={drivers.error} />;
  if (vehicles.isError) return <ErrorState error={vehicles.error} />;

  const activeDrivers = drivers.data.filter((d) => d.active);
  const offered = vehiclesFor(shipment, vehicles.data);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    assign.mutate(
      { id: shipment.id, driverId, vehicleId, reason: reason.trim() || undefined },
      {
        onSuccess: () => {
          toast.success("Shipment assigned. The hub worked out the route and ETA.");
          onDone();
        },
      },
    );
  };

  return (
    <form onSubmit={submit}>
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor={`${id}-driver`}>Driver</FieldLabel>
          <NativeSelect
            id={`${id}-driver`}
            value={driverId}
            onChange={(e) => setDriverId(e.target.value)}
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
            onChange={(e) => setVehicleId(e.target.value)}
            required
          >
            <option value="">Choose a vehicle</option>
            {offered.map((v) => (
              <option key={v.id} value={v.id}>
                {v.reg_no}
                {v.has_cold_chain ? " (cold chain)" : ""}
              </option>
            ))}
          </NativeSelect>
          {shipment.requires_cold_chain && (
            <FieldDescription>
              {offered.length === 0
                ? "This shipment needs a cold-chain vehicle, and your fleet has none."
                : "This shipment needs a cold-chain vehicle; only those are listed."}
            </FieldDescription>
          )}
        </Field>
        <Field>
          <FieldLabel htmlFor={`${id}-reason`}>Reason (optional)</FieldLabel>
          <Textarea
            id={`${id}-reason`}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
        {assign.error && <FieldError>{assign.error.message}</FieldError>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onDone} disabled={assign.isPending}>
            Back
          </Button>
          <Button type="submit" disabled={assign.isPending || !driverId || !vehicleId}>
            {assign.isPending ? "Assigning…" : "Assign"}
          </Button>
        </DialogFooter>
      </FieldGroup>
    </form>
  );
}

export function AssignDialog({ shipment }: { shipment: Shipment }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>Assign</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Assign {shipment.product_name}</DialogTitle>
          <DialogDescription>
            {shipment.from_org_name} to {shipment.to_org_name}. Choose who drives and in which
            vehicle; the hub plans the route.
          </DialogDescription>
        </DialogHeader>
        {/* Mounted only while open, so the form starts empty each time. */}
        {open && <AssignForm shipment={shipment} onDone={() => setOpen(false)} />}
      </DialogContent>
    </Dialog>
  );
}
