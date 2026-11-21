import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ManualOutreachItem } from "@/lib/outreach";
import { ManualSteps } from "./manual-steps";

function item(overrides: Partial<ManualOutreachItem> = {}): ManualOutreachItem {
  return {
    id: "item-1",
    lead_id: "lead-1",
    business_name: "Bakery",
    lead_status: "awaiting_approval",
    country: "US",
    services: ["logo"],
    channel: "platform_reply",
    post_link: "https://x.com/bakery/status/1",
    comment: "c",
    dm: "d",
    contact_form: "f",
    status: "pending",
    step: "confirm_contact",
    contact_confirmed_at: null,
    contact_confirmed_by: null,
    assets_suggested: ["logo"],
    assets_requested: null,
    assets_progress: null,
    attachments: [],
    sent_at: null,
    sent_by: null,
    done_at: null,
    done_by: null,
    created_at: "2026-11-21T08:00:00Z",
    ...overrides,
  };
}

function handlers() {
  return { onConfirm: vi.fn(), onContactFailed: vi.fn(), onPrepare: vi.fn(), onMarkSent: vi.fn() };
}

describe("ManualSteps", () => {
  it("asks to confirm the contact first, with no send or asset buttons yet", () => {
    const h = handlers();
    render(<ManualSteps item={item()} {...h} />);
    fireEvent.click(screen.getByRole("button", { name: /This contact works/ }));
    fireEvent.click(screen.getByRole("button", { name: /Contact does not work/ }));
    expect(h.onConfirm).toHaveBeenCalledOnce();
    expect(h.onContactFailed).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: /Prepare assets/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Mark as sent/ })).toBeNull();
  });

  it("preselects the lead's requirement and sends the chosen assets", () => {
    const h = handlers();
    render(<ManualSteps item={item({ step: "prepare_assets", contact_confirmed_at: "2026-11-21T08:05:00Z" })} {...h} />);
    expect(screen.getByRole("checkbox", { name: /Logo concepts/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Website preview/ })).not.toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: /Website preview/ }));
    fireEvent.click(screen.getByRole("button", { name: /Prepare assets/ }));
    expect(h.onPrepare).toHaveBeenCalledWith(["logo", "website"]);
  });

  it("cannot prepare with nothing chosen", () => {
    render(<ManualSteps item={item({ step: "prepare_assets" })} {...handlers()} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /Logo concepts/ }));
    expect(screen.getByRole("button", { name: /Prepare assets/ })).toBeDisabled();
  });

  it("shows progress while assets are made, then the assets and Mark as sent", () => {
    const progress = {
      status: "running",
      reason: "",
      steps: [
        { step: "branding", state: "done" as const },
        { step: "quality_check", state: "running" as const },
        { step: "proposal", state: "waiting" as const },
      ],
    };
    const { rerender } = render(<ManualSteps item={item({ step: "preparing_assets", assets_progress: progress })} {...handlers()} />);
    expect(screen.getByText("Preparing assets")).toBeInTheDocument();
    expect(screen.getByText("Logo concepts").closest("li")).toHaveAttribute("data-state", "done");
    expect(screen.getByText("Final message").closest("li")).toHaveAttribute("data-state", "waiting");

    const h = handlers();
    rerender(
      <ManualSteps
        item={item({
          step: "ready_to_send",
          assets_progress: { ...progress, status: "done" },
          attachments: [{ kind: "logo", filename: "logo-concept-1.png", url: "/api/files/a.png" }],
        })}
        {...h}
      />,
    );
    expect(screen.getByText("Assets ready")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "logo-concept-1.png" })).toHaveAttribute("href", "/api/files/a.png");
    fireEvent.click(screen.getByRole("button", { name: /Mark as sent/ }));
    expect(h.onMarkSent).toHaveBeenCalledOnce();
  });
});
