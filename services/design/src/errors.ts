export class DesignError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status = 400,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "DesignError";
  }
}

export function asDesignError(error: unknown): DesignError {
  if (error instanceof DesignError) return error;
  if (error instanceof Error && error.name === "ZodError") {
    return new DesignError("invalid_request", "the Design request is invalid");
  }
  console.error(
    "Design request failed",
    error instanceof Error ? error.name : "UnknownError",
  );
  return new DesignError(
    "internal_error",
    "the Design request could not be completed",
    500,
  );
}
