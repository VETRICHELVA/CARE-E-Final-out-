# hospital-web

The hospital app (React 19, TypeScript, Vite, Tailwind, shadcn/ui, TanStack Query): inventory, shortages, source requests, approvals, receiving and reconciliation for hospital staff. It only displays what the hub decides and reaches the hub only through `packages/api-client`. Scaffolded in S03.

Screens live in `src/pages/`; TanStack Query hooks over the generated client in `src/api.ts`; display-only labels and state gates in `src/display.ts`; zod form helpers in `src/forms.ts`. Tests run against a fake hub on `fetch` (`src/test/hub.tsx`) with Scenario 1 fixtures (`src/test/fixtures.ts`).
