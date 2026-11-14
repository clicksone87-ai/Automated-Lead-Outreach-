import { render, screen } from "@testing-library/react";
import { Inbox } from "lucide-react";
import { describe, expect, it } from "vitest";
import { Gauge } from "./meters";
import { EmptyState, ErrorState } from "./states";
import { StatusPill } from "./status-pill";

describe("common components", () => {
  it("StatusPill renders its label with tone classes", () => {
    render(<StatusPill tone="success">Healthy</StatusPill>);
    const pill = screen.getByText("Healthy");
    expect(pill.className).toContain("bg-success-soft");
  });

  it("EmptyState shows title and description", () => {
    render(<EmptyState icon={Inbox} title="Nothing here" description="Come back later" />);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    expect(screen.getByText("Come back later")).toBeInTheDocument();
  });

  it("ErrorState explains an unreachable API", () => {
    render(<ErrorState error={new TypeError("Failed to fetch")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Can't reach the API");
  });

  it("Gauge exposes an accessible score", () => {
    render(<Gauge value={72} label="SEO" />);
    expect(screen.getByText("SEO: 72 out of 100")).toBeInTheDocument();
  });
});
