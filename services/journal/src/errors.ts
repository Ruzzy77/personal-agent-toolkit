export class JournalError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, string | number | boolean | null>;

  constructor(
    code: string,
    message: string,
    status = 400,
    details: Record<string, string | number | boolean | null> = {},
  ) {
    super(message);
    this.name = "JournalError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

export function asJournalError(error: unknown): JournalError {
  if (error instanceof JournalError) return error;
  return new JournalError(
    "unexpected_error",
    "unexpected Journal operation failure",
    500,
  );
}
