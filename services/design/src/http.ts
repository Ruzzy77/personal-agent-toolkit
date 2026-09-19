import { executeDesignOperation } from "./operations";
import {
  bearerToken,
  constantTimeEqual,
  type OperationActor,
} from "@personal-agent/remote-runtime";

import { asDesignError, DesignError } from "./errors";
import {
  listRecipesSchema,
  createRecipeSchema,
  importRecipeSchema,
  recipeIdSchema,
  readFileSchema,
  updateRecipeSchema,
  uploadFileSchema,
} from "./schemas";
import { DesignService } from "./service";
import type { Env } from "./types";

const siteActor: OperationActor = {
  ownerId: "site-owner",
  clientId: "site",
  kind: "owner" as const,
  scopes: new Set(["design.read", "design.write"]),
};
const execute = (
  service: DesignService,
  actor: OperationActor,
  name: string,
  input: unknown,
) => executeDesignOperation(service, actor, name, input);
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
    throw new DesignError(
      "request_too_large",
      "request body is too large",
      413,
    );
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
  actor: OperationActor,
): Promise<Response | null> {
  const url = new URL(request.url);
  if (url.pathname === "/api/v1/recipes" && request.method === "GET")
    return success(
      (
        await execute(
          service,
          actor,
          "design_list_recipes",
          listRecipesSchema.parse({
            ...Object.fromEntries(url.searchParams),
            limit: Number(url.searchParams.get("limit") ?? 100),
            offset: Number(url.searchParams.get("offset") ?? 0),
          }),
        )
      ).recipes,
    );
  if (url.pathname === "/api/v1/catalog" && request.method === "GET") {
    return success(await execute(service, actor, "design_catalog", {}));
  }
  if (url.pathname === "/api/v1/recipes" && request.method === "POST") {
    const input = createRecipeSchema.parse(await readJson(request));
    return success(await execute(service, actor, "design_create_recipe", input), 201);
  }
  if (url.pathname === "/api/v1/import/recipes" && request.method === "POST") {
    const input = importRecipeSchema.parse(await readJson(request));
    return success(await execute(service, actor, "design_import_recipe", input), 201);
  }

  const segments = decodedSegments(url.pathname);
  if (
    segments[0] !== "api" ||
    segments[1] !== "v1" ||
    segments[2] !== "recipes"
  ) {
    return null;
  }
  const parsedId = recipeIdSchema.safeParse(segments[3]);
  if (!parsedId.success) {
    throw new DesignError("invalid_request", "recipe id is invalid");
  }
  const id = parsedId.data;

  if (segments.length === 4 && request.method === "GET") {
    return success(await execute(service, actor, "design_read_recipe", { id }));
  }
  if (segments.length === 4 && request.method === "PUT") {
    const input = updateRecipeSchema.parse({
      ...((await readJson(request)) as Record<string, unknown>),
      id,
    });
    return success(await execute(service, actor, "design_update_recipe", input));
  }

  if (segments[4] !== "files" || segments.length < 6) return null;
  const path = segments.slice(5).join("/");
  const parsedFile = readFileSchema
    .pick({ id: true, path: true })
    .parse({ id, path });
  if (request.method === "GET" || request.method === "HEAD") {
    const { loaded } = (await execute(
      service,
      actor,
      "design_file_download",
      parsedFile,
    )) as {
      loaded: NonNullable<Awaited<ReturnType<DesignService["readFile"]>>>;
    };
    const headers = new Headers({
      "Content-Type": loaded.record.content_type,
      "Content-Length": String(loaded.record.byte_size),
      ETag: `"${loaded.record.sha256}"`,
      "Cache-Control": "private, max-age=300",
      "X-Content-Type-Options": "nosniff",
    });
    return new Response(request.method === "HEAD" ? null : loaded.object.body, {
      headers,
    });
  }
  if (request.method === "PUT") {
    const body = (await readJson(request)) as Record<string, unknown>;
    const input = uploadFileSchema.parse({ ...body, id, path });
    return success(await execute(service, actor, "design_upload_asset", input));
  }
  return null;
}

async function handleAuthorizedHttp(
  request: Request,
  env: Pick<Env, "DB" | "ASSETS"> & Partial<Pick<Env, "MANAGEMENT_WRITE_ENABLED">>,
  actor: OperationActor,
): Promise<Response> {
  const url = new URL(request.url);
  const service = new DesignService(env);
  if (
    request.method === "POST" &&
    url.pathname.startsWith("/api/v1/operations/")
  ) {
    return success(
      await executeDesignOperation(
        service,
        actor,
        url.pathname.slice("/api/v1/operations/".length),
        await readJson(request),
      ),
    );
  }
  const response = await handleRecipeRoutes(request, service, actor);
  if (response) return response;
  return new Response("Not found", { status: 404 });
}

export async function handlePreauthenticatedHttp(
  request: Request,
  env: Pick<Env, "DB" | "ASSETS"> & Partial<Pick<Env, "MANAGEMENT_WRITE_ENABLED">>,
  actor: OperationActor,
): Promise<Response> {
  try {
    return await handleAuthorizedHttp(request, env, actor);
  } catch (error) {
    return failure(error);
  }
}

export async function handleHttp(
  request: Request,
  env: Env,
): Promise<Response> {
  try {
    requireSite(request, env);
    return await handleAuthorizedHttp(request, env, siteActor);
  } catch (error) {
    return failure(error);
  }
}
