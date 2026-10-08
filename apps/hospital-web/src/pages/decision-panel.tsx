// Decision panel on shortage detail (apps-ai-iot.md, hospital-web; S12): the hub's
// recommendation for the latest match run, and the approver's decision. Every figure comes
// from the hub as is: a hospital source's cost is null by design (CLAUDE.md rule 6) and shows
// "—"; nothing is summed or estimated here. Buttons show only for `recommendation.approve`, in
// the states business-rules §8 allows, while the validity countdown runs; the hub still decides.
import { useState } from "react";
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  Countdown,
  EmptyState,
  ErrorState,
  formatDateTime,
  formatMoney,
  isPast,
  Loading,
  StatusChip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  toast,
  useCan,
  useNow,
} from "@care-e/ui";
import {
  type Decision,
  type MatchRun,
  type Product,
  type Recommendation,
  type RecommendationLine,
  type Shortage,
  useCurrentRecommendationId,
  useDecide,
  useRecommendation,
} from "../api";
import { RecordedReason } from "../components/page";
import {
  APPROVE_FROM,
  DECISION_WORDING,
  ESCALATE_FROM,
  formatHours,
  qty,
  REJECT_FROM,
  RESOLUTION_LABELS,
} from "../display";
import { LandedCost, SourceType } from "./match-run";

function Lines({
  label,
  lines,
  product,
}: {
  label: string;
  lines: RecommendationLine[];
  product: Product | undefined;
}) {
  return (
    <Table aria-label={label}>
      <TableHeader>
        <TableRow>
          <TableHead>Source</TableHead>
          <TableHead className="text-right">Quantity</TableHead>
          <TableHead className="text-right">ETA</TableHead>
          <TableHead className="text-right">Shelf life at delivery</TableHead>
          <TableHead className="text-right">Unit price</TableHead>
          <TableHead className="text-right">Landed cost</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lines.map((line) => (
          <TableRow key={line.candidate_id}>
            <TableCell>
              <span className="mr-2 font-medium">{line.source_org_name}</span>
              <SourceType type={line.source_type} />
            </TableCell>
            <TableCell className="text-right">{qty(line.qty, product)}</TableCell>
            <TableCell className="text-right">{formatHours(line.eta_hours)}</TableCell>
            <TableCell className="text-right">
              {line.shelf_life_days === null ? "—" : `${line.shelf_life_days} days`}
            </TableCell>
            <TableCell className="text-right">
              <LandedCost paise={line.unit_price_paise} />
            </TableCell>
            <TableCell className="text-right">
              <LandedCost paise={line.landed_cost_paise} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** The decision buttons, each behind a confirmation with an optional reason box. */
function Decide({
  rec,
  onApproved,
}: {
  rec: Recommendation;
  onApproved: (message: string) => void;
}) {
  const decide = useDecide(rec);
  const wording = DECISION_WORDING[rec.type];
  const send = (decision: Decision) => async (reason: string | undefined) => {
    const out = await decide.mutateAsync({ decision, reason });
    if (decision === "approve" && out.message) {
      onApproved(out.message);
      toast.success(out.message);
    } else if (decision === "reject") toast.success("Recommendation rejected.");
    else if (decision === "escalate") toast.success("Escalated to your organization's approvers.");
  };
  return (
    <div className="flex flex-wrap gap-2" data-testid="decision-actions">
      {APPROVE_FROM.has(rec.status) && wording && (
        <ConfirmDialog
          trigger={wording.approve}
          title={`${wording.approve}?`}
          description="Your decision and any reason you type are recorded in the audit trail."
          confirmLabel={wording.approve}
          onConfirm={send("approve")}
        />
      )}
      {ESCALATE_FROM.has(rec.status) && (
        <ConfirmDialog
          trigger="Escalate"
          title="Escalate this recommendation?"
          description="Every approver in your organization is notified."
          confirmLabel="Escalate"
          destructive
          onConfirm={send("escalate")}
        />
      )}
      {REJECT_FROM.has(rec.status) && (
        <ConfirmDialog
          trigger="Reject"
          title="Reject this recommendation?"
          description="The hub releases any stock held for it and runs matching again."
          confirmLabel="Reject"
          destructive
          onConfirm={send("reject")}
        />
      )}
    </div>
  );
}

export function RecommendationCard({
  rec,
  product,
}: {
  rec: Recommendation;
  product: Product | undefined;
}) {
  const now = useNow();
  const canDecide = useCan("recommendation.approve");
  // The hub's own §13 message from the approve response, while this screen is open.
  const [approvedMessage, setApprovedMessage] = useState<string | null>(null);
  const undecided = APPROVE_FROM.has(rec.status);
  const lapsed = isPast(rec.expires_at, now);

  return (
    <Card data-testid="decision-panel">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          Recommendation
          <Badge data-testid="recommendation-type">{RESOLUTION_LABELS[rec.type] ?? rec.type}</Badge>
          <StatusChip state={rec.status} />
        </CardTitle>
        <CardDescription>
          {undecided ? (
            <>
              Valid for <Countdown to={rec.expires_at} now={now} passed="Validity passed" />
            </>
          ) : (
            `Made ${formatDateTime(rec.created_at)}`
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-5">
        <p className="text-sm whitespace-pre-line" data-testid="explanation">
          {rec.explanation}
        </p>

        <section className="grid gap-2">
          <h3 className="font-medium">Recommended</h3>
          <Lines label="Recommended sources" lines={rec.lines} product={product} />
          <p className="text-sm" data-testid="total-cost">
            Total landed cost:{" "}
            {rec.total_landed_cost_paise === null ? (
              <span className="text-muted-foreground">
                <LandedCost paise={null} /> (not shown when a hospital source is involved)
              </span>
            ) : (
              <span className="font-medium">{formatMoney(rec.total_landed_cost_paise)}</span>
            )}
          </p>
        </section>

        <section className="grid gap-2">
          <h3 className="font-medium">Alternatives</h3>
          {rec.alternatives.length === 0 ? (
            <p className="text-sm text-muted-foreground">No alternative.</p>
          ) : (
            <Lines label="Alternatives" lines={rec.alternatives} product={product} />
          )}
        </section>

        {rec.status === "APPROVED" && (
          <p
            role="status"
            data-testid="approved-message"
            className="rounded-md bg-status-success-bg p-3 text-sm font-medium text-status-success"
          >
            {approvedMessage ?? DECISION_WORDING[rec.type]?.approved}
          </p>
        )}

        {!undecided || rec.status === "ESCALATED" ? (
          <div className="grid gap-1 text-sm" data-testid="decision">
            <span className="text-muted-foreground">
              {rec.decided_at
                ? `Decided ${formatDateTime(rec.decided_at)}`
                : `Updated ${formatDateTime(rec.updated_at)}`}
            </span>
            <RecordedReason reason={rec.reason} source={rec.reason_source} />
          </div>
        ) : null}

        {undecided &&
          (lapsed ? (
            <p className="text-sm text-muted-foreground" data-testid="validity-passed">
              Its validity has passed, so it can no longer be decided. The hub expires it and runs
              matching again.
            </p>
          ) : canDecide ? (
            <Decide rec={rec} onApproved={setApprovedMessage} />
          ) : (
            <p className="text-sm text-muted-foreground">
              Waiting for an approver in your organization to decide.
            </p>
          ))}
      </CardContent>
    </Card>
  );
}

/** Finds the shortage's current recommendation and shows it (nothing when there is none). */
export function DecisionPanel({
  shortage,
  run,
  product,
}: {
  shortage: Shortage;
  run: MatchRun | null | undefined;
  product: Product | undefined;
}) {
  const current = useCurrentRecommendationId(shortage.id, run?.id);
  const rec = useRecommendation(current.id);
  const awaiting = shortage.status === "AWAITING_DECISION";

  if (current.unavailable)
    // Until the hub's "current recommendation" read lands, finding it needs `audit.read`.
    return awaiting ? (
      <EmptyState title="A recommendation is awaiting a decision">
        An approver in your organization decides on it.
      </EmptyState>
    ) : null;
  if (current.error) return <ErrorState error={current.error} />;
  if (current.isPending || (current.id !== null && rec.isPending))
    return <Loading label="Loading recommendation…" />;
  if (rec.isError) return <ErrorState error={rec.error} />;
  if (!rec.data)
    return awaiting ? <EmptyState title="The recommendation could not be found" /> : null;
  return <RecommendationCard rec={rec.data} product={product} />;
}
