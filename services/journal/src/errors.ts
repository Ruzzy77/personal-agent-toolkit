import { OperationError } from "@personal-agent/remote-runtime";
export class JournalError extends OperationError {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(
    code: string,
    message: string,
    status = 400,
    details: Record<string, unknown> = {},
  ) {
    super(code, message, status, details);
    this.name = "JournalError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

export function asJournalError(error: unknown): JournalError {
  if (error instanceof JournalError) return error;
  if (error instanceof OperationError)
    return new JournalError(
      error.code,
      error.message,
      error.status,
      error.details,
    );
  return new JournalError(
    "unexpected_error",
    "unexpected Journal operation failure",
    500,
  );
}
