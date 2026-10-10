// Cold chain in the apps (apps-ai-iot.md; business-rules.md §11, S15). The hub evaluates the
// rules and records the events; these components only show its readings and events, word
// them from the values the hub recorded, and raise a toast when it reports a new event. Nothing
// here decides whether something is an excursion or says what happened to the stock.
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { client, type EventEnvelope, onHubEvent, type Schemas, unwrap } from "@care-e/api-client";
import { useMe } from "./auth";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { formatDateTime } from "./format";
import { cn } from "./lib/utils";
import { ErrorState, Loading } from "./states";

export type ColdChain = Schemas["ColdChainOut"];
export type ColdChainEvent = Schemas["ColdChainEventOut"];
export type ColdChainSummary = Schemas["ColdChainSummary"];
export type ColdChainEventType = Schemas["ColdChainEventType"];
type Reading = Schemas["ReadingOut"];
type Band = Schemas["BandOut"];

/** Refreshed by `coldchain.*` events (EVENT_QUERIES), so the chart is live without polling. */
export const coldChainKey = (shipmentId: string) =>
  ["/api/v1/shipments/{shipment_id}/coldchain", { shipment_id: shipmentId }] as const;

/** The shipment's readings and cold-chain events, for its from, to and carrier orgs. */
export function useColdChain(shipmentId: string, enabled = true) {
  return useQuery({
    queryKey: coldChainKey(shipmentId),
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/shipments/{shipment_id}/coldchain", {
          params: { path: { shipment_id: shipmentId } },
        }),
      ),
    enabled,
  });
}

const degrees = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

/** 9.4 → "9.4 °C". */
export const formatTemp = (c: number) => `${degrees.format(c)} °C`;

/** 150 → "2 min 30 s", 120 → "2 min", 45 → "45 s". */
export function formatSeconds(total: number): string {
  const s = Math.round(total);
  const min = Math.floor(s / 60);
  const rest = s % 60;
  if (min === 0) return `${rest} s`;
  return rest === 0 ? `${min} min` : `${min} min ${rest} s`;
}

export const COLDCHAIN_LABEL: Record<ColdChainEventType, string> = {
  EXCURSION: "Temperature excursion",
  RECOVERED: "Back in range",
  DEVICE_SILENT: "Device silent",
};

type Tone = "danger" | "warning" | "success";
const TONE: Record<ColdChainEventType, Tone> = {
  EXCURSION: "danger",
  DEVICE_SILENT: "warning",
  RECOVERED: "success",
};
const TONE_CLASS: Record<Tone, string> = {
  danger: "bg-status-danger-bg text-status-danger",
  warning: "bg-status-warning-bg text-status-warning",
  success: "bg-status-success-bg text-status-success",
};

/** One event in words, from the values the hub recorded and nothing else (CLAUDE.md rule 5).
 *  EXCURSION and RECOVERED carry °C (the reading that completed the run, and the band's bound
 *  it crossed); DEVICE_SILENT carries seconds without a reading and the limit. */
export function describeColdChainEvent(e: {
  type: ColdChainEventType;
  observed_value: number;
  threshold: number;
}): string {
  if (e.type === "DEVICE_SILENT")
    return `No reading received for ${formatSeconds(e.observed_value)} (the limit is ${formatSeconds(e.threshold)}).`;
  if (e.type === "RECOVERED")
    return `Readings back in range, latest ${formatTemp(e.observed_value)}. The excursion stays on record.`;
  const side = e.observed_value > e.threshold ? "above the maximum of" : "below the minimum of";
  return `Readings out of range: ${formatTemp(e.observed_value)}, ${side} ${formatTemp(e.threshold)}.`;
}

/** "2–8 °C", "at most -15 °C", or null when the product has no cold-chain rule. */
export function formatBand(band: Band): string | null {
  const { temp_min_c: min, temp_max_c: max } = band;
  if (min !== null && max !== null) return `${degrees.format(min)}–${degrees.format(max)} °C`;
  if (max !== null) return `at most ${formatTemp(max)}`;
  if (min !== null) return `at least ${formatTemp(min)}`;
  return null;
}

const inBand = (band: Band, t: number) =>
  (band.temp_min_c === null || t >= band.temp_min_c) &&
  (band.temp_max_c === null || t <= band.temp_max_c);

const W = 1000;
const H = 220;
const PAD = { left: 44, right: 12, top: 12, bottom: 26 };

/** The readings as a line over time, with the allowed band shaded and readings outside it
 *  marked. Display only: the hub decides what counts as an excursion. */
export function TemperatureChart({ readings, band }: { readings: Reading[]; band: Band }) {
  if (readings.length === 0)
    return (
      <p className="text-sm text-muted-foreground" data-testid="no-readings">
        No readings received for this shipment yet.
      </p>
    );
  const times = readings.map((r) => new Date(r.ts).getTime());
  const temps = readings.map((r) => r.temp_c);
  const bounds = [band.temp_min_c, band.temp_max_c].filter((b): b is number => b !== null);
  const lo = Math.min(...temps, ...bounds) - 1;
  const hi = Math.max(...temps, ...bounds) + 1;
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  const x = (t: number) =>
    t1 === t0
      ? (PAD.left + W - PAD.right) / 2
      : PAD.left + ((t - t0) / (t1 - t0)) * (W - PAD.left - PAD.right);
  const y = (c: number) => PAD.top + ((hi - c) / (hi - lo)) * (H - PAD.top - PAD.bottom);
  const bandTop = y(band.temp_max_c ?? hi);
  const bandBottom = y(band.temp_min_c ?? lo);
  const last = readings.at(-1)!;
  const points = readings.map((r, i) => `${x(times[i]!).toFixed(1)},${y(r.temp_c).toFixed(1)}`);
  const time = (iso: string) =>
    new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-auto w-full"
      role="img"
      aria-label={`Temperature over time: ${readings.length} readings, latest ${formatTemp(last.temp_c)} at ${formatDateTime(last.ts)}`}
      data-testid="temperature-chart"
    >
      {bounds.length > 0 && (
        <rect
          x={PAD.left}
          y={bandTop}
          width={W - PAD.left - PAD.right}
          height={Math.max(bandBottom - bandTop, 0)}
          className="fill-status-success-bg"
          data-testid="allowed-band"
        />
      )}
      {bounds.map((b) => (
        <g key={b}>
          <line
            x1={PAD.left}
            x2={W - PAD.right}
            y1={y(b)}
            y2={y(b)}
            className="stroke-status-success"
            strokeDasharray="4 3"
          />
          <text x={PAD.left - 6} y={y(b) + 4} textAnchor="end" className="fill-current text-[11px]">
            {degrees.format(b)}°
          </text>
        </g>
      ))}
      <line
        x1={PAD.left}
        x2={PAD.left}
        y1={PAD.top}
        y2={H - PAD.bottom}
        className="stroke-border"
      />
      <polyline
        points={points.join(" ")}
        fill="none"
        className="stroke-primary"
        strokeWidth={2}
        strokeLinejoin="round"
      />
      {readings.map((r, i) =>
        inBand(band, r.temp_c) ? null : (
          <circle
            key={r.ts + r.device_id}
            cx={x(times[i]!)}
            cy={y(r.temp_c)}
            r={3.5}
            className="fill-status-danger"
            data-testid="out-of-range"
          >
            <title>{`${formatTemp(r.temp_c)} at ${formatDateTime(r.ts)}`}</title>
          </circle>
        ),
      )}
      <text x={PAD.left} y={H - 8} className="fill-current text-[11px]">
        {time(readings[0]!.ts)}
      </text>
      <text x={W - PAD.right} y={H - 8} textAnchor="end" className="fill-current text-[11px]">
        {time(last.ts)}
      </text>
    </svg>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium">{children}</dd>
    </div>
  );
}

/** The hub's cold-chain events, oldest first. */
export function ColdChainEventList({ events }: { events: ColdChainEvent[] }) {
  if (events.length === 0)
    return <p className="text-sm text-muted-foreground">No cold-chain events recorded.</p>;
  return (
    <ol className="grid gap-2" aria-label="Cold-chain events">
      {events.map((e) => (
        <li key={e.id} className="grid gap-0.5 text-sm" data-testid={`coldchain-event-${e.type}`}>
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                "inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium",
                TONE_CLASS[TONE[e.type]],
              )}
            >
              {COLDCHAIN_LABEL[e.type]}
            </span>
            <span className="text-muted-foreground">
              {formatDateTime(e.ts)} · {e.device_id}
            </span>
          </div>
          <p>{describeColdChainEvent(e)}</p>
        </li>
      ))}
    </ol>
  );
}

/** The live cold-chain view of one shipment: chart, band, events, battery and last seen.
 *  Only the shipment's own orgs may read it (the hub answers 403 otherwise). */
export function ColdChainPanel({ shipmentId }: { shipmentId: string }) {
  const query = useColdChain(shipmentId);
  let body: ReactNode;
  if (query.isPending) body = <Loading label="Loading cold chain…" />;
  else if (query.isError) body = <ErrorState error={query.error} />;
  else {
    const c = query.data;
    const band = formatBand(c.band);
    const latest = c.readings.at(-1);
    body = (
      <div className="grid gap-4">
        {c.has_excursion && (
          <p
            role="alert"
            className="rounded-md bg-status-danger-bg p-3 text-sm font-medium text-status-danger"
            data-testid="excursion-on-record"
          >
            A cold-chain excursion is on record for this shipment.
          </p>
        )}
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Fact label="Allowed range">{band ?? "No temperature range"}</Fact>
          <Fact label="Latest reading">
            {latest ? (
              <span data-testid="latest-reading">{formatTemp(latest.temp_c)}</span>
            ) : (
              "None yet"
            )}
          </Fact>
          <Fact label="Cold box">
            {c.device ? (
              <>
                {c.device.device_id}
                {c.device.battery_level !== null && (
                  <span className="font-normal text-muted-foreground">
                    , battery {c.device.battery_level}%
                  </span>
                )}
              </>
            ) : (
              "None"
            )}
          </Fact>
          <Fact label="Last seen">
            {c.device?.last_seen ? formatDateTime(c.device.last_seen) : "Never"}
          </Fact>
        </dl>
        <TemperatureChart readings={c.readings} band={c.band} />
        <div className="grid gap-2">
          <h3 className="text-sm font-medium">Events</h3>
          <ColdChainEventList events={c.events} />
        </div>
      </div>
    );
  }
  return (
    <Card data-testid="coldchain-panel">
      <CardHeader>
        <CardTitle>Cold chain</CardTitle>
        <CardDescription>
          Readings from the cold box, evaluated by the hub. A device silent for{" "}
          {query.data ? formatSeconds(query.data.silent_after_seconds) : "a while"} in transit is
          reported.
        </CardDescription>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  );
}

const badge = "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium";

/** A list badge from the hub's summary: the newest event, and whether an excursion is on
 *  record. Nothing when the shipment has no cold-chain event. A DEVICE_SILENT is worded as
 *  the event it is, with its time ("Device silent at …"), not as the device's state now. */
export function ColdChainStateBadge({ summary }: { summary: ColdChainSummary | null }) {
  if (!summary) return null;
  const at = formatDateTime(summary.last_event_at);
  const label =
    summary.last_event_type === "DEVICE_SILENT"
      ? `${COLDCHAIN_LABEL.DEVICE_SILENT} at ${at}`
      : COLDCHAIN_LABEL[summary.last_event_type];
  const onRecord = summary.had_excursion && summary.last_event_type !== "EXCURSION";
  return (
    <>
      {summary.last_event_type !== "RECOVERED" && (
        <span
          className={cn(badge, TONE_CLASS[TONE[summary.last_event_type]])}
          title={at}
          data-testid="coldchain-badge"
        >
          {label}
        </span>
      )}
      {onRecord && (
        <span className={cn(badge, TONE_CLASS.warning)} title={at} data-testid="excursion-badge">
          Excursion on record
        </span>
      )}
    </>
  );
}

const ALERTS: Record<string, ColdChainEventType> = {
  "coldchain.excursion": "EXCURSION",
  "coldchain.device_silent": "DEVICE_SILENT",
  "coldchain.recovered": "RECOVERED",
};

export type ColdChainAlertConfig = {
  /** Where the toast's "View" button goes, e.g. `/shipments/${id}`. */
  link: (shipmentId: string) => string;
  /** Only shipments to the user's own org (the hospital's inbound deliveries). */
  inboundOnly?: boolean;
};

/** Whether `event` is a cold-chain event to alert `orgId` about. */
export function isColdChainAlert(
  event: EventEnvelope,
  orgId: string | undefined,
  inboundOnly = false,
): boolean {
  if (!(event.type in ALERTS)) return false;
  return !inboundOnly || (orgId !== undefined && event.data.to_org_id === orgId);
}

/** Raises a toast for each new cold-chain event the hub sends the signed-in user's org. */
export function useColdChainAlerts({ link, inboundOnly = false }: ColdChainAlertConfig) {
  const navigate = useNavigate();
  const orgId = useMe().data?.org.id;
  useEffect(
    () =>
      onHubEvent((event) => {
        if (!isColdChainAlert(event, orgId, inboundOnly)) return;
        const type = ALERTS[event.type]!;
        const data = event.data as {
          shipment_id: string;
          coldchain_event_id?: string;
          device_id?: string;
          observed_value: number;
          threshold: number;
        };
        const show =
          type === "EXCURSION" ? toast.error : type === "DEVICE_SILENT" ? toast.warning : toast;
        show(`${COLDCHAIN_LABEL[type]}${data.device_id ? ` (${data.device_id})` : ""}`, {
          id: `coldchain-${data.coldchain_event_id ?? event.id}`,
          description: describeColdChainEvent({ ...data, type }),
          duration: type === "EXCURSION" ? Infinity : 10_000,
          action: { label: "View", onClick: () => void navigate(link(data.shipment_id)) },
        });
      }),
    [navigate, orgId, inboundOnly, link],
  );
}

/** Mount once inside the app shell. */
export function ColdChainAlerts(props: ColdChainAlertConfig) {
  useColdChainAlerts(props);
  return null;
}
