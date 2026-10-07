# supplier-web

The supplier app (React 19, TypeScript, Vite, Tailwind, shadcn/ui, TanStack Query): suppliers keep their offers (price, lead time, available quantity) current, answer purchase orders (acknowledge, reject, mark dispatched), and see network demand for their products without seeing which hospitals are short. It only displays what the hub decides and reaches the hub only through `packages/api-client`. Scaffolded in S03; screens built in S10.

- `/` dashboard: new purchase orders (SENT), orders to dispatch (ACKNOWLEDGED), offers not updated in 7 days (they fail the freshness gate).
- `/offers` catalog and offers: inline edit; "Still current" re-confirms a stale offer unchanged.
- `/orders`, `/orders/:id` purchase orders: only the actions the order's state allows (business-rules §8), for `po.respond` users.
- `/demand` network demand: `GET /network/demand` totals per product.

Run with `pnpm --filter supplier-web dev` (:5174, `/api` proxied to the hub on :8000). Screens refresh from the hub's event stream (`liveUpdates: true`); nothing polls. Tests: `pnpm --filter supplier-web test` (Vitest + Testing Library against a fake hub from `@care-e/ui/testing`).
