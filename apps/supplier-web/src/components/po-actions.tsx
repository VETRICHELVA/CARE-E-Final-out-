// Acknowledge, Reject and Mark dispatched for one purchase order. Shown only to `po.respond`
// holders, and only the actions the hub accepts from the order's state (business-rules §8);
// the hub still checks every call and answers 409 from any other state.
import { ApiError } from "@care-e/api-client";
import { ConfirmDialog, toast, useCan } from "@care-e/ui";
import { type PoAction, type Product, type PurchaseOrder, usePoAction } from "../api";
import { actionsFor, PO_RESPOND, qty } from "../display";

type Copy = {
  trigger: string;
  title: (po: PurchaseOrder) => string;
  description: (po: PurchaseOrder, amount: string) => string;
  confirm: string;
  done: string;
  destructive?: boolean;
};

const COPY: Record<PoAction, Copy> = {
  acknowledge: {
    trigger: "Acknowledge",
    title: (po) => `Acknowledge ${po.to_org_name}'s order?`,
    description: (po, amount) =>
      `You confirm you will supply ${amount} to ${po.to_org_name}. Mark it dispatched when it leaves.`,
    confirm: "Acknowledge",
    done: "Order acknowledged.",
  },
  dispatch: {
    trigger: "Mark dispatched",
    title: (po) => `Mark ${po.to_org_name}'s order dispatched?`,
    description: (po, amount) =>
      `The hub records that ${amount} left for ${po.to_org_name} and creates the shipment.`,
    confirm: "Mark dispatched",
    done: "Order dispatched. The shipment is created.",
  },
  reject: {
    trigger: "Reject",
    title: (po) => `Reject ${po.to_org_name}'s order?`,
    description: (po) =>
      `${po.to_org_name}'s shortage goes back to matching, without you as a source.`,
    confirm: "Reject order",
    done: "Order rejected.",
    destructive: true,
  },
};

/** A hub refusal: a 409 means the order moved on, so say so; the views refetch on their own. */
async function send(run: () => Promise<unknown>, done: string) {
  try {
    await run();
    toast.success(done);
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) toast.error(e.message);
    throw e;
  }
}

export function PoActions({ po, product }: { po: PurchaseOrder; product: Product | undefined }) {
  const canRespond = useCan(PO_RESPOND);
  const mutations = {
    acknowledge: usePoAction("acknowledge"),
    dispatch: usePoAction("dispatch"),
    reject: usePoAction("reject"),
  };
  const actions = canRespond ? actionsFor(po.status) : [];
  if (actions.length === 0) return null;
  const amount = qty(po.qty, product);
  return (
    <div className="flex flex-wrap justify-end gap-2" data-testid={`po-actions-${po.id}`}>
      {actions.map((action) => {
        const copy = COPY[action];
        return (
          <ConfirmDialog
            key={action}
            trigger={copy.trigger}
            title={copy.title(po)}
            description={copy.description(po, amount)}
            confirmLabel={copy.confirm}
            destructive={copy.destructive}
            onConfirm={(reason) =>
              send(() => mutations[action].mutateAsync({ id: po.id, reason }), copy.done)
            }
          />
        );
      })}
    </div>
  );
}
