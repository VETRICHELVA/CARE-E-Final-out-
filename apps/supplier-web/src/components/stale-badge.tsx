import { Badge } from "@care-e/ui";
import type { Offer } from "../api";
import { daysSince, isStaleOffer } from "../display";

/** "Not updated in 9 days": the hub's freshness gate skips this offer in matching. */
export function StaleBadge({ offer, now }: { offer: Offer; now: number }) {
  if (!isStaleOffer(offer.updated_at, now)) return null;
  return (
    <Badge variant="destructive" data-testid="stale">
      Not updated in {daysSince(offer.updated_at, now)} days
    </Badge>
  );
}
