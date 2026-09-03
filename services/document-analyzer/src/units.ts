import { AnalyzerError } from "./errors";
import type {
  Coverage,
  ExtractedUnit,
  ExtractionIssue,
} from "./types";

const MAX_UNITS = 50_000;
const MAX_UNIT_CHARS = 50_000;
const MAX_TOTAL_CHARS = 20_000_000;

export const completeCoverage = (): Coverage => ({
  text_content: "complete",
  structure: "complete",
  visual_content: "not_applicable",
  reading_order: "complete",
});

export function issue(
  code: string,
  message: string,
  impact: ExtractionIssue["impact"],
  coverage_dimensions: ExtractionIssue["coverage_dimensions"],
  severity: ExtractionIssue["severity"] = "warning",
  details?: Record<string, unknown>,
): ExtractionIssue {
  return {
    code,
    message,
    severity,
    impact,
    coverage_dimensions,
    ...(details && Object.keys(details).length ? { details } : {}),
  };
}

export function normalizeText(value: string): string {
  return value
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[ \t]+$/g, ""))
    .join("\n")
    .trim();
}

export class UnitCollector {
  readonly units: ExtractedUnit[] = [];
  private totalChars = 0;

  add(
    unitType: string,
    structurePath: Record<string, unknown>,
    rawContent: string,
    unitIssues: ExtractionIssue[] = [],
    preserveEmpty = false,
  ): void {
    const content = normalizeText(rawContent);
    if (!content && !preserveEmpty) return;
    const chunks = content.length
      ? Array.from(
          { length: Math.ceil(content.length / MAX_UNIT_CHARS) },
          (_, index) => content.slice(index * MAX_UNIT_CHARS, (index + 1) * MAX_UNIT_CHARS),
        )
      : [""];
    for (const [index, chunk] of chunks.entries()) {
      if (this.units.length >= MAX_UNITS || this.totalChars + chunk.length > MAX_TOTAL_CHARS) {
        throw new AnalyzerError(
          "analysis_output_too_large",
          "the extracted document exceeds the remote result budget",
          413,
        );
      }
      this.totalChars += chunk.length;
      this.units.push({
        unit_type: unitType,
        structure_path:
          chunks.length === 1
            ? structurePath
            : { ...structurePath, chunk: index + 1 },
        content: chunk,
        derivation_method: "native_text",
        geometry: {},
        confidence: null,
        quality_flags: [],
        issues:
          chunks.length === 1
            ? unitIssues
            : [
                ...unitIssues,
                issue(
                  "unit_split",
                  "Long source unit was split into bounded chunks.",
                  "observation",
                  [],
                  "info",
                ),
              ],
      });
    }
  }
}

export function noTextIssue(): ExtractionIssue {
  return issue(
    "no_extractable_text",
    "The remote analyzer found no extractable stored text.",
    "content_gap",
    ["text_content"],
  );
}

