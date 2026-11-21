import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReasonPicker } from "./reason-picker";

describe("ReasonPicker", () => {
  it("shows the database counts per reason, including a reason it does not know", () => {
    const counts = {
      total: 7,
      reasons: [
        { reason: "no_contact", label: "no contact route", count: 4 },
        { reason: "low_intent", label: "low intent", count: 2 },
        { reason: "older_than_8h", label: "older than 8 h", count: 0 },
        { reason: "excluded_market", label: "excluded market", count: 0 },
        { reason: "freelance", label: "freelance platform", count: 0 },
        { reason: "unknown", label: "unknown", count: 1 },
      ],
    };
    const onChange = vi.fn();
    render(<ReasonPicker reason="no_contact" counts={counts} onChange={onChange} />);
    expect(screen.getByTestId("count-all")).toHaveTextContent("7");
    expect(screen.getByTestId("count-no_contact")).toHaveTextContent("4");
    expect(screen.getByTestId("count-older_than_8h")).toHaveTextContent("0");
    expect(screen.getByTestId("count-unknown")).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: /No contact route/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: /Low intent/ }));
    expect(onChange).toHaveBeenCalledWith("low_intent");
    fireEvent.click(screen.getByRole("button", { name: /All reasons/ }));
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it("renders an unknown count as a dash, never a number", () => {
    render(<ReasonPicker reason={undefined} counts={undefined} onChange={vi.fn()} />);
    for (const key of ["all", "no_contact", "low_intent", "older_than_8h", "excluded_market", "freelance"]) {
      expect(screen.getByTestId(`count-${key}`)).toHaveTextContent("—");
    }
  });
});
