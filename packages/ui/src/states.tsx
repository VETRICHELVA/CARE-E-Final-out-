import type { ReactNode } from "react";
import { Loader2Icon } from "lucide-react";

const box = "flex flex-col items-center gap-2 rounded-lg border border-dashed p-10 text-center";

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={box}>
      <p className="font-medium">{title}</p>
      {children && <div className="text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}

/** Shows the hub's own error message; the hub decides what went wrong. */
export function ErrorState({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div role="alert" className={`${box} border-destructive/40 text-destructive`}>
      <p className="font-medium">Couldn't load this</p>
      <p className="text-sm">{message}</p>
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div
      role="status"
      className="flex items-center justify-center gap-2 p-10 text-muted-foreground"
    >
      <Loader2Icon className="size-4 animate-spin" aria-hidden />
      <span className="text-sm">{label}</span>
    </div>
  );
}
