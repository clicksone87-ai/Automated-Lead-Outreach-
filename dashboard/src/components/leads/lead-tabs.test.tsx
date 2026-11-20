import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InterestPicker, SourcePicker } from "./lead-tabs";

describe("lead section pickers", () => {
  it("shows backend counts and keeps low/unscored apart", () => {
    const counts = [{
      family: "social" as const,
      high: 2,
      medium: 3,
      low: 4,
      unscored: 5,
    }];
    render(
      <>
        <SourcePicker family="social" counts={counts} onChange={vi.fn()} />
        <InterestPicker tier={undefined} family="social" counts={counts} onChange={vi.fn()} />
      </>
    );

    expect(screen.getAllByText("14")).toHaveLength(2);
    expect(screen.getAllByText("5").length).toBeGreaterThan(0);
    expect(screen.getAllByText("4").length).toBeGreaterThan(0);
  });
});
