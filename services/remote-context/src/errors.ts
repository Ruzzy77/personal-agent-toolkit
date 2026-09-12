import { OperationError } from "@personal-agent/remote-runtime";
export class ContextError extends OperationError {}

export function asContextError(error: unknown): ContextError {
  if (error instanceof OperationError) return error;
  if (error instanceof ZodError) {
    return new ContextError(
      "invalid_request",
      "request fields are invalid",
      400,
    );
  }
  return new ContextError(
    "internal_error",
    "the personal context service encountered an unexpected error",
    500,
  );
}
import { ZodError } from "zod/v4";
