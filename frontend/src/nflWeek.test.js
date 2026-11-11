import { describe, expect, it } from "vitest";

import { defaultSeason, defaultWeek } from "./nflWeek.js";

describe("defaultWeek", () => {
  it("counts Tuesday-to-Monday weeks from the day after Labor Day", () => {
    // Labor Day 2026 is Monday September 7.
    expect(defaultWeek(new Date(2026, 8, 8))).toBe(1);
    expect(defaultWeek(new Date(2026, 8, 14))).toBe(1);
    expect(defaultWeek(new Date(2026, 8, 15))).toBe(2);
    expect(defaultWeek(new Date(2026, 10, 6))).toBe(9);
  });

  it("is week 1 before the season starts", () => {
    expect(defaultWeek(new Date(2026, 6, 1))).toBe(1);
  });

  it("puts January playoffs in the previous season", () => {
    expect(defaultSeason(new Date(2027, 0, 20))).toBe(2026);
    expect(defaultWeek(new Date(2027, 0, 20))).toBe(20);
  });
});
