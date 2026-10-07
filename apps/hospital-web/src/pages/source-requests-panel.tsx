// Shortage detail: the source requests the hub sent for this shortage and where each stands
// (apps-ai-iot.md, hospital-web). Read-only for the requester; only the source answers.
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Countdown,
  EmptyState,
  ErrorState,
  formatDateTime,
  Loading,
  StatusChip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useNow,
} from "@care-e/ui";
import { type Product, type SourceRequest, useSourceRequests } from "../api";
import { DeclineReason, LoadMore } from "../components/page";
import { qty } from "../display";

/** The deadline that matters in the request's state, from the hub's own timestamps. */
function Deadline({ request, now }: { request: SourceRequest; now: number }) {
  if (request.status === "REQUESTED")
    return (
      <div>
        <Countdown to={request.sla_deadline} now={now} />
        <div className="text-xs text-muted-foreground">to respond</div>
      </div>
    );
  if (request.status === "TENTATIVE_HOLD" && request.hold_expires_at)
    return (
      <div>
        <Countdown to={request.hold_expires_at} now={now} passed="Hold lapsed" />
        <div className="text-xs text-muted-foreground">hold</div>
      </div>
    );
  return (
    <span className="text-muted-foreground">
      {request.responded_at
        ? `Answered ${formatDateTime(request.responded_at)}`
        : formatDateTime(request.updated_at)}
    </span>
  );
}

/** `useSourceRequests` for one shortage's outgoing requests (shared with the page's actions). */
export const useShortageRequests = (shortageId: string) =>
  useSourceRequests({ direction: "outgoing", shortage_id: shortageId });

export function SourceRequestsPanel({
  shortageId,
  product,
}: {
  shortageId: string;
  product: Product | undefined;
}) {
  const now = useNow();
  const list = useShortageRequests(shortageId);

  let body;
  if (list.isPending) body = <Loading label="Loading source requests…" />;
  else if (list.isError) body = <ErrorState error={list.error} />;
  else {
    const rows = list.data.pages.flatMap((p) => p.items);
    body =
      rows.length === 0 ? (
        <EmptyState title="No source requests yet">
          The hub asks a hospital source once a match run plans a transfer.
        </EmptyState>
      ) : (
        <>
          <Table aria-label="Source requests">
            <TableHeader>
              <TableRow>
                <TableHead>Source</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead>State</TableHead>
                <TableHead>Deadline</TableHead>
                <TableHead>Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.id} data-testid={`source-request-${r.id}`}>
                  <TableCell className="font-medium">{r.source_org_name}</TableCell>
                  <TableCell className="text-right">{qty(r.qty, product)}</TableCell>
                  <TableCell>
                    <StatusChip state={r.status} />
                  </TableCell>
                  <TableCell>
                    <Deadline request={r} now={now} />
                  </TableCell>
                  <TableCell className="text-sm">
                    {r.held_qty > 0 && <div>{qty(r.held_qty, product)} on hold</div>}
                    {r.status === "DECLINED" && (
                      <DeclineReason reason={r.decline_reason} source={r.reason_source} />
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <LoadMore {...list} />
        </>
      );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Source requests</CardTitle>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  );
}
