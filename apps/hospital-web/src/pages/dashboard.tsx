// Dashboard (apps-ai-iot.md, hospital-web; S08 part): open shortages by status. Incoming
// requests with countdowns arrive with S06's source requests.
import { Link } from "react-router";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  Loading,
  StatusChip,
  useCan,
} from "@care-e/ui";
import { useShortages } from "../api";
import { PageHeader } from "../components/page";
import { OPEN_STATES, SHORTAGE_READERS } from "../display";

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

export function DashboardPage() {
  const canSeeShortages = useCan(SHORTAGE_READERS);
  return (
    <>
      <PageHeader title="Dashboard" />
      {canSeeShortages ? (
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader className="flex items-center justify-between">
              <CardTitle>Open shortages</CardTitle>
              <Link to="/shortages" className="text-sm text-primary hover:underline">
                View all
              </Link>
            </CardHeader>
            <CardContent>
              <OpenShortages />
            </CardContent>
          </Card>
        </div>
      ) : (
        <EmptyState title="Nothing to show yet">
          Your dashboard cards arrive with later sections.
        </EmptyState>
      )}
    </>
  );
}
