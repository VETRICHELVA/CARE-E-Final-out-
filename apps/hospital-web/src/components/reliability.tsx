// Reliability (business-rules.md §12, S19): the hub's stored score and the four components it
// is computed from. Display only: nothing here computes a score or ranks a source.
import { useId, useState } from "react";
import { Badge } from "@care-e/ui";
import { type Reliability, useReliability } from "../api";

const percent = new Intl.NumberFormat("en-IN", { style: "percent", maximumFractionDigits: 1 });
const minutes = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });

/** The hub's four components as lines of text; "no history yet" where the hub has none. */
export function reliabilityLines(r: Reliability): string[] {
  const rate = (v: number | null) => (v === null ? "no history yet" : percent.format(v));
  const median =
    r.median_response_minutes === null
      ? ""
      : ` (median answer in ${minutes.format(r.median_response_minutes)} min)`;
  const lines = [
    `Score: ${r.score} of 100`,
    `Acceptance rate: ${rate(r.acceptance_rate)}`,
    `On-time delivery: ${rate(r.on_time_rate)}`,
    `Discrepancy rate: ${rate(r.discrepancy_rate)}`,
    `Response speed: ${rate(r.response_speed)}${median}`,
  ];
  if (!r.has_history) lines.push("Not enough history yet, so the default score applies.");
  return lines;
}

/** A score badge whose tooltip (on hover or keyboard focus) lists the org's components. The
 *  components are fetched only once the tooltip opens. */
export function ReliabilityBadge({ orgId, score }: { orgId: string; score: number }) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();
  const details = useReliability(orgId, open);
  return (
    <span
      className="relative inline-block"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="rounded-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        aria-label={`Reliability ${score} of 100`}
        aria-describedby={open ? tooltipId : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        data-testid="reliability"
      >
        <Badge variant="outline">{score}</Badge>
      </button>
      {open && (
        <div
          role="tooltip"
          id={tooltipId}
          className="absolute top-full right-0 z-20 mt-1 w-64 rounded-md border bg-popover p-2 text-left text-xs text-popover-foreground shadow-md"
        >
          {details.isPending ? (
            "Loading…"
          ) : details.isError ? (
            "The score's components could not be loaded."
          ) : (
            <ul className="grid gap-0.5">
              {reliabilityLines(details.data).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </span>
  );
}
