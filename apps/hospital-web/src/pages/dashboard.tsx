// Dashboard (apps-ai-iot.md, hospital-web; S08 part): open shortages by status, incoming
// requests awaiting this hospital's response with live countdowns, and (S19) this hospital's
// reliability as a source and the credits it has earned.
import type { ReactNode } from "react";
import { Link } from "react-router";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Countdown,
  EmptyState,
  ErrorState,
  Loading,
  StatusChip,
  useCan,
  useMe,
  useNow,
} from "@care-e/ui";
import { useProducts, useReliability, useShortages, useSourceRequests } from "../api";
import { PageHeader } from "../components/page";
import { ReliabilityBadge } from "../components/reliability";
import { ANSWERABLE, OPEN_STATES, qty, SHORTAGE_READERS } from "../display";

function OpenShortages() {
  const shortages = useShortages();
  if (shortages.isPending) return <Loading label="Loading shortages…" />;
  if (shortages.isError) return <ErrorState error={shortages.error} />;
  const rows = shortages.data.pages.flatMap((p) => p.items);
  const counts = OPEN_STATES.map((state) => ({
    state,
    n: rows.filter((s) => s.status === state).length,
  })).filter((c) => c.n > 0);
  if (counts.length === 0) return <EmptyState title="No open shortages" />;
  return (
    <ul aria-label="Open shortages by status" className="grid gap-2">
      {counts.map(({ state, n }) => (
        <li key={state} className="flex items-center justify-between">
          <StatusChip state={state} />
          <span className="font-semibold" data-testid={`count-${state}`}>
            {n}
          </span>
        </li>
      ))}
    </ul>
  );
}

function AwaitingRequests() {
  const now = useNow();
  const requests = useSourceRequests({ direction: "incoming", status: ANSWERABLE });
  const products = useProducts();
  if (requests.isPending || products.isPending) return <Loading label="Loading requests…" />;
  if (requests.isError) return <ErrorState error={requests.error} />;
  if (products.isError) return <ErrorState error={products.error} />;
  const rows = requests.data.pages.flatMap((p) => p.items);
  if (rows.length === 0) return <EmptyState title="No requests awaiting your response" />;
  return (
    <ul aria-label="Incoming requests awaiting response" className="grid gap-3">
      {rows.map((r) => {
        const product = products.data.byId.get(r.product_id);
        return (
          <li key={r.id} className="flex items-start justify-between gap-3 text-sm">
            <div>
              <div className="font-medium">
                {qty(r.qty, product)} {product?.name ?? "Unknown product"}
              </div>
              <div className="text-xs text-muted-foreground">from {r.requester_org_name}</div>
            </div>
            <Countdown to={r.sla_deadline} now={now} />
          </li>
        );
      })}
    </ul>
  );
}

/** The hub's stored score for this org (what matching ranks it by) and its credit balance. */
function OwnReliability() {
  const orgId = useMe().data?.org.id ?? "";
  const reliability = useReliability(orgId);
  if (reliability.isPending) return <Loading label="Loading reliability…" />;
  if (reliability.isError) return <ErrorState error={reliability.error} />;
  const r = reliability.data;
  return (
    <div className="grid gap-3 text-sm">
      <div className="flex items-center justify-between">
        <span>Reliability score</span>
        <ReliabilityBadge orgId={orgId} score={r.score} />
      </div>
      {!r.has_history && (
        <p className="text-xs text-muted-foreground">
          Not enough history yet, so the default score applies.
        </p>
      )}
      <div className="flex items-center justify-between">
        <span>Credits earned</span>
        <span className="font-semibold" data-testid="credits">
          {r.credits ?? 0}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">
        One credit per 10 units you transferred that the receiver accepted. Credits cannot be spent
        yet.
      </p>
    </div>
  );
}

function DashCard({ title, to, children }: { title: string; to: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader className="flex items-center justify-between">
        <CardTitle>{title}</CardTitle>
        <Link to={to} className="text-sm text-primary hover:underline">
          View all
        </Link>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export function DashboardPage() {
  const canSeeShortages = useCan(SHORTAGE_READERS);
  const canRespond = useCan("source_request.respond");
  return (
    <>
      <PageHeader title="Dashboard" />
      {canSeeShortages || canRespond ? (
        <div className="grid gap-4 md:grid-cols-2">
          {canRespond && (
            <DashCard title="Incoming requests awaiting response" to="/requests">
              <AwaitingRequests />
            </DashCard>
          )}
          {canRespond && (
            <Card>
              <CardHeader>
                <CardTitle>Your reliability as a source</CardTitle>
              </CardHeader>
              <CardContent>
                <OwnReliability />
              </CardContent>
            </Card>
          )}
          {canSeeShortages && (
            <DashCard title="Open shortages" to="/shortages">
              <OpenShortages />
            </DashCard>
          )}
        </div>
      ) : (
        <EmptyState title="Nothing to show yet">
          Your dashboard cards arrive with later sections.
        </EmptyState>
      )}
    </>
  );
}
