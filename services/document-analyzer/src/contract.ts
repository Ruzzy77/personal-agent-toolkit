import { AnalyzerError } from "./errors";
import {
  EXTRACTION_SCHEMA,
  JOB_SCHEMA,
  MEDIA_TYPES,
  PROTOCOL_VERSION,
  RESULT_SCHEMA,
  type AdapterDescriptor,
  type AnalysisJob,
  type AnalysisResult,
  type ExtractionDraft,
  type FormatId,
} from "./types";

export const MAX_INPUT_BYTES = 32 * 1024 * 1024;
const JOB_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const encoder = new TextEncoder();

function requiredHeader(request: Request, name: string): string {
  const value = request.headers.get(name);
  if (!value) {
    throw new AnalyzerError(
      "invalid_analysis_request",
      "analysis identity headers are required",
    );
  }
  return value;
}

export function parseAnalysisJob(request: Request): AnalysisJob {
  const jobId = requiredHeader(request, "X-Analysis-Job");
  const digest = requiredHeader(request, "X-Input-Sha256");
  const format = requiredHeader(request, "X-Format-Id");
  const sizeText = requiredHeader(request, "X-Source-Size");

  if (!JOB_ID.test(jobId) || !SHA256.test(digest)) {
    throw new AnalyzerError(
      "invalid_analysis_request",
      "analysis identity is invalid",
    );
  }
  if (!(format in MEDIA_TYPES)) {
    throw new AnalyzerError(
      "unsupported_format",
      "the remote analyzer does not support this document format",
      415,
    );
  }
  const size = Number(sizeText);
  if (!Number.isSafeInteger(size) || size < 0 || size > MAX_INPUT_BYTES) {
    throw new AnalyzerError(
      "analysis_input_too_large",
      `remote analysis accepts at most ${MAX_INPUT_BYTES} bytes`,
      413,
    );
  }

  const formatId = format as FormatId;
  return {
    schema_version: JOB_SCHEMA,
    job_id: jobId,
    operation: "extract",
    input: {
      format_id: formatId,
      media_type: MEDIA_TYPES[formatId],
      byte_size: size,
      sha256: digest,
    },
    budgets: {
      max_input_bytes: Math.max(1, size),
      completion_seconds: 580,
    },
  };
}

export async function readAndVerifyBytes(
  request: Request,
  job: AnalysisJob,
): Promise<Uint8Array> {
  const bytes = new Uint8Array(await request.arrayBuffer());
  if (bytes.byteLength !== job.input.byte_size) {
    throw new AnalyzerError(
      "analysis_identity_mismatch",
      "analysis bytes do not match the declared size",
    );
  }
  const digest = await sha256(bytes);
  if (digest !== job.input.sha256) {
    throw new AnalyzerError(
      "analysis_identity_mismatch",
      "analysis bytes do not match the declared digest",
    );
  }
  return bytes;
}

export async function createAnalysisResult(
  job: AnalysisJob,
  draft: ExtractionDraft,
): Promise<AnalysisResult> {
  const descriptor: AdapterDescriptor = {
    adapter_id: `document-files.remote.${draft.adapterFamily}`,
    adapter_version: "edge-v1",
    config_hash: await sha256(
      encoder.encode(
        canonicalJson({
          adapter: draft.adapterFamily,
          format: job.input.format_id,
          protocol: PROTOCOL_VERSION,
        }),
      ),
    ),
    capabilities: {
      format_ids: [job.input.format_id],
      structural_unit_types: [...new Set(draft.structuralUnitTypes)].sort(),
      execution_mode: "in_process",
      preserves_reading_order: draft.preservesReadingOrder,
      supports_geometry: false,
      supports_confidence: false,
      supports_ocr: false,
      may_emit_partial: true,
      protocol_version: PROTOCOL_VERSION,
    },
  };
  const completeness = Object.values(draft.coverage).includes("partial")
    ? "partial"
    : "complete";
  const payload = {
    schema_version: EXTRACTION_SCHEMA,
    descriptor,
    completeness,
    coverage: draft.coverage,
    units: draft.units,
    issues: draft.issues,
  } as const;
  return {
    schema_version: RESULT_SCHEMA,
    job_id: job.job_id,
    input: job.input,
    analyzer: descriptor,
    extraction: {
      ...payload,
      manifest_hash: await sha256(encoder.encode(canonicalJson(payload))),
    },
  };
}

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    const encoded = JSON.stringify(value);
    if (encoded === undefined) throw new TypeError("value is not JSON-compatible");
    return encoded;
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(",")}}`;
}

export async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    Uint8Array.from(bytes).buffer,
  );
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}
