import { type ReactNode, useId, useState } from "react";
import { Button } from "./components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "./components/ui/dialog";
import { Field, FieldError, FieldLabel } from "./components/ui/field";
import { Textarea } from "./components/ui/textarea";

type ConfirmProps = {
  /** The button that opens the dialog, e.g. "Cancel shortage". */
  trigger: string;
  title: string;
  description?: ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  /** Called with the typed reason, or undefined when the box was left empty. A rejection
   *  keeps the dialog open and shows the error's message (the hub's own words). */
  onConfirm: (reason: string | undefined) => Promise<unknown>;
};

/** A consequential action: asks for confirmation and offers an optional reason box
 *  (apps-ai-iot.md, Shared rules). An empty box sends no reason; the hub then records its own. */
export function ConfirmDialog({
  trigger,
  title,
  description,
  confirmLabel,
  destructive,
  onConfirm,
}: ConfirmProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();

  const change = (next: boolean) => {
    if (pending) return;
    setOpen(next);
    setReason("");
    setError(undefined);
  };
  const confirm = async () => {
    setPending(true);
    setError(undefined);
    try {
      await onConfirm(reason.trim() || undefined);
      setOpen(false);
      setReason("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={change}>
      <DialogTrigger asChild>
        <Button variant={destructive ? "outline" : "default"}>{trigger}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <Field>
          <FieldLabel htmlFor={id}>Reason (optional)</FieldLabel>
          <Textarea id={id} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        {error && <FieldError>{error}</FieldError>}
        <DialogFooter>
          <Button variant="outline" onClick={() => change(false)} disabled={pending}>
            Back
          </Button>
          <Button
            variant={destructive ? "destructive" : "default"}
            onClick={() => void confirm()}
            disabled={pending}
          >
            {pending ? "Working…" : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
