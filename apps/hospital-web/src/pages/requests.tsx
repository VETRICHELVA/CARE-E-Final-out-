// Incoming requests (apps-ai-iot.md, hospital-web): what other hospitals ask this hospital to
// supply, with a live countdown to the response deadline. Accept and Decline show only to a user
// with `source_request.respond` while the request is REQUESTED and its deadline has not passed;
// the hub still checks every answer and its timers expire late requests.
import type { ReactNode } from "react";
import { ApiError } from "@care-e/api-client";
import {
  ConfirmDialog,
  Countdown,
  EmptyState,
  ErrorState,
  formatDateTime,
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
  type Product,
  type SourceRequest,
  useProducts,
  useRespondToRequest,
  useSourceRequests,
} from "../api";
import { DeclineReason, LoadMore, PageHeader } from "../components/page";
import { ANSWERABLE, qty } from "../display";
import { PriorityBadge } from "./shortages";

/** A hub refusal: a 409 means the request moved on, so say so; the lists refetch on their own. */
async function answer(send: () => Promise<unknown>, done: string) {
  try {
    await send();
    toast.success(done);
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) toast.error(e.message);
    throw e;
  }
}

function Answer({ request, product }: { request: SourceRequest; product: Product | undefined }) {
  const accept = useRespondToRequest("accept");
  const decline = useRespondToRequest("decline");
  const amount = qty(request.qty, product);
  return (
    <div className="flex justify-end gap-2">
      <ConfirmDialog
        trigger="Accept"
        title={`Accept ${request.requester_org_name}'s request?`}
        description={`The hub places a tentative hold on ${amount} of your transferable stock, earliest expiry first. ${request.requester_org_name} then decides whether to go ahead.`}
        confirmLabel="Accept and hold"
        onConfirm={(reason) =>
          answer(
            () => accept.mutateAsync({ id: request.id, reason }),
            `Accepted. ${amount} are on hold.`,
          )
        }
      />
      <ConfirmDialog
        trigger="Decline"
        title={`Decline ${request.requester_org_name}'s request?`}
        description={`The hub looks for another source for ${request.requester_org_name} without you.`}
        confirmLabel="Decline"
        destructive
        onConfirm={(reason) =>
          answer(() => decline.mutateAsync({ id: request.id, reason }), "Request declined.")
        }
      />
    </div>
  );
}

function ProductCell({ product }: { product: Product | undefined }) {
  return (
    <TableCell>
      <div className="font-medium">{product?.name ?? "Unknown product"}</div>
      <div className="text-xs text-muted-foreground">{product?.code}</div>
    </TableCell>
  );
}

/** Loading, error and empty states around a paged list of requests. */
function RequestList({
  list,
  empty,
  children,
}: {
  list: ReturnType<typeof useSourceRequests>;
  empty: ReactNode;
  children: (rows: SourceRequest[]) => ReactNode;
}) {
  const products = useProducts();
  if (list.isPending || products.isPending) return <Loading label="Loading requests…" />;
  if (list.isError) return <ErrorState error={list.error} />;
  if (products.isError) return <ErrorState error={products.error} />;
  const rows = list.data.pages.flatMap((p) => p.items);
  if (rows.length === 0) return <>{empty}</>;
  return (
    <>
      <div className="rounded-lg border bg-card">{children(rows)}</div>
      <LoadMore {...list} />
    </>
  );
}

function Awaiting() {
  const now = useNow();
  const canRespond = useCan("source_request.respond");
  const list = useSourceRequests({ direction: "incoming", status: ANSWERABLE });
  const products = useProducts();
  return (
    <RequestList list={list} empty={<EmptyState title="No requests awaiting your response" />}>
      {(rows) => (
        <Table aria-label="Awaiting response">
          <TableHeader>
            <TableRow>
              <TableHead>Product</TableHead>
              <TableHead className="text-right">Qty</TableHead>
              <TableHead>Requested by</TableHead>
              <TableHead>Priority</TableHead>
              <TableHead>Needed by</TableHead>
              <TableHead>Respond within</TableHead>
              {canRespond && <TableHead className="sr-only">Actions</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => {
              const product = products.data?.byId.get(r.product_id);
              const open = r.status === ANSWERABLE && !isPast(r.sla_deadline, now);
              return (
                <TableRow key={r.id} data-testid={`request-${r.id}`}>
                  <ProductCell product={product} />
                  <TableCell className="text-right font-semibold">{qty(r.qty, product)}</TableCell>
                  <TableCell>{r.requester_org_name}</TableCell>
                  <TableCell>
                    <PriorityBadge priority={r.priority} />
                  </TableCell>
                  <TableCell>{formatDateTime(r.required_by)}</TableCell>
                  <TableCell>
                    <Countdown to={r.sla_deadline} now={now} />
                  </TableCell>
                  {canRespond && (
                    <TableCell>
                      {open ? (
                        <Answer request={r} product={product} />
                      ) : (
                        <span className="text-xs text-muted-foreground">Too late to answer</span>
                      )}
                    </TableCell>
                  )}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </RequestList>
  );
}

/** What happened to a request once answered, from the hub's own fields. */
function Outcome({
  request,
  product,
  now,
}: {
  request: SourceRequest;
  product: Product | undefined;
  now: number;
}) {
  if (request.status === "TENTATIVE_HOLD" && request.hold_expires_at)
    return (
      <span>
        {qty(request.held_qty, product)} held ·{" "}
        <Countdown to={request.hold_expires_at} now={now} passed="Hold lapsed" />
      </span>
    );
  if (request.status === "DECLINED")
    return <DeclineReason reason={request.decline_reason} source={request.reason_source} />;
  // S19: another source accepted first (CRITICAL), a split partner dropped out, or the
  // requester cancelled; the hub released anything this hospital held for it.
  if (request.status === "SUPERSEDED")
    return <span className="text-muted-foreground">No longer needed</span>;
  return null;
}

function History() {
  const now = useNow();
  const list = useSourceRequests({ direction: "incoming" });
  const products = useProducts();
  return (
    <RequestList list={list} empty={<EmptyState title="No requests yet" />}>
      {(all) => {
        const rows = all.filter((r) => r.status !== ANSWERABLE);
        if (rows.length === 0)
          return <p className="p-4 text-sm text-muted-foreground">No answered requests yet.</p>;
        return (
          <Table aria-label="Answered and closed">
            <TableHeader>
              <TableRow>
                <TableHead>Product</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead>Requested by</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Detail</TableHead>
                <TableHead>Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => {
                const product = products.data?.byId.get(r.product_id);
                return (
                  <TableRow key={r.id} data-testid={`request-${r.id}`}>
                    <ProductCell product={product} />
                    <TableCell className="text-right">{qty(r.qty, product)}</TableCell>
                    <TableCell>{r.requester_org_name}</TableCell>
                    <TableCell>
                      <StatusChip state={r.status} />
                    </TableCell>
                    <TableCell className="text-sm">
                      <Outcome request={r} product={product} now={now} />
                    </TableCell>
                    <TableCell>{formatDateTime(r.updated_at)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        );
      }}
    </RequestList>
  );
}

export function RequestsPage() {
  return (
    <>
      <PageHeader
        title="Incoming requests"
        description="Other hospitals asking you to supply stock. Accepting places a tentative hold."
      />
      <section aria-labelledby="awaiting-heading" className="mb-6">
        <h2 id="awaiting-heading" className="mb-2 font-semibold">
          Awaiting your response
        </h2>
        <Awaiting />
      </section>
      <section aria-labelledby="history-heading">
        <h2 id="history-heading" className="mb-2 font-semibold">
          Answered and closed
        </h2>
        <History />
      </section>
    </>
  );
}
