import type { ReactNode } from "react";
import { Badge, Field, FieldError, FieldLabel } from "@care-e/ui";
import { NO_REASON } from "../display";

// Shared with the other apps since S10.
export { LoadMore, PageHeader } from "@care-e/ui";

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

/** A recorded reason, labelled by who wrote it (business-rules.md §10): a user's own words
 *  (`USER`), or the hub's wording (`SYSTEM`: "No reason was entered." or a timer's cause). A
 *  SYSTEM reason the hub returns as null means nothing was typed. */
export function RecordedReason({
  reason,
  source,
}: {
  reason: string | null;
  source: string | null;
}) {
  if (source !== "USER" && source !== "SYSTEM") return null;
  const user = source === "USER";
  return (
    <span
      data-testid="recorded-reason"
      data-reason-source={source}
      className="inline-flex flex-wrap items-baseline gap-1.5"
    >
      <Badge variant={user ? "secondary" : "outline"}>{user ? "User reason" : "System"}</Badge>
      <span className={user ? undefined : "text-muted-foreground italic"}>
        {reason ?? NO_REASON}
      </span>
    </span>
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
