import { describe, expect, it } from "vitest";
import { countryName, flag, money, pct, titleCase, usd } from "./format";
import { leadStatus } from "./status";

describe("format", () => {
  it("formats currency and percentages", () => {
    expect(usd(3.5)).toBe("$3.50");
    expect(usd(120, 0)).toBe("$120");
    expect(money(1500, "USD")).toBe("$1,500");
    expect(money(1200, "GBP")).toBe("£1,200");
    expect(pct(0.0312)).toBe("3.1%");
  });

  it("renders countries and flags", () => {
    expect(countryName("GB")).toBe("United Kingdom");
    expect(countryName(null)).toBe("Unknown");
    expect(flag("US")).toBe("🇺🇸");
    expect(flag(null)).toBe("🏳️");
  });

  it("title-cases identifiers", () => {
    expect(titleCase("no_contact_route")).toBe("No Contact Route");
  });

  it("maps lead statuses to tones with a safe fallback", () => {
    expect(leadStatus("qa_passed")).toEqual({ label: "QA passed", tone: "success" });
    expect(leadStatus("brand_new_status").tone).toBe("neutral");
  });
});
