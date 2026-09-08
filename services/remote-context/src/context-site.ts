import { bearerToken, constantTimeEqual } from "@personal-agent/remote-runtime";

import { contextOperations, executeContextOperation } from "./context-api";
import { asContextError, ContextError } from "./errors";
import type { Env, Principal } from "./types";

const JSON_BODY_LIMIT = 16 * 1024 * 1024;
const SITE_USER_HEADER = "X-Personal-Agent-Site-User-Id";

function json(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return Response.json(body, {
    status,
    headers: { ...headers, "Cache-Control": "private, no-store" },
  });
}

function authenticateSite(request: Request, env: Env): Principal {
  const { CONTEXT_SITE_TOKEN: token, CONTEXT_SITE_USER_ID: userId,
    CONTEXT_SITE_OWNER_ID: ownerId } = env;
  if (![token, userId, ownerId].every(value =>
    typeof value === "string" && value.length > 0 && value.trim() === value)) {
    throw new ContextError(
      "site_unavailable", "The Context Site connection is not configured", 503,
    );
  }

  // Both values come from a server-to-server request. Never accept an owner ID
  // from a browser, a forwarded header, or the operation's JSON body.
  const tokenMatches = constantTimeEqual(bearerToken(request) ?? "", token!);
  const userMatches = constantTimeEqual(request.headers.get(SITE_USER_HEADER) ?? "", userId!);
  if (!tokenMatches || !userMatches) {
    throw new ContextError(
      "invalid_site_credential", "An authorized Context Site connection is required", 401,
    );
  }
  return {
    ownerId: ownerId!,
    scopes: new Set(["sense.read", "sense.write", "corpus.read", "corpus.write"]),
    clientId: "context-site",
    auth: "site",
  };
}

async function readJson(request: Request): Promise<unknown> {
  const contentType = request.headers.get("Content-Type")?.split(";", 1)[0]?.trim().toLowerCase();
  if (contentType !== "application/json") {
    throw new ContextError("unsupported_media_type", "A JSON request body is required", 415);
  }
  const declaredLength = request.headers.get("Content-Length");
  if (declaredLength !== null && Number(declaredLength) > JSON_BODY_LIMIT) {
    throw new ContextError("request_too_large", "Request body is too large", 413);
  }
  if (!request.body) throw new ContextError("invalid_json", "Request body must be JSON");

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      length += part.value.byteLength;
      if (length > JSON_BODY_LIMIT) {
        await reader.cancel().catch(() => {});
        throw new ContextError("request_too_large", "Request body is too large", 413);
      }
      chunks.push(part.value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw new ContextError("invalid_json", "Request body must be valid UTF-8 JSON");
  }
}

export async function handleContextSite(request: Request, env: Env): Promise<Response | null> {
  const path = new URL(request.url).pathname;
  if (!path.startsWith("/site/")) return null;
  try {
    const route = /^\/site\/v1\/([a-z][a-z0-9_]{0,95})$/.exec(path);
    if (!route) throw new ContextError("not_found", "Context Site route was not found", 404);
    if (request.method !== "POST") {
      return json({ ok: false, error: {
        code: "method_not_allowed", message: "Use POST for this Context Site operation",
      } }, 405, { Allow: "POST" });
    }
    const principal = authenticateSite(request, env);
    const operation = route[1]!;
    if (!Object.hasOwn(contextOperations(env, principal), operation)) {
      throw new ContextError("operation_not_found", "Context operation does not exist", 404);
    }
    const result = await executeContextOperation(env, principal, operation, await readJson(request));
    return json({ ok: true, result });
  } catch (error) {
    const normalized = asContextError(error);
    return json({ ok: false, error: {
      code: normalized.code,
      message: normalized.message,
      ...(Object.keys(normalized.details).length ? { details: normalized.details } : {}),
    } }, normalized.status);
  }
}
