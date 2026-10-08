// Cold-box assignment (S14): put one of this org's devices on a shipment, or take it off.
// Readings are linked to the shipment only while it is on the way; the hub decides that, and
// refuses a shipment that has arrived or already carries another device (409, shown as is).
import { type FormEvent, useId, useState } from "react";
import {
  Button,
  ConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  NativeSelect,
  toast,
} from "@care-e/ui";
import { type Device, useAssignDevice } from "../api";

type Option = { value: string; label: string };

/** A dialog with one select: `pick` names what is chosen (a device or a shipment). */
function PickDialog({
  trigger,
  title,
  description,
  pick,
  options,
  empty,
  onPick,
}: {
  trigger: string;
  title: string;
  description: string;
  pick: string;
  options: Option[];
  empty: string;
  onPick: (value: string) => Promise<unknown>;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const change = (next: boolean) => {
    if (pending) return;
    setOpen(next);
    setValue("");
    setError(undefined);
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setPending(true);
    setError(undefined);
    try {
      await onPick(value);
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setPending(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={change}>
      <DialogTrigger asChild>
        <Button variant="outline">{trigger}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <form onSubmit={(e) => void submit(e)}>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor={id}>{pick}</FieldLabel>
              <NativeSelect
                id={id}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                required
              >
                <option value="">Choose…</option>
                {options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </NativeSelect>
              {options.length === 0 && <FieldDescription>{empty}</FieldDescription>}
            </Field>
            {error && <FieldError>{error}</FieldError>}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => change(false)}
                disabled={pending}
              >
                Back
              </Button>
              <Button type="submit" disabled={pending || !value}>
                {pending ? "Working…" : "Attach"}
              </Button>
            </DialogFooter>
          </FieldGroup>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Take a device off its shipment (`shipment_id: null`), with an optional reason. */
export function TakeOffDevice({
  device,
  shipmentLabel,
}: {
  device: Device;
  shipmentLabel: string;
}) {
  const assign = useAssignDevice();
  return (
    <ConfirmDialog
      trigger="Take off"
      title={`Take ${device.device_id} off ${shipmentLabel}?`}
      description="Its readings stop being linked to the shipment."
      confirmLabel="Take off"
      destructive
      onConfirm={async (reason) => {
        await assign.mutateAsync({ deviceId: device.id, shipmentId: null, reason });
        toast.success(`${device.device_id} taken off the shipment.`);
      }}
    />
  );
}

/** Shipment detail: choose which of this org's free devices rides with the shipment. */
export function AttachDeviceToShipment({
  shipmentId,
  shipmentLabel,
  devices,
}: {
  shipmentId: string;
  shipmentLabel: string;
  devices: Device[];
}) {
  const assign = useAssignDevice();
  const free = devices.filter((d) => d.assigned_shipment_id === null);
  return (
    <PickDialog
      trigger="Attach cold box"
      title={`Attach a cold box to ${shipmentLabel}`}
      description="Its temperature readings are linked to this shipment while it is on the way."
      pick="Cold box"
      options={free.map((d) => ({ value: d.id, label: d.device_id }))}
      empty="Every cold box is on another shipment. Take one off first."
      onPick={async (deviceId) => {
        await assign.mutateAsync({ deviceId, shipmentId });
        toast.success("Cold box attached.");
      }}
    />
  );
}

/** Fleet: choose which shipment on the way a free device rides with. */
export function AttachDeviceToShipmentPicker({
  device,
  shipments,
}: {
  device: Device;
  shipments: Option[];
}) {
  const assign = useAssignDevice();
  return (
    <PickDialog
      trigger="Attach to shipment"
      title={`Attach ${device.device_id} to a shipment`}
      description="Its temperature readings are linked to the shipment while it is on the way."
      pick="Shipment"
      options={shipments}
      empty="No shipment on the way is without a cold box."
      onPick={async (shipmentId) => {
        await assign.mutateAsync({ deviceId: device.id, shipmentId });
        toast.success(`${device.device_id} attached.`);
      }}
    />
  );
}
