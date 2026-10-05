// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { STATUS, StatusChip } from "./status";

afterEach(cleanup);

// Copied from business-rules.md §8, machine by machine.
const SPEC_STATES = {
  Shortage:
    "DRAFT OPEN MATCHING AWAITING_DECISION IN_FULFILLMENT RECEIVED RESOLVED PARTIALLY_RESOLVED CANCELLED",
  SourceRequest: "REQUESTED TENTATIVE_HOLD DECLINED EXPIRED SUPERSEDED CONFIRMED",
  Recommendation: "PENDING APPROVED REJECTED ESCALATED EXPIRED",
  PurchaseOrder: "SENT ACKNOWLEDGED DISPATCHED DELIVERED REJECTED",
  Shipment: "CREATED ASSIGNED PICKED_UP IN_TRANSIT DELIVERED RECONCILED",
};

it.each(Object.entries(SPEC_STATES))("labels every %s state", (_, states) => {
  for (const state of states.split(" ")) expect(STATUS[state]?.label, state).toBeTruthy();
});

it("renders a label, and an unknown state as-is", () => {
  render(<StatusChip state="TENTATIVE_HOLD" />);
  render(<StatusChip state="SOMETHING_NEW" />);
  expect(screen.getByText("Tentative hold")).toBeTruthy();
  expect(screen.getByText("SOMETHING_NEW")).toBeTruthy();
});
