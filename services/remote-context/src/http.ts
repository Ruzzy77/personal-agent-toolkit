import { protectedResourceMetadata } from "@personal-agent/remote-runtime";

import {
  authenticateMcp,
  authenticateSync,
  resourceUrl,
  supportedScopes,
} from "./auth";
import { CorpusService } from "./corpus";
import { asContextError, ContextError } from "./errors";
import { HypesService } from "./hypes";
import { importCorpusMetadata } from "./imports";
import { SenseService } from "./sense";
import { MCP_SURFACES } from "./surfaces";
import type { Env, Principal, ResourceKind } from "./types";

const JSON_BODY_LIMIT = 16 * 1024 * 1024;

function json(
  body: unknown,
  status = 200,
  headers: HeadersInit = {},
): Response {
  return Response.json(body, {
    status,
    headers: { "Cache-Control": "no-store", ...headers },
  });
}

async function readJson(request: Request): Promise<unknown> {
  const length = Number(request.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(length) && length > JSON_BODY_LIMIT) {
    throw new ContextError(
      "request_too_large",
      "request body is too large",
      413,
    );
  }
  try {
    return await request.json();
  } catch {
    throw new ContextError("invalid_json", "request body must be JSON");
  }
}

function protectedMetadata(
  env: Env,
  kind: ResourceKind,
): Record<string, unknown> {
  const resource = resourceUrl(env, kind);
  return protectedResourceMetadata({
    resource: resourceUrl(env, kind),
    authorizationServer: env.AUTH_ISSUER,
    scopes: supportedScopes(kind),
    documentation: `${new URL(resource).origin}/`,
  });
}

function metadataKind(path: string): ResourceKind | null {
  if (
    path === "/.well-known/oauth-protected-resource" ||
    path === "/.well-known/oauth-protected-resource/mcp"
  )
    return "toolkit";
  if (path === "/.well-known/oauth-protected-resource/sense/mcp")
    return "sense";
  if (path === "/.well-known/oauth-protected-resource/corpus/mcp")
    return "corpus";
  if (path === "/.well-known/oauth-protected-resource/hypes/mcp")
    return "hypes";
  return null;
}

function unauthorizedMetadata(env: Env, kind: ResourceKind): HeadersInit {
  const origin = new URL(resourceUrl(env, kind)).origin;
  const metadataPath =
    kind === "toolkit"
      ? "/.well-known/oauth-protected-resource/mcp"
      : `/.well-known/oauth-protected-resource/${kind}/mcp`;
  return {
    "WWW-Authenticate": `Bearer resource_metadata="${origin}${metadataPath}"`,
  };
}

function shard(env: Env, ownerId: string, corpusId: string): DurableObjectStub {
  return env.CORPUS_SHARDS.get(
    env.CORPUS_SHARDS.idFromName(`${ownerId}:${corpusId}`),
  );
}

async function callShard(
  env: Env,
  principal: Principal,
  corpusId: string,
  path: string,
  body: unknown,
): Promise<Response> {
  return shard(env, principal.ownerId, corpusId).fetch(
    `https://corpus.internal${path}`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Owner-Id": principal.ownerId,
      },
      body: JSON.stringify(body),
    },
  );
}

async function syncConnect(
  request: Request,
  env: Env,
  principal: Principal,
): Promise<Response> {
  const deviceId = principal.deviceId!;
  const id = env.SYNC_BROKERS.idFromName(`${principal.ownerId}:${deviceId}`);
  const broker = env.SYNC_BROKERS.get(id);
  const headers = new Headers(request.headers);
  headers.set("X-Owner-Id", principal.ownerId);
  headers.set("X-Device-Id", deviceId);
  headers.delete("Authorization");
  headers.delete("X-Personal-Agent-Device");
  return broker.fetch("https://sync.internal/connect", {
    method: "GET",
    headers,
  });
}

async function analysisProxy(request: Request, env: Env, principal: Principal) {
  if (!env.DOCUMENT_ANALYZER) {
    throw new ContextError(
      "remote_analyzer_unavailable",
      "the remote Document Files analyzer is not configured",
      503,
    );
  }
  const required = [
    "X-Analysis-Job",
    "X-Input-Sha256",
    "X-Format-Id",
    "X-Source-Size",
  ];
  if (required.some((name) => !request.headers.get(name))) {
    throw new ContextError(
      "invalid_analysis_request",
      "analysis identity headers are required",
    );
  }
  const declared = Number(request.headers.get("X-Source-Size"));
  const actualLength = Number(
    request.headers.get("Content-Length") ?? declared,
  );
  if (
    !Number.isInteger(declared) ||
    declared < 0 ||
    declared > 1024 * 1024 * 1024 ||
    (Number.isFinite(actualLength) && actualLength !== declared)
  ) {
    throw new ContextError(
      "invalid_analysis_request",
      "analysis source size is invalid",
    );
  }
  const headers = new Headers();
  for (const name of required) headers.set(name, request.headers.get(name)!);
  headers.set("Content-Type", "application/octet-stream");
  headers.set("X-Owner-Id", principal.ownerId);
  headers.set("X-Device-Id", principal.deviceId!);
  const remote = await env.DOCUMENT_ANALYZER.fetch(
    "https://analyzer.internal/v1/analyze",
    {
      method: "POST",
      headers,
      body: request.body,
    },
  );
  return new Response(remote.body, {
    status: remote.status,
    headers: remote.headers,
  });
}

async function verificationSummary(
  env: Env,
  principal: Principal,
): Promise<Record<string, unknown>> {
  const [sense, hypes, receipt] = await Promise.all([
    new SenseService(env.STATE_DB, principal.ownerId).verificationState(),
    new HypesService(env.STATE_DB, principal.ownerId).verificationState(),
    env.STATE_DB.prepare(
      `SELECT source_digest, counts_json, imported_at
         FROM migration_receipts
         WHERE owner_id = ? AND product = 'corpus-metadata'
         ORDER BY imported_at DESC LIMIT 1`,
    )
      .bind(principal.ownerId)
      .first<{
        source_digest: string;
        counts_json: string;
        imported_at: string;
      }>(),
  ]);
  return {
    mcp_surfaces: MCP_SURFACES,
    sense,
    hypes,
    corpus_metadata: receipt
      ? {
          source_digest: receipt.source_digest,
          counts: JSON.parse(receipt.counts_json),
          imported_at: receipt.imported_at,
        }
      : null,
  };
}

async function syncRoutes(
  request: Request,
  env: Env,
  url: URL,
): Promise<Response | null> {
  if (!url.pathname.startsWith("/sync/v1/")) return null;
  const principal = await authenticateSync(request, env);
  if (request.method === "GET" && url.pathname === "/sync/v1/connect") {
    return syncConnect(request, env, principal);
  }
  if (request.method === "POST" && url.pathname === "/sync/v1/import/sense") {
    const body = (await readJson(request)) as {
      profile?: unknown;
      skills?: unknown;
    };
    const service = new SenseService(env.STATE_DB, principal.ownerId);
    const profile = await service.importProfile(body.profile);
    const skills = await service.importSkills(body.skills ?? []);
    return json({ ok: true, result: { profile, skills } });
  }
  if (request.method === "POST" && url.pathname === "/sync/v1/import/hypes") {
    const result = await new HypesService(
      env.STATE_DB,
      principal.ownerId,
    ).importGraph(await readJson(request));
    return json({ ok: true, result });
  }
  if (
    request.method === "POST" &&
    url.pathname === "/sync/v1/import/corpus-metadata"
  ) {
    const result = await importCorpusMetadata(
      env.STATE_DB,
      principal.ownerId,
      await readJson(request),
    );
    return json({ ok: true, result });
  }
  if (
    request.method === "GET" &&
    url.pathname === "/sync/v1/verification-summary"
  ) {
    return json({
      ok: true,
      result: await verificationSummary(env, principal),
    });
  }
  if (request.method === "POST" && url.pathname === "/sync/v1/analysis") {
    return analysisProxy(request, env, principal);
  }

  const documentImport =
    /^\/sync\/v1\/corpora\/([^/]+)\/documents:import$/.exec(url.pathname);
  if (request.method === "POST" && documentImport) {
    const corpusId = decodeURIComponent(documentImport[1]!);
    const body = (await readJson(request)) as Record<string, unknown>;
    if (body.corpusId !== corpusId) {
      throw new ContextError(
        "corpus_mismatch",
        "document import corpus id does not match its route",
      );
    }
    return callShard(env, principal, corpusId, "/documents/import", body);
  }

  const externalImport = /^\/sync\/v1\/corpora\/([^/]+)\/external:import$/.exec(
    url.pathname,
  );
  if (request.method === "POST" && externalImport) {
    const corpusId = decodeURIComponent(externalImport[1]!);
    const body = (await readJson(request)) as Record<string, unknown>;
    if (body.corpusId !== corpusId) {
      throw new ContextError(
        "corpus_mismatch",
        "external Source import corpus id does not match its route",
      );
    }
    return callShard(env, principal, corpusId, "/external/import", body);
  }

  const inventory = /^\/sync\/v1\/corpora\/([^/]+)\/inventory$/.exec(
    url.pathname,
  );
  if (request.method === "POST" && inventory) {
    const corpusId = decodeURIComponent(inventory[1]!);
    return callShard(
      env,
      principal,
      corpusId,
      "/inventory",
      await readJson(request),
    );
  }

  const maintenance = /^\/sync\/v1\/corpora\/([^/]+)\/maintenance$/.exec(
    url.pathname,
  );
  if (request.method === "POST" && maintenance) {
    const corpusId = decodeURIComponent(maintenance[1]!);
    const body = (await readJson(request)) as Record<string, unknown>;
    if (body.corpusId !== corpusId) {
      throw new ContextError(
        "corpus_mismatch",
        "Corpus maintenance id does not match its route",
      );
    }
    return callShard(env, principal, corpusId, "/maintenance", body);
  }

  const revisionResolve =
    /^\/sync\/v1\/corpora\/([^/]+)\/revisions:resolve$/.exec(url.pathname);
  if (request.method === "POST" && revisionResolve) {
    const corpusId = decodeURIComponent(revisionResolve[1]!);
    const body = (await readJson(request)) as Record<string, unknown>;
    if (body.corpusId !== corpusId) {
      throw new ContextError(
        "corpus_mismatch",
        "revision lookup corpus id does not match its route",
      );
    }
    return callShard(env, principal, corpusId, "/revision/resolve", body);
  }

  const begin = /^\/sync\/v1\/corpora\/([^/]+)\/projections:begin$/.exec(
    url.pathname,
  );
  if (request.method === "POST" && begin) {
    const corpusId = decodeURIComponent(begin[1]!);
    const body = (await readJson(request)) as Record<string, unknown>;
    if (body.corpusId !== corpusId) {
      throw new ContextError(
        "corpus_mismatch",
        "projection corpus id does not match its route",
      );
    }
    return callShard(env, principal, corpusId, "/projection/begin", body);
  }
  const units = /^\/sync\/v1\/corpora\/([^/]+)\/projection-units:append$/.exec(
    url.pathname,
  );
  if (request.method === "POST" && units) {
    const corpusId = decodeURIComponent(units[1]!);
    return callShard(
      env,
      principal,
      corpusId,
      "/projection/units",
      await readJson(request),
    );
  }
  const commit = /^\/sync\/v1\/corpora\/([^/]+)\/projections:commit$/.exec(
    url.pathname,
  );
  if (request.method === "POST" && commit) {
    const corpusId = decodeURIComponent(commit[1]!);
    return callShard(
      env,
      principal,
      corpusId,
      "/projection/commit",
      await readJson(request),
    );
  }
  const state =
    /^\/sync\/v1\/corpora\/([^/]+)\/documents\/([^/]+)\/source-state$/.exec(
      url.pathname,
    );
  if (request.method === "POST" && state) {
    const corpusId = decodeURIComponent(state[1]!);
    const documentId = decodeURIComponent(state[2]!);
    const body = (await readJson(request)) as Record<string, unknown>;
    if (body.corpusId !== corpusId || body.documentId !== documentId) {
      throw new ContextError(
        "document_mismatch",
        "source state identity does not match its route",
      );
    }
    return callShard(env, principal, corpusId, "/source-state", body);
  }
  return json(
    {
      ok: false,
      error: { code: "not_found", message: "Sync route was not found" },
    },
    404,
  );
}

export async function handleHttp(
  request: Request,
  env: Env,
): Promise<Response> {
  try {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({
        ok: true,
        service: "personal-agent-context",
        version: "0.1.1",
        resources: ["toolkit", "sense", "corpus", "hypes"],
      });
    }
    const kind = metadataKind(url.pathname);
    if (request.method === "GET" && kind)
      return json(protectedMetadata(env, kind));

    const sync = await syncRoutes(request, env, url);
    if (sync) return sync;

    const job = /^\/corpus\/api\/v1\/jobs\/(job_[0-9a-f]{32})$/.exec(
      url.pathname,
    );
    if (request.method === "GET" && job) {
      const principal = await authenticateMcp(request, env, "corpus", [
        "corpus.read",
      ]);
      const result = await new CorpusService(env, principal).jobStatus({
        job_id: job[1]!,
      });
      return json({ ok: true, result });
    }
    return json(
      {
        ok: false,
        error: { code: "not_found", message: "route was not found" },
      },
      404,
    );
  } catch (error) {
    const normalized = asContextError(error);
    let headers: HeadersInit = {};
    if (normalized.status === 401) {
      const path = new URL(request.url).pathname;
      const kind: ResourceKind = path.startsWith("/sense")
        ? "sense"
        : path.startsWith("/hypes")
          ? "hypes"
          : "corpus";
      headers = unauthorizedMetadata(env, kind);
    }
    return json(
      {
        ok: false,
        error: {
          code: normalized.code,
          message: normalized.message,
          ...(Object.keys(normalized.details).length > 0
            ? { details: normalized.details }
            : {}),
        },
      },
      normalized.status,
      headers,
    );
  }
}
