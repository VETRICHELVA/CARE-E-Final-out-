// "Share location" (apps-ai-iot.md, Driver jobs): while on, the phone's position is sent for
// each job on the way every 30 s. The hub decides whether to take a ping (assigned driver,
// shipment on the way); a refusal is shown, and sharing carries on for the other jobs.
import { useEffect, useRef, useState } from "react";
import { cn, formatDateTime } from "@care-e/ui";
import { postLocation } from "../api";
import { PING_INTERVAL_MS } from "../display";

export type SharingState = "off" | "starting" | "on" | "denied" | "unsupported";

/** GeolocationPositionError.PERMISSION_DENIED */
const PERMISSION_DENIED = 1;

export function useLocationSharing(shipmentIds: readonly string[], intervalMs = PING_INTERVAL_MS) {
  const [state, setState] = useState<SharingState>("off");
  const [lastSent, setLastSent] = useState<string>();
  const [problem, setProblem] = useState<string>();
  // The jobs can change while sharing (a delivery, a new assignment) without restarting it.
  const ids = useRef(shipmentIds);
  useEffect(() => {
    ids.current = shipmentIds;
  }, [shipmentIds]);

  const sharing = state === "starting" || state === "on";
  useEffect(() => {
    if (!sharing) return;
    let stopped = false;
    const tick = () =>
      navigator.geolocation.getCurrentPosition(
        async ({ coords }) => {
          const results = await Promise.allSettled(
            ids.current.map((id) => postLocation(id, coords.latitude, coords.longitude)),
          );
          if (stopped) return;
          setState("on");
          if (results.some((r) => r.status === "fulfilled")) setLastSent(new Date().toISOString());
          const failed = results.find((r) => r.status === "rejected");
          setProblem(
            failed
              ? `The hub did not take the last position: ${failed.reason instanceof Error ? failed.reason.message : "unknown error"}`
              : undefined,
          );
        },
        (error) => {
          if (stopped) return;
          if (error.code === PERMISSION_DENIED) {
            setState("denied");
            setProblem(undefined);
          } else {
            setProblem("Couldn't get your position. Trying again in 30 seconds.");
          }
        },
        { enableHighAccuracy: true, timeout: 20_000, maximumAge: 10_000 },
      );
    tick();
    const timer = setInterval(tick, intervalMs);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [sharing, intervalMs]);

  return {
    state,
    lastSent,
    problem,
    start: () => {
      setProblem(undefined);
      setState(
        typeof navigator !== "undefined" && navigator.geolocation ? "starting" : "unsupported",
      );
    },
    stop: () => {
      setState("off");
      setProblem(undefined);
    },
  };
}

const STATUS_TEXT: Record<SharingState, string> = {
  off: "Your position is not being shared.",
  starting: "Getting your position…",
  on: "Sending your position every 30 seconds for each job on the way. Keep this page open.",
  denied:
    "Location access is blocked for this site. Allow location in your browser's site settings, then turn sharing on again.",
  unsupported: "This browser cannot share your location.",
};

export function ShareLocation({ shipmentIds }: { shipmentIds: readonly string[] }) {
  const { state, lastSent, problem, start, stop } = useLocationSharing(shipmentIds);
  const on = state === "starting" || state === "on";
  return (
    <section
      aria-label="Location sharing"
      className="mb-4 grid gap-2 rounded-lg border bg-card p-4"
    >
      <button
        type="button"
        role="switch"
        aria-checked={on}
        onClick={on ? stop : start}
        className="flex min-h-14 w-full items-center justify-between gap-3 rounded-md text-left text-base font-medium"
      >
        <span>Share location</span>
        <span
          aria-hidden
          className={cn(
            "relative inline-flex h-8 w-14 shrink-0 items-center rounded-full transition-colors",
            on ? "bg-primary" : "bg-muted-foreground/30",
          )}
        >
          <span
            className={cn(
              "inline-block size-6 rounded-full bg-white shadow transition-transform",
              on ? "translate-x-7" : "translate-x-1",
            )}
          />
        </span>
      </button>
      <p
        role={state === "denied" || state === "unsupported" ? "alert" : undefined}
        className={cn(
          "text-sm",
          state === "denied" || state === "unsupported"
            ? "text-destructive"
            : "text-muted-foreground",
        )}
        data-testid="sharing-status"
      >
        {STATUS_TEXT[state]}
      </p>
      {on && lastSent && (
        <p className="text-sm text-muted-foreground" data-testid="last-sent">
          Last sent {formatDateTime(lastSent)}
        </p>
      )}
      {on && problem && <p className="text-sm text-destructive">{problem}</p>}
    </section>
  );
}
