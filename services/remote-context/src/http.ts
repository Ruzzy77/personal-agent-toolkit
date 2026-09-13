import packageInfo from "../package.json";
import { managementSchemaReady } from "./management-db";
import { protectedResourceMetadata } from "@personal-agent/remote-runtime";

import {
  authenticateMcp,
  authenticateSync,
  resourceUrl,
  supportedScopes,
} from "./auth";
import { CorpusService } from "./corpus";
import { canonicalJson, nowIso } from "./canonical";
import { handleAdminSite } from "./admin-site";
import { handleContextSite } from "./context-site";
import { asContextError, ContextError } from "./errors";
import { HypesService } from "./hypes";
import { importCorpusMetadata } from "./imports";
import { SenseService } from "./sense";
import { MCP_SURFACES } from "./surfaces";
import { syncConnectionsUpsertSchema } from "./sync-connection-schemas";
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
  if (
    (await managementSchemaReady(env.STATE_DB)) &&
    !["/inventory", "/maintenance", "/revision/resolve"].includes(path)
  ) {
    const blocked = await env.STATE_DB.prepare(
      `SELECT 1 FROM corpus_connections c JOIN corpus_spaces s ON s.owner_id=c.owner_id AND s.space_id=c.space_id
      WHERE c.owner_id=? AND c.corpus_id=? AND (s.trash_group_id IS NOT NULL OR s.state<>'active' OR c.configuration_state IN ('detaching','detached')) LIMIT 1`,
    )
      .bind(principal.ownerId, corpusId)
      .first();
    if (blocked)
      throw new ContextError(
        "source_registration_inactive",
        "Source updates are blocked while an owning registration or Space is inactive",
        409,
      );
    if (
      await env.STATE_DB.prepare(
        "SELECT 1 FROM sqlite_master WHERE name='corpus_registration_detachments'",
      ).first()
    ) {
      const retiredWithoutBinding = await env.STATE_DB.prepare(
        `SELECT 1 FROM corpus_registration_detachments d WHERE d.owner_id=? AND d.device_id=? AND d.kind='connection' AND
        NOT EXISTS(SELECT 1 FROM corpus_connections c WHERE c.owner_id=d.owner_id AND c.device_id=d.device_id AND c.corpus_id=? AND c.configuration_state='ready') LIMIT 1`,
      )
        .bind(principal.ownerId, principal.deviceId ?? "", corpusId)
        .first();
      if (retiredWithoutBinding)
        throw new ContextError(
          "source_registration_required",
          "A current registered Source connection is required after retirement",
          409,
        );
    }
  }
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

async function upsertSyncConnections(
  env: Env,
  principal: Principal,
  raw: unknown,
): Promise<Record<string, unknown>> {
  const input = syncConnectionsUpsertSchema.parse(raw);
  const ownerId = principal.ownerId;
  const deviceId = principal.deviceId!;
  const registered = await env.STATE_DB.prepare(
    "SELECT 1 FROM sync_devices WHERE owner_id=? AND device_id=? AND status='active'",
  )
    .bind(ownerId, deviceId)
    .first();
  if (!registered)
    throw new ContextError(
      "device_not_registered",
      "Connect the registered Sync device before publishing Connections",
      409,
    );
  const keys = input.connections.map(
    (row) => `${row.spaceId}:${row.connectionId}`,
  );
  if (
    new Set(keys).size !== keys.length ||
    Object.keys(input.expected_generations).length !== keys.length ||
    keys.some((key) => !Object.hasOwn(input.expected_generations, key))
  ) {
    throw new ContextError(
      "invalid_connection_batch",
      "Each unique Connection requires exactly one expected generation",
    );
  }
  const managed = await managementSchemaReady(env.STATE_DB);
  const guards: D1PreparedStatement[] = [];
  const writes: D1PreparedStatement[] = [];
  const now = nowIso();
  for (const row of input.connections) {
    const key = `${row.spaceId}:${row.connectionId}`;
    const expected = input.expected_generations[key]!;
    if (row.deviceId !== deviceId)
      throw new ContextError(
        "connection_device_mismatch",
        "Connection must belong to the authenticated Sync device",
        403,
      );
    if (
      row.localConnectionKey !== null &&
      !/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,499}$/.test(row.localConnectionKey)
    ) {
      throw new ContextError(
        "private_path_rejected",
        "Local Connection keys must be opaque identifiers, not filesystem paths",
      );
    }
    if (
      new Set(row.roles).size !== row.roles.length ||
      (row.roles.includes("source") && !row.corpusId) ||
      (row.indexMode === "indexed" &&
        (!row.roles.includes("source") || !row.corpusId)) ||
      (row.permission !== "read_only" && !row.roles.includes("work"))
    ) {
      throw new ContextError(
        "invalid_connection_policy",
        "Connection roles, indexing, and permission do not agree",
      );
    }
    const space = await env.STATE_DB.prepare(
      `SELECT 1 FROM corpus_spaces WHERE owner_id=? AND space_id=? AND state='active' ${managed ? "AND trash_group_id IS NULL" : ""}`,
    )
      .bind(ownerId, row.spaceId)
      .first();
    if (!space)
      throw new ContextError(
        "space_not_found",
        "Connection requires an existing active Space",
        404,
      );
    const current = await env.STATE_DB.prepare(
      `SELECT roles_json,access_scope,permission,index_mode,corpus_id,device_id,local_connection_key,generation
      FROM corpus_connections WHERE owner_id=? AND space_id=? AND connection_id=?`,
    )
      .bind(ownerId, row.spaceId, row.connectionId)
      .first<{
        roles_json: string;
        access_scope: string;
        permission: string;
        index_mode: string;
        corpus_id: string | null;
        device_id: string | null;
        local_connection_key: string | null;
        generation: number;
      }>();
    if (current?.device_id && current.device_id !== deviceId) {
      throw new ContextError(
        "connection_device_mismatch",
        "Another Sync device owns this Connection",
        403,
      );
    }
    if (
      (expected === "absent" && current) ||
      (expected !== "absent" && current?.generation !== expected)
    ) {
      throw new ContextError(
        "connection_generation_conflict",
        "Connection generation changed or its absence was not confirmed",
        409,
        { connection_key: key },
      );
    }
    const rolesJson = canonicalJson([...row.roles].sort());
    const authorityChanged =
      current &&
      (canonicalJson((JSON.parse(current.roles_json) as string[]).sort()) !==
        rolesJson ||
        current.access_scope !== row.accessScope ||
        current.permission !== row.permission ||
        current.index_mode !== row.indexMode ||
        current.corpus_id !== row.corpusId ||
        current.device_id !== row.deviceId ||
        current.local_connection_key !== row.localConnectionKey);
    if (
      !Number.isSafeInteger(row.generation) ||
      (current &&
        (row.generation < current.generation ||
          (authorityChanged && row.generation <= current.generation)))
    ) {
      throw new ContextError(
        "connection_generation_required",
        "Authority changes require a newer Connection generation",
        409,
        { connection_key: key },
      );
    }
    // Guards are all evaluated before the first write in one D1 transaction.
    // A concurrent change aborts the entire batch, rather than committing its
    // earlier Connections and merely reporting a later zero-row CAS result.
    const identityGuard =
      expected === "absent"
        ? "NOT EXISTS (SELECT 1 FROM corpus_connections WHERE owner_id=? AND space_id=? AND connection_id=?)"
        : "EXISTS (SELECT 1 FROM corpus_connections WHERE owner_id=? AND space_id=? AND connection_id=? AND generation=? AND (device_id=? OR device_id IS NULL))";
    guards.push(
      env.STATE_DB.prepare(
        `SELECT CASE WHEN
      EXISTS (SELECT 1 FROM sync_devices WHERE owner_id=? AND device_id=? AND status='active') AND
      EXISTS (SELECT 1 FROM corpus_spaces WHERE owner_id=? AND space_id=? AND state='active' ${managed ? "AND trash_group_id IS NULL" : ""}) AND
      ${identityGuard} THEN 1 ELSE json('sync_connection_conflict') END AS valid`,
      ).bind(
        ownerId,
        deviceId,
        ownerId,
        row.spaceId,
        ownerId,
        row.spaceId,
        row.connectionId,
        ...(expected === "absent" ? [] : [expected, deviceId]),
      ),
    );
    writes.push(
      env.STATE_DB.prepare(
        `INSERT INTO corpus_connections(owner_id,space_id,connection_id,display_name,roles_json,access_scope,
      permission,index_mode,corpus_id,device_id,local_connection_key,generation,configuration_state,source_state,record_state,captured_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(owner_id,space_id,connection_id) DO UPDATE SET display_name=excluded.display_name,roles_json=excluded.roles_json,
      access_scope=excluded.access_scope,permission=excluded.permission,index_mode=excluded.index_mode,corpus_id=excluded.corpus_id,
      device_id=excluded.device_id,local_connection_key=excluded.local_connection_key,generation=excluded.generation,
      configuration_state=excluded.configuration_state,source_state=excluded.source_state,record_state=excluded.record_state,
      captured_at=excluded.captured_at,updated_at=excluded.updated_at`,
      ).bind(
        ownerId,
        row.spaceId,
        row.connectionId,
        row.displayName,
        rolesJson,
        row.accessScope,
        row.permission,
        row.indexMode,
        row.corpusId,
        deviceId,
        row.localConnectionKey,
        row.generation,
        row.configurationState,
        row.sourceState,
        row.recordState,
        row.capturedAt,
        now,
      ),
    );
  }
  try {
    await env.STATE_DB.batch([...guards, ...writes]);
  } catch (error) {
    if (
      error instanceof Error &&
      /malformed JSON|registration_detached/.test(error.message)
    ) {
      throw new ContextError(
        "connection_generation_conflict",
        "Connection, Space, or device changed before the batch; no Connections were updated",
        409,
      );
    }
    throw error;
  }
  return {
    updated_connections: input.connections.map((row) => ({
      space_id: row.spaceId,
      connection_id: row.connectionId,
      generation: row.generation,
    })),
    updated_count: input.connections.length,
    updated_at: now,
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
  if (
    request.method === "POST" &&
    url.pathname === "/sync/v1/connections:upsert"
  ) {
    return json({
      ok: true,
      result: await upsertSyncConnections(
        env,
        principal,
        await readJson(request),
      ),
    });
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
    const adminSite = await handleAdminSite(request, env);
    if (adminSite) return adminSite;
    const contextSite = await handleContextSite(request, env);
    if (contextSite) return contextSite;
    if (request.method === "GET" && url.pathname === "/health") {
      return json({
        ok: true,
        service: "personal-agent-context",
        version: packageInfo.version,
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
