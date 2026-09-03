export const JOB_SCHEMA = "document-files.analysis-job.v1";
export const RESULT_SCHEMA = "document-files.analysis-result.v1";
export const EXTRACTION_SCHEMA = "document-files.extraction-envelope.v2";
export const PROTOCOL_VERSION = "document-files.extraction-result.v2";

export const MEDIA_TYPES = {
  md: "text/markdown",
  markdown: "text/markdown",
  txt: "text/plain",
  html: "text/html",
  htm: "text/html",
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  hwpx: "application/vnd.hancom.hwpx",
  hwp: "application/x-hwp",
} as const;

export type FormatId = keyof typeof MEDIA_TYPES;
export type CoverageValue = "complete" | "partial" | "unverified" | "not_applicable";

export interface AnalysisInput {
  format_id: FormatId;
  media_type: (typeof MEDIA_TYPES)[FormatId];
  byte_size: number;
  sha256: string;
}

export interface AnalysisJob {
  schema_version: typeof JOB_SCHEMA;
  job_id: string;
  operation: "extract";
  input: AnalysisInput;
  budgets: {
    max_input_bytes: number;
    completion_seconds: number;
  };
}

export interface ExtractionIssue {
  code: string;
  message: string;
  severity: "info" | "warning" | "error";
  impact:
    | "observation"
    | "operational_failure"
    | "processing_limit"
    | "content_gap"
    | "structure_gap"
    | "visual_uninterpreted"
    | "reading_order_unverified"
    | "unsupported_feature";
  coverage_dimensions: Array<
    "text_content" | "structure" | "visual_content" | "reading_order"
  >;
  details?: Record<string, unknown>;
}

export interface ExtractedUnit {
  unit_type: string;
  structure_path: Record<string, unknown>;
  content: string;
  derivation_method: "native_text" | "ocr";
  geometry: Record<string, unknown>;
  confidence: number | null;
  quality_flags: string[];
  issues: ExtractionIssue[];
}

export interface Coverage {
  text_content: CoverageValue;
  structure: CoverageValue;
  visual_content: CoverageValue;
  reading_order: CoverageValue;
}

export interface AdapterDescriptor {
  adapter_id: string;
  adapter_version: string;
  config_hash: string;
  capabilities: {
    format_ids: FormatId[];
    structural_unit_types: string[];
    execution_mode: "in_process";
    preserves_reading_order: boolean;
    supports_geometry: false;
    supports_confidence: false;
    supports_ocr: false;
    may_emit_partial: true;
    protocol_version: typeof PROTOCOL_VERSION;
  };
}

export interface ExtractionDraft {
  units: ExtractedUnit[];
  issues: ExtractionIssue[];
  coverage: Coverage;
  structuralUnitTypes: string[];
  preservesReadingOrder: boolean;
  adapterFamily: string;
}

export interface AnalysisResult {
  schema_version: typeof RESULT_SCHEMA;
  job_id: string;
  input: AnalysisInput;
  analyzer: AdapterDescriptor;
  extraction: {
    schema_version: typeof EXTRACTION_SCHEMA;
    descriptor: AdapterDescriptor;
    completeness: "complete" | "partial";
    coverage: Coverage;
    units: ExtractedUnit[];
    issues: ExtractionIssue[];
    manifest_hash: string;
  };
}

