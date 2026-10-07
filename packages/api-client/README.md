# @care-e/api-client

The typed TypeScript client for the hub, GENERATED from the hub's OpenAPI spec (`/api/v1/openapi.json`) with openapi-typescript and openapi-fetch by `make client`. Never hand-edit it; CI regenerates it and fails if the committed copy drifts. Generation is wired up in S03.

`src/schema.d.ts` and `openapi.json` are the generated part. The hand-written wrappers are `src/index.ts` (auth, `unwrap`, `ApiError`) and `src/events.ts`: `useEventStream()` keeps the cache fresh from `GET /events/stream`. Its `EVENT_QUERIES` maps each event type to the API path templates it makes stale, so every query key must start with the path template it reads, then its params: `["/api/v1/shortages/{shortage_id}", { shortage_id }]`.
