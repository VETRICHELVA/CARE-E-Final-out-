import { cn } from "./lib/utils";

type Tone = "neutral" | "info" | "progress" | "warning" | "success" | "danger";

// Full class names (not built from strings) so Tailwind can find them.
const TONES: Record<Tone, string> = {
  neutral: "bg-status-neutral-bg text-status-neutral",
  info: "bg-status-info-bg text-status-info",
  progress: "bg-status-progress-bg text-status-progress",
  warning: "bg-status-warning-bg text-status-warning",
  success: "bg-status-success-bg text-status-success",
  danger: "bg-status-danger-bg text-status-danger",
};

/** Every state in business-rules.md §8 (Shortage, SourceRequest, Recommendation,
 *  PurchaseOrder, Shipment) and SurplusPost (S18). A state shared by several machines means the
 *  same thing in each. */
export const STATUS: Record<string, { label: string; tone: Tone }> = {
  DRAFT: { label: "Draft", tone: "neutral" },
  OPEN: { label: "Open", tone: "info" },
  MATCHING: { label: "Matching", tone: "progress" },
  AWAITING_DECISION: { label: "Awaiting decision", tone: "warning" },
  IN_FULFILLMENT: { label: "In fulfillment", tone: "progress" },
  RECEIVED: { label: "Received", tone: "info" },
  RESOLVED: { label: "Resolved", tone: "success" },
  PARTIALLY_RESOLVED: { label: "Partially resolved", tone: "warning" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
  REQUESTED: { label: "Requested", tone: "info" },
  TENTATIVE_HOLD: { label: "Tentative hold", tone: "warning" },
  CONFIRMED: { label: "Confirmed", tone: "success" },
  DECLINED: { label: "Declined", tone: "danger" },
  EXPIRED: { label: "Expired", tone: "neutral" },
  SUPERSEDED: { label: "Superseded", tone: "neutral" },
  PENDING: { label: "Pending", tone: "warning" },
  APPROVED: { label: "Approved", tone: "success" },
  REJECTED: { label: "Rejected", tone: "danger" },
  ESCALATED: { label: "Escalated", tone: "warning" },
  SENT: { label: "Sent", tone: "info" },
  ACKNOWLEDGED: { label: "Acknowledged", tone: "progress" },
  DISPATCHED: { label: "Dispatched", tone: "progress" },
  DELIVERED: { label: "Delivered", tone: "success" },
  CREATED: { label: "Created", tone: "neutral" },
  ASSIGNED: { label: "Assigned", tone: "info" },
  PICKED_UP: { label: "Picked up", tone: "progress" },
  IN_TRANSIT: { label: "In transit", tone: "progress" },
  RECONCILED: { label: "Reconciled", tone: "success" },
  MATCHED: { label: "Matched", tone: "success" },
  WITHDRAWN: { label: "Withdrawn", tone: "neutral" },
};

/** Shows a hub state as a coloured chip. An unknown state shows as-is, in neutral. */
export function StatusChip({ state, className }: { state: string; className?: string }) {
  const { label, tone } = STATUS[state] ?? { label: state, tone: "neutral" };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        TONES[tone],
        className,
      )}
    >
      {label}
    </span>
  );
}
