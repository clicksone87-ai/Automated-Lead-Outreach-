import { describe, expect, it } from "vitest";
import { duration } from "./lane-speed";

describe("duration", () => {
  it("formats seconds, minutes and hours", () => {
    expect(duration(null)).toBe("—");
    expect(duration(42.4)).toBe("42 s");
    expect(duration(180)).toBe("3 min");
    expect(duration(185)).toBe("3 min 05 s");
    expect(duration(4320)).toBe("1 h 12 min");
  });
});
