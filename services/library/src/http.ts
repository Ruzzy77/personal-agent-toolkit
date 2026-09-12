import { executeLibraryOperation } from "./operations";
import { bearerToken, constantTimeEqual } from "@personal-agent/remote-runtime";

import { asLibraryError, LibraryError } from "./errors";
import {
  listIssuesSchema,
  createIssueSchema,
  importIssueSchema,
  issueIdSchema,
  updateIssueBodySchema,
  updateIssueFragmentsSchema,
} from "./schemas";
import { LibraryService } from "./service";
import type { Env } from "./types";

const API_ISSUES = "/api/v1/issues";
const API_IMPORT_ISSUES = "/api/v1/import/issues";
const API_ASSETS = "/api/v1/assets/";
const MEDIA = "/media/";
const siteActor = {
  ownerId: "site-owner",
  clientId: "site",
  kind: "owner" as const,
  scopes: new Set(["library.read", "library.write"]),
};
const execute = (service: LibraryService, name: string, input: unknown) =>
  executeLibraryOperation(service, siteActor, name, input);
const MAX_JSON_BYTES = 2_100_000;

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: { "Cache-Control": "private, no-store" },
  });
}

function success(result: unknown, status = 200): Response {
  return json({ ok: true, result }, status);
}

function failure(error: unknown): Response {
  const normalized = asLibraryError(error);
  return json(
    {
      ok: false,
      error: {
        code: normalized.code,
        message: normalized.message,
        details: normalized.details,
      },
    },
    normalized.status,
  );
}

function requireSite(request: Request, env: Env): void {
  const token = bearerToken(request);
  if (!token || !constantTimeEqual(token, env.LIBRARY_SITE_TOKEN)) {
    throw new LibraryError(
      "invalid_site_credential",
      "a valid Library Site credential is required",
      401,
    );
  }
}

async function readJson(request: Request): Promise<unknown> {
  const length = Number(request.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(length) && length > MAX_JSON_BYTES) {
    throw new LibraryError(
      "request_too_large",
      "request body is too large",
      413,
    );
  }
  try {
    return await request.json();
  } catch {
    throw new LibraryError("invalid_json", "request body must be JSON");
  }
}

function pathTail(pathname: string, prefix: string): string | null {
  try {
    const value = decodeURIComponent(pathname.slice(prefix.length));
    return value || null;
  } catch {
    return null;
  }
}

async function handleIssues(
  request: Request,
  url: URL,
  service: LibraryService,
): Promise<Response | null> {
  if (url.pathname === API_ISSUES && request.method === "GET") {
    const { limit, lifecycle, offset } = listIssuesSchema.parse({
      ...Object.fromEntries(url.searchParams),
      limit: Number(url.searchParams.get("limit") ?? 100),
      offset: Number(url.searchParams.get("offset") ?? 0),
    });
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
      throw new LibraryError("invalid_request", "issue limit is invalid");
    }
    const result = await execute(service, "library_list_issues", {
      collection: url.searchParams.get("collection") ?? undefined,
      limit,
      lifecycle,
      offset,
    });
    return success(result.issues);
  }

  if (url.pathname === API_ISSUES && request.method === "POST") {
    const input = createIssueSchema.parse(await readJson(request));
    return success(await execute(service, "library_create_issue", input), 201);
  }

  if (url.pathname === API_IMPORT_ISSUES && request.method === "POST") {
    const input = importIssueSchema.parse(await readJson(request));
    return success(await execute(service, "library_import_issue", input), 201);
  }

  if (url.pathname === `${API_ISSUES}/by-path` && request.method === "GET") {
    const path = url.searchParams.get("path");
    if (!path)
      throw new LibraryError("invalid_request", "issue path is required");
    return success(
      (await execute(service, "library_issue_by_path", { path })).issue,
    );
  }

  if (!url.pathname.startsWith(`${API_ISSUES}/`)) return null;
  const rawId = pathTail(url.pathname, `${API_ISSUES}/`);
  const parsedId = issueIdSchema.safeParse(rawId);
  if (!parsedId.success) {
    throw new LibraryError("invalid_request", "issue id is invalid");
  }
  const id = parsedId.data;

  if (request.method === "GET") {
    return success(
      (await execute(service, "library_read_issue", { id })).issue,
    );
  }

  if (request.method === "PUT") {
    const input = updateIssueBodySchema.parse(await readJson(request));
    return success(
      await execute(service, "library_update_issue", { id, ...input }),
    );
  }

  if (request.method === "PATCH") {
    const input = updateIssueFragmentsSchema.parse(await readJson(request));
    return success(
      await execute(service, "library_edit_fragments", { id, ...input }),
    );
  }

  throw new LibraryError("method_not_allowed", "method is not allowed", 405);
}

async function handleAssetWrite(
  request: Request,
  url: URL,
  service: LibraryService,
): Promise<Response | null> {
  if (!url.pathname.startsWith(API_ASSETS)) return null;
  if (request.method !== "PUT") {
    throw new LibraryError("method_not_allowed", "method is not allowed", 405);
  }
  const rawKey = pathTail(url.pathname, API_ASSETS);
  if (!rawKey) {
    throw new LibraryError("invalid_asset_path", "asset path is invalid");
  }
  const contentType = request.headers
    .get("Content-Type")
    ?.split(";", 1)[0]
    ?.trim()
    .toLowerCase();
  const bytes = new Uint8Array(await request.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  return success(
    await execute(service, "library_upload_asset", {
      path: rawKey,
      content_type: contentType ?? "",
      base64: btoa(binary),
    }),
  );
}

async function handleMediaRead(
  request: Request,
  url: URL,
  service: LibraryService,
): Promise<Response | null> {
  if (!url.pathname.startsWith(MEDIA)) return null;
  if (!["GET", "HEAD"].includes(request.method)) {
    throw new LibraryError("method_not_allowed", "method is not allowed", 405);
  }
  const rawKey = pathTail(url.pathname, MEDIA);
  if (!rawKey) return new Response("Not found", { status: 404 });
  const { object } = (await execute(service, "library_media_read", {
    path: rawKey,
  })) as { object: Awaited<ReturnType<LibraryService["readAsset"]>> };
  if (!object) return new Response("Not found", { status: 404 });
  const headers = new Headers({ "Cache-Control": "private, max-age=3600" });
  object.writeHttpMetadata(headers);
  headers.set("ETag", object.httpEtag ?? object.etag);
  return new Response(request.method === "HEAD" ? null : object.body, {
    headers,
  });
}

export async function handleHttp(
  request: Request,
  env: Env,
): Promise<Response> {
  try {
    requireSite(request, env);
    const url = new URL(request.url);
    const service = new LibraryService(env);
    if (
      request.method === "POST" &&
      url.pathname.startsWith("/api/v1/operations/")
    ) {
      return json({
        ok: true,
        result: await executeLibraryOperation(
          service,
          {
            ownerId: "site-owner",
            clientId: "site",
            kind: "owner",
            scopes: new Set(["library.read", "library.write"]),
          },
          url.pathname.slice("/api/v1/operations/".length),
          await readJson(request),
        ),
      });
    }
    const issues = await handleIssues(request, url, service);
    if (issues) return issues;
    const asset = await handleAssetWrite(request, url, service);
    if (asset) return asset;
    const media = await handleMediaRead(request, url, service);
    if (media) return media;
    return new Response("Not found", { status: 404 });
  } catch (error) {
    return failure(error);
  }
}
