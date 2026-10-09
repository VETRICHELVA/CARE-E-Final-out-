// Network metrics for the platform team (S20; api-and-events.md "Network metrics"). Every
// figure is the hub's, computed at request time from recorded rows; this screen only formats
// them. A PLATFORM org is in no event's `org_ids`, so nothing arrives on the event stream to
// refresh it: the user refreshes on request (no polling).
import type { ReactNode } from "react";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  formatDateTime,
  formatMoney,
  Loading,
  showNavItem,
  useMe,
} from "@care-e/ui";
import { type NetworkMetrics, useNetworkMetrics } from "../api";
import { PageHeader } from "../components/page";

/** Who sees `/admin` (the nav entry and the screen); the hub's 403 is the real check. */
export const ADMIN_GATE = { capability: "audit.read", orgTypes: ["PLATFORM"] } as const;

/** Each figure's definition, shortened from api-and-events.md "Network metrics". */
export const DEFINITIONS = {
  time: "From a shortage being reported to its first confirmed source: a hospital accepting the source request, or a supplier acknowledging the purchase order. Residual shortages count too.",
  mix: "Approved recommendations by type: transfers (single and split) against purchases.",
  cost: "Units accepted from hospital-to-hospital transfers × the cheapest supplier unit price recorded in the match run that chose the source. Before transport costs.",
  expiry:
    "Units accepted from transfers that came from a batch posted as surplus before the source request.",
  coldChain:
    "Of the cold-chain deliveries with at least one sensor reading, the share with no temperature excursion. Unmonitored deliveries never count as compliant.",
} as const;

const count = new Intl.NumberFormat("en-IN");
const percent = new Intl.NumberFormat("en-IN", { style: "percent", maximumFractionDigits: 1 });
const n = (value: number) => count.format(value);
const plural = (value: number, one: string, many = `${one}s`) =>
  `${n(value)} ${value === 1 ? one : many}`;

/** 42 → "42 min", 185 → "3 h 5 min", 3000 → "2 d 2 h". */
export function formatMinutes(minutes: number): string {
  const total = Math.round(minutes);
  if (total < 60) return `${total} min`;
  const days = Math.floor(total / 1440);
  const hours = Math.floor((total % 1440) / 60);
  const mins = total % 60;
  if (days > 0) return hours > 0 ? `${days} d ${hours} h` : `${days} d`;
  return mins > 0 ? `${hours} h ${mins} min` : `${hours} h`;
}

function Metric({
  id,
  title,
  value,
  detail,
  definition,
}: {
  id: string;
  title: string;
  value: ReactNode;
  detail: ReactNode;
  definition: string;
}) {
  return (
    <Card data-testid={`metric-${id}`} role="region" aria-label={title}>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-2xl font-semibold tabular-nums" data-testid="value">
          {value}
        </p>
        <p className="text-sm" data-testid="detail">
          {detail}
        </p>
        <p className="text-xs text-muted-foreground" data-testid="definition">
          {definition}
        </p>
      </CardContent>
    </Card>
  );
}

function Metrics({ m }: { m: NetworkMetrics }) {
  const time = m.time_to_confirmed_source;
  const mix = m.resolution_mix;
  const cost = m.procurement_cost_avoided;
  const cold = m.cold_chain;
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <Metric
        id="time"
        title="Median time to a confirmed source"
        value={time.median_minutes === null ? "—" : formatMinutes(time.median_minutes)}
        detail={
          time.shortages_confirmed === 0
            ? `No shortage has a confirmed source yet (${plural(time.shortages_reported, "shortage")} reported).`
            : `${n(time.shortages_confirmed)} of ${plural(time.shortages_reported, "shortage")} reported have a confirmed source.`
        }
        definition={DEFINITIONS.time}
      />
      <Metric
        id="mix"
        title="Transfers vs purchases"
        value={
          mix.transfer_share === null || mix.purchase_share === null
            ? "—"
            : `${percent.format(mix.transfer_share)} transfers · ${percent.format(mix.purchase_share)} purchases`
        }
        detail={
          mix.transfers + mix.purchases === 0
            ? "No recommendation has been approved yet."
            : `${plural(mix.transfers, "approved transfer")}, ${plural(mix.purchases, "approved purchase")}.`
        }
        definition={DEFINITIONS.mix}
      />
      <Metric
        id="cost"
        title="Procurement cost avoided"
        value={formatMoney(cost.paise)}
        detail={
          <>
            On {plural(cost.units_priced, "unit")} received from hospital transfers.
            {cost.units_unpriced > 0 && (
              <>
                {" "}
                {plural(cost.units_unpriced, "more unit")} had no supplier price on record and are
                left out.
              </>
            )}
          </>
        }
        definition={DEFINITIONS.cost}
      />
      <Metric
        id="expiry"
        title="Units saved from expiry"
        value={n(m.units_saved_from_expiry.units)}
        detail="Surplus stock that reached another hospital instead of expiring."
        definition={DEFINITIONS.expiry}
      />
      <Metric
        id="cold-chain"
        title="Cold-chain compliance"
        value={cold.compliance_rate === null ? "—" : percent.format(cold.compliance_rate)}
        detail={
          cold.deliveries === 0
            ? "No cold-chain delivery yet."
            : `${n(cold.monitored)} of ${plural(cold.deliveries, "cold-chain delivery", "cold-chain deliveries")} monitored; ${n(cold.with_excursion)} with an excursion; ${n(cold.with_device_silent)} with a silent device (a gap, not a breach).`
        }
        definition={DEFINITIONS.coldChain}
      />
    </div>
  );
}

export function AdminPage() {
  const me = useMe().data;
  const allowed = showNavItem(me, ADMIN_GATE);
  const metrics = useNetworkMetrics(allowed);

  if (!allowed) {
    return (
      <EmptyState title="Network metrics are for the CARE-E platform team">
        Only platform users who can read the audit trail see them.
      </EmptyState>
    );
  }
  return (
    <>
      <PageHeader
        title="Network metrics"
        description={
          metrics.data
            ? `Across every organization, computed by the hub from what was recorded. As of ${formatDateTime(metrics.data.computed_at)}.`
            : "Across every organization, computed by the hub from what was recorded."
        }
        actions={
          <Button
            variant="outline"
            onClick={() => void metrics.refetch()}
            disabled={metrics.isFetching}
          >
            {metrics.isFetching && !metrics.isPending ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />
      {metrics.isPending ? (
        <Loading />
      ) : metrics.isError ? (
        <ErrorState error={metrics.error} />
      ) : metrics.data.time_to_confirmed_source.shortages_reported === 0 ? (
        <EmptyState title="Nothing to measure yet">
          No shortage has been reported in the network yet. The figures appear once hospitals start
          reporting.
        </EmptyState>
      ) : (
        <Metrics m={metrics.data} />
      )}
    </>
  );
}
