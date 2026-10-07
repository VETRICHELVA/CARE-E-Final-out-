// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Countdown, formatCountdown, isPast, useNow } from "./countdown";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("formatCountdown", () => {
  it("shows minutes and seconds under an hour, rounding up to the second", () => {
    expect(formatCountdown(15 * 60_000)).toBe("15:00");
    expect(formatCountdown(899_500)).toBe("15:00");
    expect(formatCountdown(61_000)).toBe("1:01");
    expect(formatCountdown(1)).toBe("0:01");
  });

  it("shows hours and minutes, then days and hours", () => {
    expect(formatCountdown(90 * 60_000)).toBe("1 h 30 m");
    expect(formatCountdown(4 * 3_600_000 + 5 * 60_000)).toBe("4 h 05 m");
    expect(formatCountdown(51 * 3_600_000)).toBe("2 d 3 h");
  });

  it("never goes below zero", () => {
    expect(formatCountdown(0)).toBe("0:00");
    expect(formatCountdown(-5_000)).toBe("0:00");
  });
});

function Ticking({ to }: { to: string }) {
  return <Countdown to={to} now={useNow()} />;
}

describe("Countdown", () => {
  it("ticks down every second and says when the deadline has passed", () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    vi.setSystemTime(new Date("2026-10-07T06:00:00Z"));
    render(<Ticking to="2026-10-07T06:00:03Z" />);
    const clock = screen.getByTestId("countdown");
    expect(clock.textContent).toBe("0:03 left");
    expect(clock.getAttribute("datetime")).toBe("2026-10-07T06:00:03Z");
    expect(clock.className).toContain("text-destructive");
    act(() => vi.advanceTimersByTime(1000));
    expect(clock.textContent).toBe("0:02 left");
    act(() => vi.advanceTimersByTime(2000));
    expect(clock.textContent).toBe("Deadline passed");
  });

  it("is not urgent with more than five minutes left", () => {
    const now = Date.parse("2026-10-07T06:00:00Z");
    render(<Countdown to="2026-10-07T06:15:00Z" now={now} passed="Lapsed" />);
    const clock = screen.getByTestId("countdown");
    expect(clock.textContent).toBe("15:00 left");
    expect(clock.className).not.toContain("text-destructive");
  });

  it("isPast is true at and after the deadline", () => {
    const at = Date.parse("2026-10-07T06:00:00Z");
    expect(isPast("2026-10-07T06:00:00Z", at - 1)).toBe(false);
    expect(isPast("2026-10-07T06:00:00Z", at)).toBe(true);
  });
});
