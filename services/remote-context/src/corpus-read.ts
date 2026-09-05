import { canonicalJson } from "./canonical";
import { ContextError } from "./errors";

export const SOURCE_READ_MAX_UNITS = 500;
export const SOURCE_READ_MAX_BYTES = 2 * 1024 * 1024;
export const SOURCE_READ_MAX_START = 2 * 1024 * 1024;

export function sourceReadBudgetExceeded(
  details: Record<string, unknown>,
): never {
  throw new ContextError(
    "budget_exceeded",
    "Source read exceeds its budget. Reduce neighbor_span or disable structure context; read individual units with source_view=text and a smaller max_chars.",
    413,
    details,
  );
}

export function serializedBytes(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

export function assertSourceReadBudget(result: Record<string, unknown>): void {
  const bytes = serializedBytes(result);
  if (bytes > SOURCE_READ_MAX_BYTES) {
    sourceReadBudgetExceeded({
      serialized_bytes: bytes,
      maximum_serialized_bytes: SOURCE_READ_MAX_BYTES,
    });
  }
}

type Structure = Record<string, unknown>;

function same(left: unknown, right: unknown): boolean {
  return canonicalJson(left ?? null) === canonicalJson(right ?? null);
}

function linked(left: unknown, right: unknown): boolean {
  return left != null && right != null && same(left, right);
}

// HWP includes the current cell in container_path; Office/HWPX retain only
// ancestors. Compare ancestor identities, not row/column or parser scaffolding.
function ancestors(path: Structure, kind: string): unknown[] {
  const containers = Array.isArray(path.container_path)
    ? path.container_path
    : [];
  const parents: unknown[] = [];
  for (const container of containers) {
    if (!container || typeof container !== "object" || Array.isArray(container))
      continue;
    const item = container as Structure;
    if (linked(item[kind], path[kind])) break;
    const identity = Object.fromEntries(
      ["table", "cell", "note", "object"]
        .filter((key) => item[key] != null)
        .map((key) => [key, item[key]]),
    );
    if (Object.keys(identity).length) parents.push(identity);
  }
  return parents;
}

export function isRelatedStructure(
  seed: Structure,
  candidate: Structure,
  candidateType: string,
): boolean {
  if (
    !["section", "section_stream", "part", "page"].every((key) =>
      same(seed[key], candidate[key]),
    )
  )
    return false;

  if (
    linked(seed.table, candidate.table) &&
    same(ancestors(seed, "table"), ancestors(candidate, "table")) &&
    same(seed.note, candidate.note) &&
    same(seed.object, candidate.object)
  ) {
    if (
      candidateType === "table" ||
      candidate.container_kind === "caption" ||
      candidate.is_header === true ||
      candidate.is_header === 1
    )
      return true;
    if (seed.row == null) return true;
    if (typeof seed.row === "number" && typeof candidate.row === "number") {
      const span = (value: unknown) =>
        typeof value === "number" && value > 0 ? value : 1;
      return (
        seed.row < candidate.row + span(candidate.row_span) &&
        candidate.row < seed.row + span(seed.row_span)
      );
    }
    return same(seed.row, candidate.row);
  }

  if (
    linked(seed.note, candidate.note) &&
    same(seed.table, candidate.table) &&
    same(seed.cell, candidate.cell) &&
    same(ancestors(seed, "note"), ancestors(candidate, "note"))
  )
    return true;
  if (
    seed.table == null &&
    candidate.table == null &&
    same(seed.note, candidate.note) &&
    linked(seed.object, candidate.object) &&
    same(ancestors(seed, "object"), ancestors(candidate, "object"))
  )
    return true;

  // Only explicit ownership links connect a paragraph and its note/object.
  const owns = (owner: Structure, child: Structure) => {
    if (
      !linked(owner.paragraph_record, child.owner_paragraph_record) &&
      !linked(owner.paragraph_element, child.owner_paragraph)
    )
      return false;
    if (owner.table == null) return true;
    if (linked(owner.table, child.table)) {
      return (
        same(owner.cell, child.cell) &&
        same(ancestors(owner, "table"), ancestors(child, "table"))
      );
    }
    return ancestors(child, "table").some((item) => {
      const parent = item as Structure;
      return linked(owner.table, parent.table) && same(owner.cell, parent.cell);
    });
  };
  return owns(seed, candidate) || owns(candidate, seed);
}
