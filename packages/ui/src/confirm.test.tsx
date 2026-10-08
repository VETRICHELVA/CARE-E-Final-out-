// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./confirm";

afterEach(cleanup);

const open = (onConfirm: (reason: string | undefined) => Promise<unknown>) => {
  render(
    <ConfirmDialog
      trigger="Cancel shortage"
      title="Cancel?"
      confirmLabel="Yes"
      onConfirm={onConfirm}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancel shortage" }));
};

describe("ConfirmDialog", () => {
  it("sends no reason when the box is left empty", async () => {
    const onConfirm = vi.fn(async () => undefined);
    open(onConfirm);
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith(undefined));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("sends the typed reason", async () => {
    const onConfirm = vi.fn(async () => undefined);
    open(onConfirm);
    fireEvent.change(screen.getByLabelText("Reason (optional)"), {
      target: { value: "Covered locally" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith("Covered locally"));
  });

  it("stays open and shows the hub's message when the action fails", async () => {
    open(async () => {
      throw new Error("Cannot move a shortage from RESOLVED to CANCELLED.");
    });
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Cannot move a shortage from RESOLVED to CANCELLED.",
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("styles the trigger with triggerClassName", () => {
    render(
      <ConfirmDialog
        trigger="Picked up"
        title="Record the pickup?"
        confirmLabel="Yes"
        triggerClassName="h-14 w-full"
        onConfirm={async () => undefined}
      />,
    );
    expect(screen.getByRole("button", { name: "Picked up" }).className).toContain("h-14 w-full");
  });
});
