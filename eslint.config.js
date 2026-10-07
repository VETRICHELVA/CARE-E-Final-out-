import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default defineConfig(
  globalIgnores([
    "**/dist/",
    "**/coverage/",
    "**/.venv/",
    "packages/api-client/src/schema.d.ts",
    "e2e/test-results/",
    "e2e/playwright-report/",
    ".claude/worktrees/",
  ]),
  js.configs.recommended,
  tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
);
