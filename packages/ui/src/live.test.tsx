// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAuth, useEventStream } from "@care-e/api-client";

/** A mocked browser EventSource. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener() {}
  close() {
    this.closed = true;
  }
}

const SHORTAGE = ["/api/v1/shortages/{shortage_id}", { shortage_id: "s1" }] as const;

function Screen({ live = true, onFetch }: { live?: boolean; onFetch: () => void }) {
  useEventStream(live);
  useQuery({
    queryKey: SHORTAGE,
    queryFn: () => {
      onFetch();
      return { status: "MATCHING" };
    },
  });
  return null;
}

function renderScreen(live = true) {
  const fetches = vi.fn();
  const queryClient = new QueryClient();
  const view = render(
    <QueryClientProvider client={queryClient}>
      <Screen live={live} onFetch={fetches} />
    </QueryClientProvider>,
  );
  return { fetches, view };
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      expect(new URL(request.url).pathname).toBe("/api/v1/events/ticket");
      expect(request.headers.get("Authorization")).toBe("Bearer a");
      return new Response(JSON.stringify({ ticket: "t-1", expires_at: "" }), {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useAuth.setState({ tokens: null });
});

describe("useEventStream", () => {
  it("refetches a screen's query when the hub reports a change to it", async () => {
    useAuth.setState({ tokens: { access: "a", refresh: "r" } });
    const { fetches, view } = renderScreen();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const source = FakeEventSource.instances[0]!;
    expect(source.url).toBe("/api/v1/events/stream?ticket=t-1");
    await waitFor(() => expect(fetches).toHaveBeenCalledTimes(1));

    const envelope = {
      id: "e1",
      type: "shortage.status_changed",
      occurred_at: "",
      org_ids: ["o1"],
      data: { shortage_id: "s1", from: "MATCHING", to: "CANCELLED" },
    };
    act(() => source.onmessage?.(new MessageEvent("message", { data: JSON.stringify(envelope) })));
    await waitFor(() => expect(fetches).toHaveBeenCalledTimes(2));

    // Another shortage's event leaves this screen alone.
    const other = { ...envelope, data: { ...envelope.data, shortage_id: "s2" } };
    act(() => source.onmessage?.(new MessageEvent("message", { data: JSON.stringify(other) })));
    await new Promise((r) => setTimeout(r, 50));
    expect(fetches).toHaveBeenCalledTimes(2);

    view.unmount();
    expect(source.closed).toBe(true);
  });

  it("stays closed while signed out or turned off, and closes on sign-out", async () => {
    renderScreen();
    cleanup();
    useAuth.setState({ tokens: { access: "a", refresh: "r" } });
    renderScreen(false);
    await new Promise((r) => setTimeout(r, 50));
    expect(FakeEventSource.instances).toHaveLength(0);
    cleanup();

    renderScreen();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    act(() => useAuth.setState({ tokens: null }));
    expect(FakeEventSource.instances[0]!.closed).toBe(true);
  });
});
