// The latest match run: eligible candidates in the hub's rank order, and rejected ones with
// the hub's own reason text. Nothing here ranks, filters or re-words what the hub decided.
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  formatDateTime,
  formatMoney,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@care-e/ui";
import type { Candidate, MatchRun, Product } from "../api";
import { ReliabilityBadge } from "../components/reliability";
import {
  candidateQty,
  formatHours,
  GATE_LABELS,
  qty,
  RESOLUTION_LABELS,
  TRIGGER_LABELS,
} from "../display";

export const SourceType = ({ type }: { type: string }) => (
  <Badge variant="outline">{type === "HOSPITAL" ? "Hospital" : "Supplier"}</Badge>
);

/** Hospital sources carry no cost by design (the requester must not learn their unit cost). */
export function LandedCost({ paise }: { paise: number | null }) {
  if (paise === null)
    return (
      <span title="Not shown for hospital sources" aria-label="Not shown">
        —
      </span>
    );
  return <>{formatMoney(paise)}</>;
}

function Eligible({ rows, product }: { rows: Candidate[]; product: Product | undefined }) {
  return (
    <Table aria-label="Eligible sources">
      <TableHeader>
        <TableRow>
          <TableHead>Rank</TableHead>
          <TableHead>Source</TableHead>
          <TableHead className="text-right">Quantity</TableHead>
          <TableHead className="text-right">ETA</TableHead>
          <TableHead className="text-right">Reliability</TableHead>
          <TableHead className="text-right">Landed cost</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((c) => (
          <TableRow key={c.id}>
            <TableCell className="font-semibold">{c.rank}</TableCell>
            <TableCell>
              <span className="mr-2 font-medium">{c.source_org_name}</span>
              <SourceType type={c.source_type} />
            </TableCell>
            <TableCell className="text-right">{candidateQty(c, product)}</TableCell>
            <TableCell className="text-right">{formatHours(c.eta_hours)}</TableCell>
            <TableCell className="text-right">
              {/* The score this run ranked by; the tooltip shows the org's components. */}
              <ReliabilityBadge orgId={c.source_org_id} score={c.reliability} />
            </TableCell>
            <TableCell className="text-right">
              <LandedCost paise={c.landed_cost_paise} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Rejected({ rows, product }: { rows: Candidate[]; product: Product | undefined }) {
  return (
    <ul aria-label="Rejected sources" className="divide-y rounded-md border">
      {rows.map((c) => (
        <li key={c.id} className="grid gap-1 p-3 sm:grid-cols-[14rem_1fr]">
          <div>
            <span className="mr-2 font-medium">{c.source_org_name}</span>
            <SourceType type={c.source_type} />
            <div className="text-xs text-muted-foreground">{candidateQty(c, product)}</div>
          </div>
          <ul className="grid gap-1 text-sm">
            {c.gate_results
              .filter((g) => !g.passed)
              .map((g) => (
                <li key={g.gate} data-gate={g.gate}>
                  <span className="font-medium text-status-danger">
                    {GATE_LABELS[g.gate] ?? g.gate}:
                  </span>{" "}
                  <span data-testid="reason">{g.reason ?? "No reason given."}</span>
                </li>
              ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

export function MatchRunCard({
  run,
  product,
}: {
  run: MatchRun | null;
  product: Product | undefined;
}) {
  if (!run)
    return (
      <EmptyState title="No match run yet">The hub has not looked for sources yet.</EmptyState>
    );

  const eligible = run.candidates.filter((c) => c.eligible);
  const rejected = run.candidates.filter((c) => !c.eligible);
  const names = new Map(run.candidates.map((c) => [c.id, c.source_org_name]));
  const plan = run.planned_resolution;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Latest match run #{run.run_no}</CardTitle>
        <CardDescription>
          {TRIGGER_LABELS[run.triggered_by] ?? run.triggered_by} · {formatDateTime(run.ts)}
          {run.excluded_org_ids.length > 0 &&
            ` · ${run.excluded_org_ids.length} earlier ${
              run.excluded_org_ids.length === 1 ? "source" : "sources"
            } left out`}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6">
        <p className="text-sm" data-testid="plan">
          {plan ? (
            <>
              <span className="font-medium">
                Planned: {RESOLUTION_LABELS[plan.type] ?? plan.type}
              </span>
              {" — "}
              {plan.lines
                .map(
                  (l) => `${qty(l.qty, product)} from ${names.get(l.candidate_id) ?? "a source"}`,
                )
                .join(", ")}
            </>
          ) : (
            <span className="font-medium">{run.reason ?? "No plan"}</span>
          )}
        </p>

        <section className="grid gap-2">
          <h3 className="font-medium">Eligible ({eligible.length})</h3>
          {eligible.length === 0 ? (
            <EmptyState title="No eligible source" />
          ) : (
            <Eligible rows={eligible} product={product} />
          )}
        </section>

        <section className="grid gap-2">
          <h3 className="font-medium">Not eligible ({rejected.length})</h3>
          {rejected.length === 0 ? (
            <EmptyState title="Every source considered was eligible" />
          ) : (
            <Rejected rows={rejected} product={product} />
          )}
        </section>
      </CardContent>
    </Card>
  );
}
