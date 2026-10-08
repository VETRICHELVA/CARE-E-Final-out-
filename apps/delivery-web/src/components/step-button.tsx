// The driver's one big button: the next step the hub accepts from the shipment's state
// (business-rules §8). Only the assigned driver sees it; the hub still checks (403, 409).
import { ApiError } from "@care-e/api-client";
import { ConfirmDialog, type Product, toast } from "@care-e/ui";
import { type Shipment, type ShipmentStatus, useMoveShipment } from "../api";
import { nextStep, place, qty, STEP_LABEL } from "../display";

type Copy = { title: string; description: string; done: string };

function copyFor(to: ShipmentStatus, s: Shipment, amount: string): Copy {
  switch (to) {
    case "PICKED_UP":
      return {
        title: `Record the pickup at ${place(s.from_org_name, s.pickup)}?`,
        description: `Confirm you have collected ${amount} of ${s.product_name} from ${s.from_org_name}.`,
        done: "Pickup recorded.",
      };
    case "IN_TRANSIT":
      return {
        title: "Mark the shipment in transit?",
        description: `You are on the way to ${place(s.to_org_name, s.drop)}.`,
        done: "Marked in transit.",
      };
    default:
      return {
        title: `Record the delivery at ${place(s.to_org_name, s.drop)}?`,
        description: `Confirm you have handed ${amount} of ${s.product_name} to ${s.to_org_name}. ${s.to_org_name} records what it received.`,
        done: "Delivery recorded.",
      };
  }
}

export function StepButton({
  shipment,
  product,
}: {
  shipment: Shipment;
  product: Product | undefined;
}) {
  const move = useMoveShipment();
  const to = nextStep(shipment.status);
  if (!to) return null;
  const copy = copyFor(to, shipment, qty(shipment.qty, product));
  return (
    <ConfirmDialog
      trigger={STEP_LABEL[to] ?? to}
      title={copy.title}
      description={copy.description}
      confirmLabel={STEP_LABEL[to] ?? to}
      triggerClassName="h-14 w-full text-base"
      onConfirm={async (reason) => {
        try {
          await move.mutateAsync({ id: shipment.id, status: to, reason });
          toast.success(copy.done);
        } catch (e) {
          // A 409 means the shipment moved on; the views refetch on their own.
          if (e instanceof ApiError && e.status === 409) toast.error(e.message);
          throw e;
        }
      }}
    />
  );
}
