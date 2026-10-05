# S07 — Events, webhooks and live updates

**Milestone:** M1 · **Depends on:** S06, S03 · **Workstream:** Backend (plus a small frontend hook) · **Can run alongside:** S08 (the screens can poll until this lands)

## Read first
- `docs/specs/api-and-events.md` — Events, Webhooks, Realtime in the apps; the S07 rows
- `docs/specs/domain-model.md` — Records (EventOutbox, WebhookSubscription, WebhookDelivery)

## Goal
Every state change produces an event that the right organizations receive live in their apps, and optionally by signed webhook, without ever leaking to other organizations or firing for a rolled-back change.

## Build
- **`events.emit(session, type, org_ids, data)`** writes to EventOutbox in the caller's transaction. Retrofit `emit` calls into every transition built in S04–S06, using the event list in the spec.
- **Publisher job** (in the arq worker): reads unpublished outbox rows in order, publishes them to Redis pub/sub, and marks them published.
- **`GET /events/stream`:** server-sent events for the caller's org, filtered by `org_ids`, with a 15-second heartbeat and `Last-Event-ID` resume from the outbox.
- **Webhooks:**
  - Subscription create, list and delete.
  - Delivery job with the `X-CareE-Signature` HMAC and exponential backoff for 24 h.
  - WebhookDelivery records each attempt.
- **`packages/api-client`:** a `useEventStream()` hook mapping event types to TanStack Query keys to invalidate. Reconnect with backoff.

## Out of scope
Notification UI and emails.

## Acceptance criteria
- [ ] An event emitted in a transaction that rolls back is never published (test).
- [ ] An org B client never receives an event addressed only to org A (test over SSE).
- [ ] Webhook signatures verify with the subscription secret; retry timing follows the schedule (frozen-clock test).
- [ ] Reconnecting with `Last-Event-ID` replays missed events.
- [ ] `useEventStream()` invalidates the right query keys for each event type (unit test with a mocked EventSource).

## Verify
```
make test-hub && make test-web && make lint && make client
```
