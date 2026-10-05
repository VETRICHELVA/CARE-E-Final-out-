# @care-e/ui

Shared React components (Tailwind + shadcn/ui) used by the three apps, so screens look and behave the same across hospital, supplier and delivery. Holds presentation only — no business rules. Scaffolded in S03.

Adding a shadcn/ui component: `pnpm dlx shadcn@4.21.1 add <name>` here, then rewrite its `@/…` imports to relative ones (`from "../../lib/utils"`, `from "./button"`) and check `package.json`: the CLI may add packages we don't use (in S03 it added `cn` and `next-themes`; we use `clsx` + `tailwind-merge` and a light theme). Export the new component from `src/index.ts`.
