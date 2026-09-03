import { ZodError } from "zod/v4";

const CONTENT_ERROR_CODES = new Set([
  "article_markup_not_allowed",
  "article_not_found",
  "article_style_not_allowed",
  "embedded_content_not_allowed",
  "event_handler_not_allowed",
  "incomplete_source_html",
  "invalid_article_html",
  "invalid_canonical_path",
  "invalid_issue_id",
  "invalid_issue_identity",
  "invalid_lead_text",
  "invalid_references",
  "invalid_source_size",
  "invalid_title",
  "javascript_url_not_allowed",
  "lead_not_found",
  "script_not_allowed",
]);

export class LibraryError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status = 400,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "LibraryError";
  }
}

export function asLibraryError(error: unknown): LibraryError {
  if (error instanceof LibraryError) return error;
  if (error instanceof ZodError) {
    return new LibraryError("invalid_request", "request fields are invalid");
  }
  if (error instanceof Error && CONTENT_ERROR_CODES.has(error.message)) {
    return new LibraryError(error.message, "Library content is invalid");
  }
  return new LibraryError(
    "internal_error",
    "the Library service encountered an unexpected error",
    500,
  );
}
