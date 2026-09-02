import { describe, expect, it } from "vitest";

import {
  currentWeekId,
  kstDate,
  periodRange,
  validateWeekId,
  weekIdForDate,
} from "../src/time";

describe("KST calendar boundaries", () => {
  it("moves into the next KST day before UTC midnight", () => {
    const instant = new Date("2026-09-01T15:30:00.000Z");
    expect(kstDate(instant)).toBe("2026-09-02");
    expect(currentWeekId(instant)).toBe("2026-08-31");
  });

  it("uses Monday through Sunday for every selected date", () => {
    expect(weekIdForDate("2026-09-06")).toBe("2026-08-31");
    expect(weekIdForDate("2026-09-07")).toBe("2026-09-07");
    expect(() => validateWeekId("2026-09-06")).toThrow(/Monday/);
  });

  it("returns leap-month, quarter, and year boundaries", () => {
    expect(periodRange("month", "2028-02-12")).toEqual({
      startsOn: "2028-02-01",
      endsOn: "2028-02-29",
    });
    expect(periodRange("quarter", "2026-09-02")).toEqual({
      startsOn: "2026-07-01",
      endsOn: "2026-09-30",
    });
    expect(periodRange("year", "2026-09-02")).toEqual({
      startsOn: "2026-01-01",
      endsOn: "2026-12-31",
    });
  });
});
