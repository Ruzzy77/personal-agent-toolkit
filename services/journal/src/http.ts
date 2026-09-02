import { ZodError, type ZodType } from "zod/v4";

import { authenticate } from "./auth";
import { asJournalError, JournalError } from "./errors";
import {
  closeWeekRequestSchema,
  correctionRequestSchema,
  ingestRequestSchema,
  periodKindSchema,
  promotionRequestSchema,
  resolutionRequestSchema,
} from "./schemas";
import { JournalService } from "./service";
import type { Env } from "./types";

const MAX_BODY_BYTES = 1_000_000;

function corsHeaders(request: Request, env: Env): HeadersInit {
  const origin = request.headers.get("Origin");
  if (!origin || !env.ALLOWED_SITE_ORIGIN || origin !== env.ALLOWED_SITE_ORIGIN) {
    return {};
  }
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
    Vary: "Origin",
  };
}

function jsonResponse(
  request: Request,
  env: Env,
  body: unknown,
  status = 200,
): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      ...corsHeaders(request, env),
    },
  });
}

async function readJson<T>(request: Request, schema: ZodType<T>): Promise<T> {
  const contentLength = Number(request.headers.get("Content-Length") ?? "0");
  if (contentLength > MAX_BODY_BYTES) {
    throw new JournalError("request_too_large", "request body is too large", 413);
  }
  let value: unknown;
  try {
    value = await request.json();
  } catch {
    throw new JournalError("invalid_json", "request body must be JSON");
  }
  try {
    return schema.parse(value);
  } catch (error) {
    if (error instanceof ZodError) {
      throw new JournalError("invalid_request", "request fields are invalid");
    }
    throw error;
  }
}

function rejectUnexpectedOrigin(request: Request, env: Env): void {
  const origin = request.headers.get("Origin");
  if (origin && origin !== env.ALLOWED_SITE_ORIGIN) {
    throw new JournalError(
      "origin_not_allowed",
      "request origin is not allowed",
      403,
    );
  }
}

function unauthorizedHeaders(env: Env): HeadersInit {
  const resource = new URL(env.JOURNAL_RESOURCE);
  const metadata = `${resource.origin}/.well-known/oauth-protected-resource`;
  return {
    "WWW-Authenticate": `Bearer resource_metadata="${metadata}"`,
  };
}

export async function handleHttp(request: Request, env: Env): Promise<Response> {
  try {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      rejectUnexpectedOrigin(request, env);
      return new Response(null, { status: 204, headers: corsHeaders(request, env) });
    }
    rejectUnexpectedOrigin(request, env);

    if (request.method === "GET" && url.pathname === "/health") {
      return jsonResponse(request, env, {
        ok: true,
        service: "personal-agent-journal",
        version: "0.1.0",
      });
    }
    if (
      request.method === "GET" &&
      url.pathname === "/.well-known/oauth-protected-resource"
    ) {
      return jsonResponse(request, env, {
        resource: env.JOURNAL_RESOURCE,
        authorization_servers: [env.AUTH_ISSUER],
        scopes_supported: [
          "journal.read",
          "journal.write",
          "journal.ingest",
          "journal.close",
        ],
        bearer_methods_supported: ["header"],
        resource_documentation: `${new URL(env.JOURNAL_RESOURCE).origin}/`,
      });
    }

    const service = new JournalService(env.DB);
    if (request.method === "GET" && url.pathname === "/api/v1/board") {
      await authenticate(request, env, ["journal.read"]);
      const week = url.searchParams.get("week");
      const includeResolved =
        url.searchParams.get("include_resolved") === "true";
      const result = await service.getBoard(week, includeResolved);
      return jsonResponse(request, env, { ok: true, result });
    }

    if (request.method === "POST" && url.pathname === "/api/v1/items:ingest") {
      const principal = await authenticate(request, env, [
        "journal.ingest",
        "journal.write",
      ]);
      const input = await readJson(request, ingestRequestSchema);
      const result = await service.ingestItems(input.items, principal);
      return jsonResponse(request, env, { ok: true, result }, 200);
    }

    const resolutionMatch =
      /^\/api\/v1\/items\/([0-9a-f-]+)\/resolution$/.exec(url.pathname);
    if (request.method === "PATCH" && resolutionMatch) {
      const principal = await authenticate(request, env, ["journal.write"]);
      const input = await readJson(request, resolutionRequestSchema);
      const itemId = resolutionMatch[1];
      if (!itemId) {
        throw new JournalError("item_not_found", "item was not found", 404);
      }
      const result = await service.setResolution(itemId, input, principal);
      return jsonResponse(request, env, { ok: true, result });
    }

    const closeMatch = /^\/api\/v1\/weeks\/(\d{4}-\d{2}-\d{2}):close$/.exec(
      url.pathname,
    );
    if (request.method === "POST" && closeMatch) {
      const principal = await authenticate(request, env, ["journal.close"]);
      const input = await readJson(request, closeWeekRequestSchema);
      const weekId = closeMatch[1];
      if (!weekId) {
        throw new JournalError("week_not_found", "week was not found", 404);
      }
      const result = await service.closeWeek(
        weekId,
        input.idempotencyKey,
        input.occurredAt,
        principal,
      );
      return jsonResponse(request, env, { ok: true, result });
    }

    const correctionMatch =
      /^\/api\/v1\/weeks\/(\d{4}-\d{2}-\d{2})\/corrections$/.exec(
        url.pathname,
      );
    if (request.method === "POST" && correctionMatch) {
      const principal = await authenticate(request, env, ["journal.write"]);
      const input = await readJson(request, correctionRequestSchema);
      const weekId = correctionMatch[1];
      if (!weekId) {
        throw new JournalError("week_not_found", "week was not found", 404);
      }
      const result = await service.addCorrection(weekId, input, principal);
      return jsonResponse(request, env, { ok: true, result });
    }

    if (request.method === "GET" && url.pathname === "/api/v1/period") {
      await authenticate(request, env, ["journal.read"]);
      const kindResult = periodKindSchema.safeParse(
        url.searchParams.get("kind") ?? "week",
      );
      if (!kindResult.success) {
        throw new JournalError("invalid_request", "period kind is invalid");
      }
      const result = await service.getPeriod(
        kindResult.data,
        url.searchParams.get("anchor"),
      );
      return jsonResponse(request, env, { ok: true, result });
    }

    if (
      request.method === "POST" &&
      url.pathname === "/api/v1/corpus-promotions"
    ) {
      const principal = await authenticate(request, env, [
        "journal.ingest",
        "journal.write",
      ]);
      const input = await readJson(request, promotionRequestSchema);
      const result = await service.recordPromotion(input, principal);
      return jsonResponse(request, env, { ok: true, result });
    }

    return jsonResponse(
      request,
      env,
      {
        ok: false,
        error: { code: "not_found", message: "route was not found" },
      },
      404,
    );
  } catch (error) {
    const journalError = asJournalError(error);
    const headers =
      journalError.status === 401 ? unauthorizedHeaders(env) : undefined;
    const response = jsonResponse(
      request,
      env,
      {
        ok: false,
        error: {
          code: journalError.code,
          message: journalError.message,
          ...(Object.keys(journalError.details).length > 0
            ? { details: journalError.details }
            : {}),
        },
      },
      journalError.status,
    );
    if (headers) {
      for (const [name, value] of Object.entries(headers)) {
        response.headers.set(name, value);
      }
    }
    return response;
  }
}
