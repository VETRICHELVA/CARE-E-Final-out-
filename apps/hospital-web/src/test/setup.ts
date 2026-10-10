// Shared Vitest setup. `pnpm test` runs every package's suite in parallel, and a screen's first
// render (cold module load) can then take longer than Testing Library's 1 s default for
// `findBy`/`waitFor`; tests chain several of them, so allow more time per wait instead of retrying.
import { configure } from "@testing-library/react";

configure({ asyncUtilTimeout: 4000 });
