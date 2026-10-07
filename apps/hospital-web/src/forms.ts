// zod helpers for form input (strings from <input>). They check shape only: whole numbers,
// dates, amounts. Business rules (e.g. whether a shortfall is 0) are the hub's to decide.
import { type ChangeEvent, useState } from "react";
import { z } from "zod";

const MAX_INT = 2_147_483_647; // the hub's int4 limit

/** A required whole number, 0 or more. */
export const count = (what: string) =>
  z
    .string()
    .trim()
    .min(1, `Enter ${what}.`)
    .regex(/^\d+$/, "Enter a whole number, 0 or more.")
    .transform(Number)
    .refine((n) => n <= MAX_INT, "That number is too large.");

/** An optional whole number: blank means "not given". */
export const optionalCount = z
  .string()
  .trim()
  .regex(/^\d*$/, "Enter a whole number, 0 or more.")
  .transform((s) => (s === "" ? undefined : Number(s)))
  .refine((n) => n === undefined || n <= MAX_INT, "That number is too large.");

/** Rupees as typed ("14" or "14.50") → integer paise, without floating-point rounding. */
export const rupees = z
  .string()
  .trim()
  .regex(/^\d+(\.\d{1,2})?$/, "Enter an amount in rupees, e.g. 14.00.")
  .transform((s) => {
    const [whole, fraction = ""] = s.split(".");
    return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  })
  .refine((n) => n <= MAX_INT, "That amount is too large.");

/** Paise → the rupee string an amount input starts with: 1400 → "14.00". */
export const paiseToRupees = (paise: number) =>
  `${Math.floor(paise / 100)}.${String(paise % 100).padStart(2, "0")}`;

/** Optional free text: blank means "not given". */
export const optionalText = z
  .string()
  .trim()
  .transform((s) => s || undefined);

/** The first message for each field, keyed by field name. */
export function fieldErrors(error: z.ZodError): Record<string, string> {
  const out: Record<string, string> = {};
  for (const issue of error.issues) out[String(issue.path[0] ?? "form")] ??= issue.message;
  return out;
}

/** Controlled string inputs plus their errors; `errors.form` holds a form-level message
 *  such as the hub's reply. */
export function useForm<K extends string>(initial: Record<K, string>) {
  const [values, setValues] = useState(initial);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const bind = (name: K) => ({
    id: name,
    name,
    value: values[name],
    "aria-invalid": errors[name] ? true : undefined,
    onChange: (e: ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
      setValues((v) => ({ ...v, [name]: e.target.value })),
  });
  /** Parses with `schema`; on failure shows the messages and returns undefined. */
  function parse<S extends z.ZodType>(schema: S): z.output<S> | undefined {
    const result = schema.safeParse(values);
    setErrors(result.success ? {} : fieldErrors(result.error));
    return result.success ? result.data : undefined;
  }
  return { values, errors, setErrors, bind, parse };
}
