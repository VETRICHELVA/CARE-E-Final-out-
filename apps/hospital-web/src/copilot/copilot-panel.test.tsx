import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { meAs } from "../test/fixtures";
import { fakeHub, renderAs } from "../test/hub";
import { CopilotPanel } from "./copilot-panel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const SHORTAGE = "5a000000-0000-4000-8000-000000000001";
const CONFIGURED = { configured: true, model: "claude-opus-5-5" };

const trace = [
  {
    tool: "get_shortage",
    input: { shortage_id: SHORTAGE },
    ok: true,
    label: "shortage: Surgical Kit A",
    result: {},
    from_context: true,
  },
  {
    tool: "get_match_run",
    input: { shortage_id: SHORTAGE },
    ok: true,
    label: "match run #1",
    result: {},
    from_context: false,
  },
  {
    tool: "get_match_run",
    input: { shortage_id: SHORTAGE },
    ok: true,
    label: "match run #1",
    result: {},
    from_context: false,
  },
  {
    tool: "get_audit",
    input: {},
    ok: false,
    label: "audit (not available)",
    result: {},
    from_context: false,
  },
];

function open(path = "/", route = path) {
  renderAs(meAs("APPROVER"), <CopilotPanel />, { path, route });
  fireEvent.click(screen.getByRole("button", { name: "Ask copilot" }));
}

function ask(question: string) {
  fireEvent.change(screen.getByLabelText("Question for the copilot"), {
    target: { value: question },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
}

describe("CopilotPanel", () => {
  it("contacts the AI service only once opened", () => {
    const hub = fakeHub({ "GET /ai/status": CONFIGURED });
    renderAs(meAs("APPROVER"), <CopilotPanel />);
    expect(screen.getByRole("button", { name: "Ask copilot" })).toBeTruthy();
    expect(hub.calls).toEqual([]);
  });

  it("says AI is not configured, and nothing else, without a key", async () => {
    fakeHub({ "GET /ai/status": { configured: false, model: null } });
    open();
    const panel = screen.getByRole("region", { name: "Copilot" });
    expect(await within(panel).findByText("AI is not configured.")).toBeTruthy();
    expect(screen.queryByLabelText("Question for the copilot")).toBeNull();
  });

  it("asks with the screen's shortage id and shows the sources of the answer", async () => {
    const hub = fakeHub({
      "GET /ai/status": CONFIGURED,
      "POST /ai/copilot/ask": {
        answer: "Hospital D failed the shelf-life gate: Expires in 12 days; 30 required.",
        tool_trace: trace,
      },
    });
    open(`/shortages/${SHORTAGE}`, "/shortages/:id");
    expect(await screen.findByText("About this shortage")).toBeTruthy();
    ask("Why was Hospital D rejected?");

    expect(await screen.findByTestId("answer")).toHaveProperty(
      "textContent",
      "Hospital D failed the shelf-life gate: Expires in 12 days; 30 required.",
    );
    const chips = within(screen.getByTestId("based-on"));
    expect(chips.getAllByText(/./).map((c) => c.textContent)).toEqual([
      "Based on:",
      "shortage: Surgical Kit A",
      "match run #1",
      "audit (not available)",
    ]);
    const [call] = hub.to("POST", "/ai/copilot/ask");
    expect(JSON.parse(call!.body!)).toEqual({
      question: "Why was Hospital D rejected?",
      context: { shortage_id: SHORTAGE },
    });
    expect(call!.headers.get("Authorization")).toBe("Bearer a"); // the signed-in user's token
    // The panel offers no action of its own: Ask and Close are its only buttons.
    const panel = screen.getByRole("region", { name: "Copilot" });
    expect(
      within(panel)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["Close", "Ask"]);
  });

  it("sends no context off a record's screen", async () => {
    const hub = fakeHub({
      "GET /ai/status": CONFIGURED,
      "POST /ai/copilot/ask": { answer: "That information is not available.", tool_trace: [] },
    });
    open("/inventory");
    await screen.findByLabelText("Question for the copilot");
    ask("Why?");
    await screen.findByTestId("answer");
    expect(JSON.parse(hub.to("POST", "/ai/copilot/ask")[0]!.body!).context).toEqual({});
  });

  it("shows the service's errors in place of an answer", async () => {
    fakeHub({
      "GET /ai/status": CONFIGURED,
      "POST /ai/copilot/ask": Response.json(
        { error: "ai_not_configured", message: "AI is not configured." },
        { status: 503 },
      ),
    });
    open();
    await screen.findByLabelText("Question for the copilot");
    ask("Why?");
    expect((await screen.findByRole("alert")).textContent).toBe("AI is not configured.");
  });

  it("says when the AI service is not running", async () => {
    fakeHub({ "GET /ai/status": new Response("Bad gateway", { status: 502 }) });
    open();
    expect(await screen.findByText("The AI service is not reachable right now.")).toBeTruthy();
  });
});
