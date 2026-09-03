import { createAnalysisResult, parseAnalysisJob, readAndVerifyBytes } from "./contract";
import { asAnalyzerError, AnalyzerError } from "./errors";
import { extractDocument } from "./extractors";

const VERSION = "1.3.2";

function json(value: unknown, status = 200): Response {
  return Response.json(value, {
    status,
    headers: {
      "Cache-Control": "private, no-store",
      "Content-Type": "application/json; charset=utf-8",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export async function handleAnalysis(request: Request): Promise<Response> {
  if (request.method !== "POST") {
    return json(
      { error: { code: "method_not_allowed", message: "POST is required" } },
      405,
    );
  }
  if (
    !request.headers.get("X-Owner-Id")
    || !request.headers.get("X-Device-Id")
  ) {
    throw new AnalyzerError(
      "unauthorized_analyzer_request",
      "the analyzer requires an authenticated Sync request",
      401,
    );
  }
  const job = parseAnalysisJob(request);
  const bytes = await readAndVerifyBytes(request, job);
  const extraction = await extractDocument(job.input.format_id, bytes);
  return json(await createAnalysisResult(job, extraction));
}

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    try {
      if (request.method === "GET" && url.pathname === "/health") {
        return json({ ok: true, service: "personal-agent-document-analyzer", version: VERSION });
      }
      if (url.pathname === "/v1/analyze") return await handleAnalysis(request);
      return json({ error: { code: "not_found", message: "route not found" } }, 404);
    } catch (error) {
      const normalized = asAnalyzerError(error);
      return json(
        { error: { code: normalized.code, message: normalized.message } },
        normalized.status,
      );
    }
  },
} satisfies ExportedHandler;
