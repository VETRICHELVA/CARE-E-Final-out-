// Shortage detail (apps-ai-iot.md, hospital-web): facts, status timeline, latest match run,
// and the requester's actions. Buttons show only when the user's capability and the shortage's
// state allow them; the hub still checks every call.
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  ErrorState,
  formatDateTime,
  Loading,
  StatusChip,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  toast,
  useCan,
} from "@care-e/ui";
import {
  type AuditRow,
  type MatchRun,
  type Shortage,
  useAudit,
  useCancelShortage,
  useLatestRun,
  useProducts,
  useRerunMatch,
  useShortage,
} from "../api";
import { PageHeader } from "../components/page";
import {
  CANCEL_FROM,
  OPEN_REQUEST_STATES,
  qty,
  RERUN_FROM,
  statusLabel,
  TRIGGER_LABELS,
} from "../display";
import { DecisionPanel } from "./decision-panel";
import { MatchRunCard } from "./match-run";
import { ShortageAudit } from "./shortage-audit";
import { PriorityBadge } from "./shortages";
import { SourceRequestsPanel, useShortageRequests } from "./source-requests-panel";

type Event = { ts: string; title: ReactNode; detail?: ReactNode };

/** Status history from what the user may read: the audit trail with `audit.read`,
 *  otherwise the shortage's own timestamps. Nothing is inferred beyond those records. */
function timeline(shortage: Shortage, run: MatchRun | null, audit: AuditRow[] | undefined) {
  const events: Event[] = [];
  if (audit) {
    for (const row of audit) {
      if (row.action === "shortage.created")
        events.push({ ts: row.ts, title: "Reported", detail: row.reason });
      else if (row.action === "shortage.status_changed")
        events.push({
          ts: row.ts,
          title: `${statusLabel(row.before?.status)} → ${statusLabel(row.after?.status)}`,
          detail: row.reason,
        });
    }
  } else {
    events.push({ ts: shortage.created_at, title: "Reported" });
    events.push({
      ts: shortage.updated_at,
      title: (
        <>
          Status: <StatusChip state={shortage.status} />
        </>
      ),
    });
  }
  if (run)
    events.push({
      ts: run.ts,
      title: `Match run #${run.run_no}`,
      detail: TRIGGER_LABELS[run.triggered_by] ?? run.triggered_by,
    });
  return events.sort((a, b) => a.ts.localeCompare(b.ts));
}

function Timeline({ events }: { events: Event[] }) {
  return (
    <ol aria-label="Status timeline" className="grid gap-3 border-l pl-4">
      {events.map((e, i) => (
        <li key={i} className="relative text-sm">
          <span className="absolute top-1.5 -left-[1.3rem] size-2 rounded-full bg-primary" />
          <div className="font-medium">{e.title}</div>
          <div className="text-xs text-muted-foreground">
            {formatDateTime(e.ts)}
            {e.detail && <> · {e.detail}</>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium">{children}</dd>
    </div>
  );
}

function Actions({ shortage }: { shortage: Shortage }) {
  const canAct = useCan("shortage.create");
  const rerun = useRerunMatch(shortage.id);
  const cancel = useCancelShortage(shortage.id);
  const requests = useShortageRequests(shortage.id);
  if (!canAct) return null;
  // The hub refuses a manual re-run while a source request is open; it re-runs on its own when
  // the source answers or the deadline passes. Hidden until the requests are known.
  const waitingOnSource =
    !requests.isSuccess ||
    requests.data.pages.some((p) => p.items.some((r) => OPEN_REQUEST_STATES.has(r.status)));
  return (
    <>
      {RERUN_FROM.has(shortage.status) && !waitingOnSource && (
        <ConfirmDialog
          trigger="Re-run match"
          title="Re-run matching?"
          description="The hub checks every source again with the latest stock and offers."
          confirmLabel="Re-run match"
          onConfirm={async (reason) => {
            const run = await rerun.mutateAsync(reason);
            toast.success(`Match run #${run.run_no} finished.`);
          }}
        />
      )}
      {CANCEL_FROM.has(shortage.status) && (
        <ConfirmDialog
          trigger="Cancel shortage"
          title="Cancel this shortage?"
          description="The hub stops looking for sources for it."
          confirmLabel="Cancel shortage"
          destructive
          onConfirm={async (reason) => {
            await cancel.mutateAsync(reason);
            toast.success("Shortage cancelled.");
          }}
        />
      )}
    </>
  );
}

export function ShortageDetailPage() {
  const id = useParams().id ?? "";
  const shortage = useShortage(id);
  const run = useLatestRun(id);
  const products = useProducts();
  const canReadAudit = useCan("audit.read");
  const audit = useAudit("shortage", id, canReadAudit);

  if (shortage.isPending) return <Loading label="Loading shortage…" />;
  if (shortage.isError) return <ErrorState error={shortage.error} />;

  const s = shortage.data;
  const product = products.data?.byId.get(s.product_id);
  return (
    <div className="grid gap-4">
      <Link to="/shortages" className="text-sm text-muted-foreground hover:underline">
        ← Shortages
      </Link>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {product?.name ?? "Shortage"} <StatusChip state={s.status} />
            <PriorityBadge priority={s.priority} />
          </span>
        }
        description={product?.code}
        actions={<Actions shortage={s} />}
      />

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          {canReadAudit && <TabsTrigger value="audit">Audit trail</TabsTrigger>}
        </TabsList>
        <TabsContent value="overview" className="grid gap-4">
          <DecisionPanel shortage={s} product={product} />
          <Card>
            <CardContent>
              <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Fact label="Shortfall (computed by the hub)">
                  <span className="text-primary" data-testid="shortfall">
                    {qty(s.shortfall, product)}
                  </span>
                </Fact>
                <Fact label="Required">{qty(s.qty_required, product)}</Fact>
                <Fact label="Usable on hand">{qty(s.qty_local_usable, product)}</Fact>
                <Fact label="Required by">{formatDateTime(s.required_by)}</Fact>
                <Fact label="Minimum shelf life">{s.min_shelf_life_days} days</Fact>
                <Fact label="Reported">{formatDateTime(s.created_at)}</Fact>
                {s.notes && <Fact label="Notes">{s.notes}</Fact>}
                {s.parent_shortage_id && (
                  <Fact label="Residual of">
                    <Link
                      to={`/shortages/${s.parent_shortage_id}`}
                      className="text-primary hover:underline"
                    >
                      An earlier shortage
                    </Link>
                  </Fact>
                )}
              </dl>
            </CardContent>
          </Card>

          <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
            <Card className="self-start">
              <CardHeader>
                <CardTitle>Timeline</CardTitle>
              </CardHeader>
              <CardContent>
                {audit.isError ? (
                  <ErrorState error={audit.error} />
                ) : audit.isLoading || run.isPending ? (
                  <Loading />
                ) : (
                  <Timeline events={timeline(s, run.data ?? null, audit.data?.items)} />
                )}
              </CardContent>
            </Card>
            <div className="grid gap-4 self-start">
              {run.isPending ? (
                <Loading label="Loading match run…" />
              ) : run.isError ? (
                <ErrorState error={run.error} />
              ) : (
                <MatchRunCard run={run.data} product={product} />
              )}
              <SourceRequestsPanel shortageId={s.id} product={product} />
            </div>
          </div>
        </TabsContent>
        {canReadAudit && (
          <TabsContent value="audit">
            <ShortageAudit shortage={s} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
