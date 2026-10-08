// Notifications (S12): the signed-in user's own, newest first, and the header bell with the
// unread count. Escalations are the only kind the hub writes so far; `recommendation.status_changed`
// events refresh both (EVENT_QUERIES in packages/api-client), so nothing polls.
import { Link } from "react-router";
import {
  Badge,
  BellIcon,
  Button,
  cn,
  EmptyState,
  ErrorState,
  formatDateTime,
  Loading,
  toast,
  useCan,
} from "@care-e/ui";
import { type Notification, useMarkRead, useNotifications, useUnreadCount } from "../api";
import { LoadMore, PageHeader, RecordedReason } from "../components/page";
import { NOTIFICATION_LABELS, SHORTAGE_READERS } from "../display";

const str = (v: unknown) => (typeof v === "string" ? v : undefined);

/** The header bell: a link to the list, with the hub's unread count. */
export function NotificationBell() {
  const unread = useUnreadCount();
  const count = unread.data?.count ?? 0;
  const shown = unread.data?.more ? `${count}+` : String(count);
  return (
    <Link
      to="/notifications"
      aria-label={count > 0 ? `Notifications, ${shown} unread` : "Notifications"}
      className="relative inline-flex size-9 items-center justify-center rounded-md hover:bg-accent"
    >
      <BellIcon className="size-5" aria-hidden />
      {count > 0 && (
        <span
          data-testid="unread-count"
          className="absolute -top-0.5 -right-0.5 min-w-5 rounded-full bg-destructive px-1 text-center text-xs leading-5 font-medium text-white"
        >
          {shown}
        </span>
      )}
    </Link>
  );
}

function Item({ notification: n }: { notification: Notification }) {
  const canReadShortages = useCan(SHORTAGE_READERS);
  const markRead = useMarkRead();
  const unread = n.read_at === null;
  const shortageId = str(n.payload.shortage_id);
  const reason = str(n.payload.reason) ?? null;
  const expiresAt = str(n.payload.expires_at);
  return (
    <li
      data-testid="notification"
      data-unread={unread || undefined}
      className={cn("grid gap-1 p-3 text-sm", unread && "bg-primary/5")}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{NOTIFICATION_LABELS[n.type] ?? n.type}</span>
        {unread && <Badge>Unread</Badge>}
        <time dateTime={n.created_at} className="text-xs text-muted-foreground">
          {formatDateTime(n.created_at)}
        </time>
      </div>
      {n.type === "recommendation.escalated" && (
        <>
          <RecordedReason reason={reason} source={reason ? "USER" : "SYSTEM"} />
          {expiresAt && (
            <span className="text-xs text-muted-foreground">
              Valid until {formatDateTime(expiresAt)}
            </span>
          )}
        </>
      )}
      <div className="flex flex-wrap items-center gap-3">
        {shortageId && canReadShortages && (
          <Link to={`/shortages/${shortageId}`} className="text-primary hover:underline">
            Open shortage
          </Link>
        )}
        {unread && (
          <Button
            size="sm"
            variant="outline"
            disabled={markRead.isPending}
            onClick={() => markRead.mutate(n.id, { onError: (e) => toast.error(e.message) })}
          >
            Mark as read
          </Button>
        )}
      </div>
    </li>
  );
}

export function NotificationsPage() {
  const list = useNotifications();
  let body;
  if (list.isPending) body = <Loading label="Loading notifications…" />;
  else if (list.isError) body = <ErrorState error={list.error} />;
  else {
    const items = list.data.pages.flatMap((p) => p.items);
    body =
      items.length === 0 ? (
        <EmptyState title="No notifications">
          You are notified here when a recommendation is escalated to you.
        </EmptyState>
      ) : (
        <ul aria-label="Notifications" className="divide-y rounded-md border">
          {items.map((n) => (
            <Item key={n.id} notification={n} />
          ))}
        </ul>
      );
  }
  return (
    <div className="grid gap-4">
      <PageHeader title="Notifications" description="Yours only, newest first." />
      {body}
      {list.isSuccess && <LoadMore {...list} />}
    </div>
  );
}
