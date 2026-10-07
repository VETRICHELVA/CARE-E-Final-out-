import type { ReactNode } from "react";
import { Button, Field, FieldError, FieldLabel } from "@care-e/ui";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/** "Load more" for a paged hub list. */
export function LoadMore({
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
}: {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => unknown;
}) {
  if (!hasNextPage) return null;
  return (
    <div className="mt-3 flex justify-center">
      <Button variant="outline" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
        {isFetchingNextPage ? "Loading…" : "Load more"}
      </Button>
    </div>
  );
}

/** A labelled form field with its validation message. */
export function FormField({
  id,
  label,
  error,
  hint,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Field data-invalid={error ? true : undefined}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      {children}
      {hint && !error && <p className="text-xs text-muted-foreground">{hint}</p>}
      {error && <FieldError id={`${id}-error`}>{error}</FieldError>}
    </Field>
  );
}

/** A decline's reason as the hub recorded it: the source's own words, or, when it typed none
 *  (`reason_source: SYSTEM`), the hub's standard wording (CLAUDE.md, Honest audit). */
export function DeclineReason({
  reason,
  source,
}: {
  reason: string | null;
  source: "USER" | "SYSTEM" | null;
}) {
  if (reason) return <span data-testid="decline-reason">{reason}</span>;
  if (source === "SYSTEM")
    return (
      <span data-testid="decline-reason" className="text-muted-foreground">
        No reason was entered.
      </span>
    );
  return null;
}
