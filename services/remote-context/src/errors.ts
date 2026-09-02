export class ContextError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status = 400,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ContextError";
  }
}

export function asContextError(error: unknown): ContextError {
  if (error instanceof ContextError) return error;
  if (error instanceof ZodError) {
    return new ContextError("invalid_request", "request fields are invalid", 400);
  }
  return new ContextError(
    "internal_error",
    "the personal context service encountered an unexpected error",
    500,
  );
}
import { ZodError } from "zod/v4";

