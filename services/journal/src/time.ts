import type { PeriodKind } from "./types";
import { JournalError } from "./errors";

const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function shifted(date: Date): Date {
  return new Date(date.getTime() + KST_OFFSET_MS);
}

function isoDateFromUtc(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function parseDate(value: string): Date {
  if (!DATE_PATTERN.test(value)) {
    throw new JournalError("invalid_date", "date must use YYYY-MM-DD");
  }
  const date = new Date(`${value}T00:00:00.000Z`);
  if (Number.isNaN(date.getTime()) || isoDateFromUtc(date) !== value) {
    throw new JournalError("invalid_date", "date is not a calendar date");
  }
  return date;
}

export function addDays(value: string, days: number): string {
  const date = parseDate(value);
  return isoDateFromUtc(new Date(date.getTime() + days * DAY_MS));
}

export function kstDate(now = new Date()): string {
  return isoDateFromUtc(shifted(now));
}

export function weekIdForDate(value: string): string {
  const date = parseDate(value);
  const day = date.getUTCDay();
  const daysSinceMonday = (day + 6) % 7;
  return isoDateFromUtc(
    new Date(date.getTime() - daysSinceMonday * DAY_MS),
  );
}

export function currentWeekId(now = new Date()): string {
  return weekIdForDate(kstDate(now));
}

export function validateWeekId(value: string): string {
  const normalized = weekIdForDate(value);
  if (normalized !== value) {
    throw new JournalError(
      "invalid_week_id",
      "week id must be the Monday date in YYYY-MM-DD",
    );
  }
  return value;
}

export function kstEventDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    throw new JournalError("invalid_timestamp", "timestamp must be ISO 8601");
  }
  return kstDate(date);
}

export function normalizeTimestamp(value: string | null, now: Date): string {
  if (value === null) return now.toISOString();
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    throw new JournalError("invalid_timestamp", "timestamp must be ISO 8601");
  }
  return parsed.toISOString();
}

export function periodRange(
  kind: PeriodKind,
  anchor: string,
): { startsOn: string; endsOn: string } {
  const date = parseDate(anchor);
  const year = date.getUTCFullYear();
  const month = date.getUTCMonth();

  if (kind === "day") return { startsOn: anchor, endsOn: anchor };
  if (kind === "week") {
    const startsOn = weekIdForDate(anchor);
    return { startsOn, endsOn: addDays(startsOn, 6) };
  }
  if (kind === "month") {
    const startsOn = `${year}-${String(month + 1).padStart(2, "0")}-01`;
    const end = new Date(Date.UTC(year, month + 1, 0));
    return { startsOn, endsOn: isoDateFromUtc(end) };
  }
  if (kind === "quarter") {
    const quarterMonth = Math.floor(month / 3) * 3;
    const startsOn = `${year}-${String(quarterMonth + 1).padStart(2, "0")}-01`;
    const end = new Date(Date.UTC(year, quarterMonth + 3, 0));
    return { startsOn, endsOn: isoDateFromUtc(end) };
  }
  return { startsOn: `${year}-01-01`, endsOn: `${year}-12-31` };
}
