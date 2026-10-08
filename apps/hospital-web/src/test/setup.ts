// Testing Library waits 1 s by default for `findBy*`/`waitFor`. Under a parallel `pnpm test` on a
// busy machine, the first render of a screen with chained hub reads (decision-panel.test.tsx)
// can take longer, so allow 3 s. No assertion changes; a test that fails still fails.
import { configure } from "@testing-library/react";

configure({ asyncUtilTimeout: 3000 });
