// Live countdowns to hub deadlines. Display only: the hub's timers decide when a deadline has
// passed (business-rules.md §6), and the hub still refuses a late answer itself.
import { useEffect, useState } from "react";
import { formatDateTime } from "./format";
import { cn } from "./lib/utils";

/** The browser's clock, re-read every `intervalMs` (one second by default). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Time left, rounded up to the second: 899_500 → "15:00", 5_400_000 → "1 h 30 m",
 *  "2 d 3 h" from a day on. Zero or less → "0:00". */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  const seconds = total % 60;
  if (days > 0) return `${days} d ${hours} h`;
  if (hours > 0) return `${hours} h ${pad(minutes)} m`;
  return `${minutes}:${pad(seconds)}`;
}

/** Under this much time left, a countdown turns red. */
const URGENT_MS = 5 * 60_000;

/** Time left until `to` (a hub UTC timestamp), ticking with `now`; `passed` once it is over. */
export function Countdown({
  to,
  now,
  passed = "Deadline passed",
  className,
}: {
  to: string;
  now: number;
  passed?: string;
  className?: string;
}) {
  const left = Date.parse(to) - now;
  return (
    <time
      dateTime={to}
      title={formatDateTime(to)}
      data-testid="countdown"
      className={cn(
        "font-medium tabular-nums",
        left <= 0 ? "text-muted-foreground" : left < URGENT_MS && "text-destructive",
        className,
      )}
    >
      {left <= 0 ? passed : `${formatCountdown(left)} left`}
    </time>
  );
}

/** True once `to` is at or before `now`. */
export const isPast = (to: string, now: number) => Date.parse(to) <= now;
