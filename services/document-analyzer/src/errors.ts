export class AnalyzerError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status = 400,
  ) {
    super(message);
  }
}

export function asAnalyzerError(error: unknown): AnalyzerError {
  if (error instanceof AnalyzerError) return error;
  return new AnalyzerError(
    "analysis_failed",
    "the document could not be analyzed by the remote runtime",
    422,
  );
}

