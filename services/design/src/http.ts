import { bearerToken, constantTimeEqual } from "@personal-agent/remote-runtime";

import { asDesignError, DesignError } from "./errors";
import {
  createRecipeSchema,
  importRecipeSchema,
  recipeIdSchema,
  readFileSchema,
  updateRecipeSchema,
  uploadFileSchema,
} from "./schemas";
import { DesignService } from "./service";
import type { Env } from "./types";

const MAX_JSON_BYTES = 16_500_000;

function success(result: unknown, status = 200): Response {
  return Response.json(
    { ok: true, result },
    { status, headers: { "Cache-Control": "private, no-store" } },
  );
}

function failure(error: unknown): Response {
  const normalized = asDesignError(error);
  return Response.json(
    {
      ok: false,
      error: {
        code: normalized.code,
        message: normalized.message,
        details: normalized.details,
      },
    },
    {
      status: normalized.status,
      headers: { "Cache-Control": "private, no-store" },
    },
  );
}

function requireSite(request: Request, env: Env): void {
  const token = bearerToken(request);
  if (!token || !constantTimeEqual(token, env.DESIGN_SITE_TOKEN)) {
    throw new DesignError(
      "invalid_site_credential",
      "a valid Design Site credential is required",
      401,
    );
  }
}

async function readJson(request: Request): Promise<unknown> {
  const length = Number(request.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(length) && length > MAX_JSON_BYTES) {
    throw new DesignError("request_too_large", "request body is too large", 413);
  }
  try {
    return await request.json();
  } catch {
    throw new DesignError("invalid_json", "request body must be JSON");
  }
}

function decodedSegments(pathname: string): string[] {
  try {
    return pathname.split("/").filter(Boolean).map(decodeURIComponent);
  } catch {
    throw new DesignError("invalid_request", "request path is invalid");
  }
}

async function handleRecipeRoutes(
  request: Request,
  service: DesignService,
): Promise<Response | null> {
  const url = new URL(request.url);
  if (url.pathname === "/api/v1/catalog" && request.method === "GET") {
    return success(await service.catalog());
  }
  if (url.pathname === "/api/v1/recipes" && request.method === "POST") {
    const input = createRecipeSchema.parse(await readJson(request));
    return success(await service.createRecipe(input.recipe), 201);
  }
  if (url.pathname === "/api/v1/import/recipes" && request.method === "POST") {
    const input = importRecipeSchema.parse(await readJson(request));
    return success(await service.importRecipe(input), 201);
  }

  const segments = decodedSegments(url.pathname);
  if (segments[0] !== "api" || segments[1] !== "v1" || segments[2] !== "recipes") {
    return null;
  }
  const parsedId = recipeIdSchema.safeParse(segments[3]);
  if (!parsedId.success) {
    throw new DesignError("invalid_request", "recipe id is invalid");
  }
  const id = parsedId.data;

  if (segments.length === 4 && request.method === "GET") {
    const recipe = await service.readRecipe(id);
    if (!recipe) throw new DesignError("not_found", "the Design recipe was not found", 404);
    return success(recipe);
  }
  if (segments.length === 4 && request.method === "PUT") {
    const input = updateRecipeSchema.parse({
      ...(await readJson(request) as Record<string, unknown>),
      id,
    });
    return success(await service.updateRecipe(id, input.expected_revision, input.recipe));
  }

  if (segments[4] !== "files" || segments.length < 6) return null;
  const path = segments.slice(5).join("/");
  const parsedFile = readFileSchema.pick({ id: true, path: true }).parse({ id, path });
  if (request.method === "GET" || request.method === "HEAD") {
    const loaded = await service.readFile(parsedFile.id, parsedFile.path);
    if (!loaded) throw new DesignError("not_found", "the Design asset was not found", 404);
    const headers = new Headers({
      "Content-Type": loaded.record.content_type,
      "Content-Length": String(loaded.record.byte_size),
      "ETag": `"${loaded.record.sha256}"`,
      "Cache-Control": "private, max-age=300",
      "X-Content-Type-Options": "nosniff",
    });
    return new Response(request.method === "HEAD" ? null : loaded.object.body, { headers });
  }
  if (request.method === "PUT") {
    const body = await readJson(request) as Record<string, unknown>;
    const input = uploadFileSchema.parse({ ...body, id, path });
    return success(await service.uploadFile(input));
  }
  return null;
}

export async function handleHttp(request: Request, env: Env): Promise<Response> {
  try {
    requireSite(request, env);
    const response = await handleRecipeRoutes(request, new DesignService(env));
    if (response) return response;
    return new Response("Not found", { status: 404 });
  } catch (error) {
    return failure(error);
  }
}
