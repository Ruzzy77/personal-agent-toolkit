import { canonicalJson, nowIso, sha256Hex } from "./canonical";
import { ContextError } from "./errors";
import {
  corpusContextItemsReviseSchema,
  corpusContextSkillReviseSchema,
  corpusFileDeleteSchema,
  corpusFileListSchema,
  corpusFileReadSchema,
  corpusFileRestoreSchema,
  corpusFileSelectSchema,
  corpusFileWriteSchema,
  corpusJobStatusSchema,
  corpusSourceRefreshSchema,
  corpusSpaceGetSchema,
  corpusSpaceListSchema,
  corpusSpaceSearchSchema,
} from "./schemas";
import type { Env, Principal, SyncJobRequest } from "./types";

interface SpaceRow {
  space_id: string;
  display_name: string;
  state: "active" | "archived";
  access_scope: "remote_allowed" | "local_only";
  primary_work_connection_id: string | null;
  updated_at: string;
}

interface ContextRow {
  space_id: string;
  title: string;
  purpose: string;
  scope_json: string;
  version: number;
  updated_at: string;
}

interface ContextItemRow {
  item_id: string;
  kind: string;
  body_text: string;
  attributes_json: string;
  created_at: string;
}

interface SkillRow {
  name: string;
  description: string;
  instructions: string;
  version: string;
  updated_at: string;
}

interface ConnectionRow {
  space_id: string;
  connection_id: string;
  display_name: string;
  roles_json: string;
  access_scope: "remote_allowed" | "local_only";
  permission: "read_only" | "read_write";
  index_mode: "indexed" | "not_indexed";
  corpus_id: string | null;
  device_id: string | null;
  generation: number;
  configuration_state: string;
  source_state: string | null;
  record_state: string | null;
  captured_at: string | null;
  updated_at: string;
}

interface CurrentFileRow {
  relative_path: string;
  version_token: string | null;
  state: string;
  reason: string | null;
  residency_state: string | null;
  size: number | null;
  modified_ns: string | null;
  updated_at: string;
}

interface BrokerResult {
  jobId: string;
  state: "queued" | "dispatched" | "succeeded" | "failed";
  response?: Record<string, unknown>;
  deviceOnline: boolean;
}

interface ReadReference {
  version: 1;
  spaceId: string;
  connectionId: string;
  corpusId: string;
  unitId: string;
}

function parseObject(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new ContextError(
      "invalid_stored_state",
      "stored Corpus state is invalid",
      500,
    );
  }
  return parsed as Record<string, unknown>;
}

function parseStrings(value: string): string[] {
  const parsed: unknown = JSON.parse(value);
  if (
    !Array.isArray(parsed) ||
    !parsed.every((item) => typeof item === "string")
  ) {
    throw new ContextError(
      "invalid_stored_state",
      "stored Corpus state is invalid",
      500,
    );
  }
  return parsed;
}

function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary)
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
}

function base64UrlToBytes(value: string): Uint8Array {
  const padded =
    value.replaceAll("-", "+").replaceAll("_", "/") +
    "=".repeat((4 - (value.length % 4)) % 4);
  let binary: string;
  try {
    binary = atob(padded);
  } catch {
    throw new ContextError("invalid_reference", "Space reference is invalid");
  }
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function encodeReadReference(value: ReadReference): string {
  return `read1.${bytesToBase64Url(new TextEncoder().encode(canonicalJson(value)))}`;
}

function decodeReadReference(value: string): ReadReference {
  if (!value.startsWith("read1.") || value.length > 8192) {
    throw new ContextError("invalid_reference", "Space reference is invalid");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(
      new TextDecoder().decode(base64UrlToBytes(value.slice(6))),
    );
  } catch (error) {
    if (error instanceof ContextError) throw error;
    throw new ContextError("invalid_reference", "Space reference is invalid");
  }
  const candidate = parsed as Partial<ReadReference> | null;
  if (
    !candidate ||
    candidate.version !== 1 ||
    typeof candidate.spaceId !== "string" ||
    typeof candidate.connectionId !== "string" ||
    typeof candidate.corpusId !== "string" ||
    typeof candidate.unitId !== "string"
  ) {
    throw new ContextError("invalid_reference", "Space reference is invalid");
  }
  return candidate as ReadReference;
}

function skillProjection(row: SkillRow | null, includeInstructions: boolean) {
  if (!row) return null;
  return {
    name: row.name,
    description: row.description,
    version: row.version,
    updated_at: row.updated_at,
    provenance: "user_approved_context_skill",
    scope: "linked_context",
    source_evidence: false,
    ...(includeInstructions ? { instructions: row.instructions } : {}),
  };
}

export class CorpusService {
  constructor(
    private readonly env: Env,
    private readonly principal: Principal,
  ) {}

  private async space(spaceId: string): Promise<SpaceRow> {
    const row = await this.env.STATE_DB.prepare(
      `SELECT space_id, display_name, state, access_scope,
              primary_work_connection_id, updated_at
       FROM corpus_spaces WHERE owner_id = ? AND space_id = ?`,
    )
      .bind(this.principal.ownerId, spaceId)
      .first<SpaceRow>();
    if (
      !row ||
      row.state !== "active" ||
      row.access_scope !== "remote_allowed"
    ) {
      throw new ContextError("space_not_found", "space does not exist", 404, {
        space_id: spaceId,
      });
    }
    return row;
  }

  private async context(spaceId: string): Promise<ContextRow | null> {
    return this.env.STATE_DB.prepare(
      `SELECT space_id, title, purpose, scope_json, version, updated_at
       FROM corpus_contexts WHERE owner_id = ? AND space_id = ?`,
    )
      .bind(this.principal.ownerId, spaceId)
      .first<ContextRow>();
  }

  private async skill(spaceId: string): Promise<SkillRow | null> {
    return this.env.STATE_DB.prepare(
      `SELECT name, description, instructions, version, updated_at
       FROM corpus_context_skills WHERE owner_id = ? AND space_id = ?`,
    )
      .bind(this.principal.ownerId, spaceId)
      .first<SkillRow>();
  }

  private async connections(spaceId: string): Promise<ConnectionRow[]> {
    const rows = await this.env.STATE_DB.prepare(
      `SELECT space_id, connection_id, display_name, roles_json, access_scope,
              permission, index_mode, corpus_id, device_id, generation,
              configuration_state, source_state, record_state, captured_at, updated_at
       FROM corpus_connections
       WHERE owner_id = ? AND space_id = ? AND access_scope = 'remote_allowed'
       ORDER BY connection_id`,
    )
      .bind(this.principal.ownerId, spaceId)
      .all<ConnectionRow>();
    return rows.results;
  }

  private async currentFile(
    spaceId: string,
    connectionId: string,
  ): Promise<Record<string, unknown> | null> {
    const row = await this.env.STATE_DB.prepare(
      `SELECT relative_path, version_token, state, reason, residency_state,
              size, modified_ns, updated_at
       FROM corpus_current_files
       WHERE owner_id = ? AND space_id = ? AND connection_id = ?`,
    )
      .bind(this.principal.ownerId, spaceId, connectionId)
      .first<CurrentFileRow>();
    if (!row) return null;
    return {
      relative_path: row.relative_path,
      version_token: row.version_token,
      state: row.state,
      reason: row.reason,
      residency_state: row.residency_state,
      size: row.size,
      modified_ns: row.modified_ns,
      updated_at: row.updated_at,
    };
  }

  private async publicConnection(
    row: ConnectionRow,
  ): Promise<Record<string, unknown>> {
    const roles = parseStrings(row.roles_json);
    const currentFile = roles.includes("work")
      ? await this.currentFile(row.space_id, row.connection_id)
      : null;
    return {
      connection_id: row.connection_id,
      display_name: row.display_name,
      roles,
      access_scope: row.access_scope,
      permission: row.permission,
      index_mode: row.index_mode,
      connection_state: row.device_id ? "registered" : "unavailable",
      connection_reason:
        row.configuration_state === "ready" ? null : row.configuration_state,
      current_file: currentFile,
      generation: row.generation,
      write_state: roles.includes("work") ? "unknown" : null,
      configuration_state: row.configuration_state,
      ...(roles.includes("source")
        ? {
            source_state: row.source_state ?? "unknown",
            record_state: row.record_state ?? "unknown",
            captured_at: row.captured_at,
          }
        : {}),
    };
  }

  private async publicSpace(
    row: SpaceRow,
    includeContextInstructions: boolean,
  ) {
    const [context, skill, connections] = await Promise.all([
      this.context(row.space_id),
      this.skill(row.space_id),
      this.connections(row.space_id),
    ]);
    const publicConnections = await Promise.all(
      connections.map((connection) => this.publicConnection(connection)),
    );
    const primary =
      row.primary_work_connection_id ??
      connections.find((connection) =>
        parseStrings(connection.roles_json).includes("work"),
      )?.connection_id ??
      null;
    return {
      space_id: row.space_id,
      display_name: row.display_name,
      state: row.state,
      access_scope: row.access_scope,
      context: context
        ? {
            title: context.title,
            purpose: context.purpose,
            access_scope: row.access_scope,
            version: context.version,
            updated_at: context.updated_at,
            skill: skillProjection(skill, includeContextInstructions),
          }
        : null,
      connections: publicConnections,
      primary_work_connection_id: primary,
      current_file:
        primary == null ? null : await this.currentFile(row.space_id, primary),
    };
  }

  async spaceList(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusSpaceListSchema.parse(raw);
    const rows = await this.env.STATE_DB.prepare(
      `SELECT space_id, display_name, state, access_scope,
              primary_work_connection_id, updated_at
       FROM corpus_spaces
       WHERE owner_id = ? AND state = 'active' AND access_scope = 'remote_allowed'
       ORDER BY space_id LIMIT ? OFFSET ?`,
    )
      .bind(this.principal.ownerId, input.limit + 1, input.offset)
      .all<SpaceRow>();
    const selected = rows.results.slice(0, input.limit);
    const spaces = await Promise.all(
      selected.map((row) => this.publicSpace(row, false)),
    );
    const hasMore = rows.results.length > input.limit;
    return {
      offset: input.offset,
      limit: input.limit,
      returned_count: spaces.length,
      has_more: hasMore,
      next_offset: hasMore ? input.offset + spaces.length : null,
      spaces,
      surface_revision: "space-v8-remote1",
      capabilities: {
        context: ["read", "revise_items"],
        context_skill: ["read", "revise"],
        indexed_source: ["search", "read_ref", "refresh"],
        sync_job: ["status"],
        work_file: [
          "list",
          "read",
          "write",
          "delete",
          "select_current",
          "restore",
        ],
      },
    };
  }

  async spaceGet(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusSpaceGetSchema.parse(raw);
    const space = await this.space(input.space_id);
    const result = await this.publicSpace(space, true);
    const context = await this.context(input.space_id);
    if (context) {
      const rows = await this.env.STATE_DB.prepare(
        `SELECT item_id, kind, body_text, attributes_json, created_at
         FROM corpus_context_items
         WHERE owner_id = ? AND space_id = ? AND lifecycle_state = 'active'
         ORDER BY created_at, item_id LIMIT ? OFFSET ?`,
      )
        .bind(
          this.principal.ownerId,
          input.space_id,
          input.context_limit + 1,
          input.context_offset,
        )
        .all<ContextItemRow>();
      const selected = rows.results.slice(0, input.context_limit);
      const hasMore = rows.results.length > input.context_limit;
      const detail = result.context as Record<string, unknown>;
      detail.scope = parseObject(context.scope_json);
      detail.items = selected.map((row) => ({
        item_id: row.item_id,
        kind: row.kind,
        body_text: row.body_text,
        attributes: parseObject(row.attributes_json),
        created_at: row.created_at,
      }));
      detail.offset = input.context_offset;
      detail.limit = input.context_limit;
      detail.returned_count = selected.length;
      detail.has_more = hasMore;
      detail.next_offset = hasMore
        ? input.context_offset + selected.length
        : null;
    }
    return { space: result };
  }

  async reviseContextItems(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusContextItemsReviseSchema.parse(raw);
    await this.space(input.space_id);
    const context = await this.context(input.space_id);
    if (!context)
      throw new ContextError(
        "context_not_found",
        "Context does not exist",
        404,
      );
    if (context.version !== input.expected_version) {
      throw new ContextError(
        "context_conflict",
        "Context changed after it was read",
        409,
        {
          expected_version: input.expected_version,
          current_version: context.version,
        },
      );
    }
    const ids = new Set<string>();
    const statements: D1PreparedStatement[] = [];
    let changed = false;
    for (const revision of input.revisions) {
      if (ids.has(revision.item_id)) {
        throw new ContextError(
          "duplicate_item",
          "a Context item may be revised only once",
        );
      }
      ids.add(revision.item_id);
      const row = await this.env.STATE_DB.prepare(
        `SELECT kind, body_text, attributes_json FROM corpus_context_items
         WHERE owner_id = ? AND space_id = ? AND item_id = ? AND lifecycle_state = 'active'`,
      )
        .bind(this.principal.ownerId, input.space_id, revision.item_id)
        .first<{ kind: string; body_text: string; attributes_json: string }>();
      if (!row) {
        throw new ContextError(
          "context_item_not_found",
          "Context item does not exist",
          404,
          {
            item_id: revision.item_id,
          },
        );
      }
      const attributes = parseObject(row.attributes_json);
      attributes.status = revision.status;
      const attributesJson = canonicalJson(attributes);
      if (
        row.kind === revision.kind &&
        row.body_text === revision.body_text &&
        row.attributes_json === attributesJson
      ) {
        continue;
      }
      changed = true;
      statements.push(
        this.env.STATE_DB.prepare(
          `UPDATE corpus_context_items SET kind = ?, body_text = ?, attributes_json = ?
           WHERE owner_id = ? AND space_id = ? AND item_id = ?
             AND EXISTS (
               SELECT 1 FROM corpus_contexts
               WHERE owner_id = ? AND space_id = ? AND version = ?
             )`,
        ).bind(
          revision.kind,
          revision.body_text,
          attributesJson,
          this.principal.ownerId,
          input.space_id,
          revision.item_id,
          this.principal.ownerId,
          input.space_id,
          context.version,
        ),
      );
    }
    if (!changed) {
      return {
        changed: false,
        space_id: input.space_id,
        version: context.version,
      };
    }
    const nextVersion = context.version + 1;
    const updatedAt = nowIso();
    statements.push(
      this.env.STATE_DB.prepare(
        `UPDATE corpus_contexts SET version = ?, updated_at = ?
         WHERE owner_id = ? AND space_id = ? AND version = ?`,
      ).bind(
        nextVersion,
        updatedAt,
        this.principal.ownerId,
        input.space_id,
        context.version,
      ),
    );
    const results = await this.env.STATE_DB.batch(statements);
    if (results.at(-1)?.meta.changes !== 1) {
      throw new ContextError(
        "context_conflict",
        "Context changed while revisions were being committed",
        409,
        { expected_version: context.version },
      );
    }
    return {
      changed: true,
      space_id: input.space_id,
      version: nextVersion,
      revised_item_ids: [...ids],
      updated_at: updatedAt,
    };
  }

  async reviseContextSkill(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusContextSkillReviseSchema.parse(raw);
    await this.space(input.space_id);
    if (!(await this.context(input.space_id))) {
      throw new ContextError(
        "context_not_found",
        "Context does not exist",
        404,
      );
    }
    const current = await this.skill(input.space_id);
    const normalized = {
      name: input.new_skill.name.trim(),
      description: input.new_skill.description.trim(),
      instructions: input.new_skill.instructions.replace(/\r\n?/g, "\n").trim(),
    };
    const serialized =
      `---\nname: ${normalized.name}\ndescription: ${JSON.stringify(normalized.description)}\n` +
      `---\n\n${normalized.instructions}\n`;
    const version = `context-skill-v1:${await sha256Hex(serialized)}`;
    if (current?.version === version) {
      return { changed: false, skill: skillProjection(current, true) };
    }
    const expected = current?.version ?? "absent";
    if (input.expected_version !== expected) {
      throw new ContextError(
        "context_skill_conflict",
        "Context Skill changed after it was read",
        409,
      );
    }
    const updatedAt = nowIso();
    const result = current
      ? await this.env.STATE_DB.prepare(
          `UPDATE corpus_context_skills SET
             name = ?, description = ?, instructions = ?, version = ?, updated_at = ?
           WHERE owner_id = ? AND space_id = ? AND version = ?`,
        )
          .bind(
            normalized.name,
            normalized.description,
            normalized.instructions,
            version,
            updatedAt,
            this.principal.ownerId,
            input.space_id,
            current.version,
          )
          .run()
      : await this.env.STATE_DB.prepare(
          `INSERT INTO corpus_context_skills(
             owner_id, space_id, name, description, instructions, version, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(owner_id, space_id) DO NOTHING`,
        )
          .bind(
            this.principal.ownerId,
            input.space_id,
            normalized.name,
            normalized.description,
            normalized.instructions,
            version,
            updatedAt,
          )
          .run();
    if (result.meta.changes !== 1) {
      throw new ContextError(
        "context_skill_conflict",
        "Context Skill changed while the revision was being committed",
        409,
      );
    }
    return {
      changed: true,
      skill: skillProjection(
        { ...normalized, version, updated_at: updatedAt },
        true,
      ),
    };
  }

  private shard(corpusId: string): DurableObjectStub {
    const id = this.env.CORPUS_SHARDS.idFromName(
      `${this.principal.ownerId}:${corpusId}`,
    );
    return this.env.CORPUS_SHARDS.get(id);
  }

  private async callShard(
    corpusId: string,
    path: string,
    body: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const result = await this.shard(corpusId).fetch(
      `https://corpus.internal${path}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Owner-Id": this.principal.ownerId,
        },
        body: JSON.stringify(body),
      },
    );
    const payload = (await result.json()) as {
      ok: boolean;
      result?: Record<string, unknown>;
      error?: {
        code: string;
        message: string;
        details?: Record<string, unknown>;
      };
    };
    if (!result.ok || !payload.ok || !payload.result) {
      throw new ContextError(
        payload.error?.code ?? "source_unavailable",
        payload.error?.message ?? "Corpus Source is unavailable",
        result.status,
        payload.error?.details ?? {},
      );
    }
    return payload.result;
  }

  private async sourceConnections(
    spaceId: string,
    connectionId?: string | null,
  ): Promise<ConnectionRow[]> {
    await this.space(spaceId);
    const rows = (await this.connections(spaceId)).filter((row) => {
      const roles = parseStrings(row.roles_json);
      return (
        roles.includes("source") &&
        row.index_mode === "indexed" &&
        row.corpus_id
      );
    });
    const selected = connectionId
      ? rows.filter((row) => row.connection_id === connectionId)
      : rows;
    if (selected.length === 0) {
      throw new ContextError(
        connectionId ? "connection_not_found" : "source_connection_not_found",
        connectionId
          ? "Space Connection does not exist or is not searchable"
          : "Space has no searchable Source Connection",
        404,
      );
    }
    return selected;
  }

  private async sourceConnection(
    spaceId: string,
    connectionId?: string | null,
  ): Promise<ConnectionRow> {
    const rows = await this.sourceConnections(spaceId, connectionId);
    if (rows.length !== 1) {
      throw new ContextError(
        "source_connection_selection_required",
        "one Source Connection must be selected",
        409,
        { available_connection_ids: rows.map((row) => row.connection_id) },
      );
    }
    const selected = rows[0]!;
    if (!selected.device_id) {
      throw new ContextError(
        "source_sync_unavailable",
        "the selected Source Connection has no registered Sync device",
        409,
      );
    }
    return selected;
  }

  async spaceSearch(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusSpaceSearchSchema.parse(raw);
    const connections = await this.sourceConnections(
      input.space_id,
      input.connection_id,
    );
    const perShardLimit = Math.min(100, Math.max(input.limit, 20));
    const results = await Promise.all(
      connections.map(async (connection) => ({
        connection,
        result: await this.callShard(connection.corpus_id!, "/search", {
          query: input.query,
          limit: perShardLimit,
        }),
      })),
    );
    const candidates: Array<Record<string, unknown>> = [];
    for (const { connection, result } of results) {
      const values = Array.isArray(result.candidates) ? result.candidates : [];
      for (const candidate of values) {
        if (
          candidate === null ||
          Array.isArray(candidate) ||
          typeof candidate !== "object"
        )
          continue;
        const item = candidate as Record<string, unknown>;
        if (typeof item.unit_id !== "string") continue;
        candidates.push({
          ...item,
          connection_id: connection.connection_id,
          read_ref: encodeReadReference({
            version: 1,
            spaceId: input.space_id,
            connectionId: connection.connection_id,
            corpusId: connection.corpus_id!,
            unitId: item.unit_id,
          }),
        });
      }
    }
    const selected = candidates.slice(0, input.limit);
    return { query: input.query, count: selected.length, candidates: selected };
  }

  async sourceRefresh(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusSourceRefreshSchema.parse(raw);
    const connection = await this.sourceConnection(
      input.space_id,
      input.connection_id,
    );
    return this.unwrapJob(
      await this.runJob(
        connection,
        "source.refresh",
        input as Record<string, unknown>,
        1024 * 1024,
        20_000,
        30 * 60_000,
      ),
    );
  }

  private async workConnection(
    spaceId: string,
    connectionId?: string | null,
    write = false,
  ): Promise<ConnectionRow> {
    const space = await this.space(spaceId);
    const rows = (await this.connections(spaceId)).filter((row) =>
      parseStrings(row.roles_json).includes("work"),
    );
    const selectedId =
      connectionId ??
      space.primary_work_connection_id ??
      (rows.length === 1 ? rows[0]?.connection_id : null);
    const selected = rows.find((row) => row.connection_id === selectedId);
    if (!selected || !selected.device_id) {
      throw new ContextError(
        "work_connection_not_found",
        "a connected Work Connection must be selected",
        404,
        { available_connection_ids: rows.map((row) => row.connection_id) },
      );
    }
    if (write && selected.permission !== "read_write") {
      throw new ContextError(
        "connection_read_only",
        "selected Work Connection is read-only",
        403,
      );
    }
    return selected;
  }

  private async runJob(
    connection: ConnectionRow,
    operation: string,
    request: Record<string, unknown>,
    maximumResponseBytes: number,
    waitMs = 20_000,
    ttlMs = 5 * 60_000,
  ): Promise<BrokerResult> {
    const jobId = `job_${crypto.randomUUID().replaceAll("-", "")}`;
    const createdAt = nowIso();
    const expiresAt = new Date(Date.now() + ttlMs).toISOString();
    const scope = {
      spaceId: connection.space_id,
      connectionId: connection.connection_id,
      generation: connection.generation,
    };
    await this.env.STATE_DB.prepare(
      `INSERT INTO sync_jobs(
         owner_id, job_id, device_id, operation, scope_json, request_json,
         response_json, idempotency_key, state, maximum_response_bytes,
         expires_at, created_at, updated_at, completed_at
       ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'queued', ?, ?, ?, ?, NULL)`,
    )
      .bind(
        this.principal.ownerId,
        jobId,
        connection.device_id,
        operation,
        canonicalJson(scope),
        canonicalJson(request),
        jobId,
        maximumResponseBytes,
        expiresAt,
        createdAt,
        createdAt,
      )
      .run();
    const job: SyncJobRequest = {
      jobId,
      operation,
      scope,
      request,
      maximumResponseBytes,
      expiresAt,
    };
    const brokerId = this.env.SYNC_BROKERS.idFromName(
      `${this.principal.ownerId}:${connection.device_id}`,
    );
    const broker = this.env.SYNC_BROKERS.get(brokerId);
    const remote = await broker.fetch("https://sync.internal/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ownerId: this.principal.ownerId,
        deviceId: connection.device_id,
        waitMs,
        job,
      }),
    });
    const payload = (await remote.json()) as {
      ok: boolean;
      result?: BrokerResult;
      error?: {
        code: string;
        message: string;
        details?: Record<string, unknown>;
      };
    };
    if (!remote.ok || !payload.ok || !payload.result) {
      throw new ContextError(
        payload.error?.code ?? "sync_unavailable",
        payload.error?.message ?? "Sync service is unavailable",
        remote.status,
        payload.error?.details ?? {},
      );
    }
    return payload.result;
  }

  private unwrapJob(result: BrokerResult): Record<string, unknown> {
    if (result.state === "succeeded") return result.response ?? {};
    if (result.state === "failed") {
      throw new ContextError(
        typeof result.response?.code === "string"
          ? result.response.code
          : "sync_job_failed",
        typeof result.response?.message === "string"
          ? result.response.message
          : "the local Sync operation failed",
        409,
      );
    }
    return {
      pending: true,
      job_id: result.jobId,
      state: result.state,
      device_online: result.deviceOnline,
      message:
        "The Sync operation is queued for the owner's app. Inspect the job after it reconnects or finishes.",
    };
  }

  async fileList(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusFileListSchema.parse(raw);
    const connection = await this.workConnection(
      input.space_id,
      input.connection_id,
    );
    return this.unwrapJob(
      await this.runJob(
        connection,
        "work.file.list",
        input as Record<string, unknown>,
        2 * 1024 * 1024,
      ),
    );
  }

  async fileRead(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusFileReadSchema.parse(raw);
    if (input.read_ref != null) {
      const reference = decodeReadReference(input.read_ref);
      if (reference.spaceId !== input.space_id) {
        throw new ContextError(
          "invalid_reference",
          "read_ref belongs to another Space",
        );
      }
      const connections = await this.sourceConnections(
        input.space_id,
        reference.connectionId,
      );
      const connection = connections[0]!;
      if (connection.corpus_id !== reference.corpusId) {
        throw new ContextError(
          "invalid_reference",
          "read_ref Source binding has changed",
          409,
        );
      }
      const result = await this.callShard(reference.corpusId, "/units/read", {
        unitIds: [reference.unitId],
        neighborSpan: input.neighbor_span,
        includeStructureContext: input.include_structure_context,
      });
      const units = Array.isArray(result.units) ? result.units : [];
      const text = units
        .map((unit) =>
          unit && typeof unit === "object" && "untrusted_content" in unit
            ? String((unit as Record<string, unknown>).untrusted_content)
            : "",
        )
        .join("\n\n");
      const selected = text.slice(
        input.start_char,
        input.start_char + input.max_chars,
      );
      const hasMore = input.start_char + selected.length < text.length;
      return {
        ...result,
        untrusted_content: selected,
        start_char: input.start_char,
        returned_chars: selected.length,
        has_more: hasMore,
        next_start_char: hasMore ? input.start_char + selected.length : null,
      };
    }
    const connection = await this.workConnection(
      input.space_id,
      input.connection_id,
    );
    return this.unwrapJob(
      await this.runJob(
        connection,
        "work.file.read",
        input as Record<string, unknown>,
        16 * 1024 * 1024,
      ),
    );
  }

  async fileWrite(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusFileWriteSchema.parse(raw);
    const connection = await this.workConnection(
      input.space_id,
      input.connection_id,
      true,
    );
    const result = this.unwrapJob(
      await this.runJob(
        connection,
        "work.file.write",
        input as Record<string, unknown>,
        2 * 1024 * 1024,
      ),
    );
    if (input.make_current && result.pending !== true) {
      await this.storeCurrentFile(
        input.space_id,
        connection.connection_id,
        result,
        input.relative_path,
      );
    }
    return result;
  }

  async fileDelete(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusFileDeleteSchema.parse(raw);
    if (!input.confirm_delete) {
      throw new ContextError(
        "delete_not_confirmed",
        "confirm_delete must be true",
      );
    }
    const connection = await this.workConnection(
      input.space_id,
      input.connection_id,
      true,
    );
    return this.unwrapJob(
      await this.runJob(
        connection,
        "work.file.delete",
        input as Record<string, unknown>,
        1024 * 1024,
      ),
    );
  }

  async fileSelectCurrent(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusFileSelectSchema.parse(raw);
    const connection = await this.workConnection(
      input.space_id,
      input.connection_id,
      true,
    );
    const result = this.unwrapJob(
      await this.runJob(
        connection,
        "work.file.select_current",
        input as Record<string, unknown>,
        1024 * 1024,
      ),
    );
    if (result.pending !== true) {
      await this.storeCurrentFile(
        input.space_id,
        connection.connection_id,
        result,
        input.relative_path,
      );
    }
    return result;
  }

  async fileRestore(raw: unknown): Promise<Record<string, unknown>> {
    const input = corpusFileRestoreSchema.parse(raw);
    const connection = await this.workConnection(
      input.space_id,
      input.connection_id,
      true,
    );
    return this.unwrapJob(
      await this.runJob(
        connection,
        "work.file.restore",
        input as Record<string, unknown>,
        2 * 1024 * 1024,
      ),
    );
  }

  private async storeCurrentFile(
    spaceId: string,
    connectionId: string,
    result: Record<string, unknown>,
    fallbackPath: string,
  ): Promise<void> {
    const path =
      typeof result.relative_path === "string"
        ? result.relative_path
        : fallbackPath.normalize("NFC");
    await this.env.STATE_DB.prepare(
      `INSERT INTO corpus_current_files(
         owner_id, space_id, connection_id, relative_path, version_token,
         state, reason, residency_state, size, modified_ns, updated_at
       ) VALUES (?, ?, ?, ?, ?, 'ready', NULL, ?, ?, ?, ?)
       ON CONFLICT(owner_id, space_id, connection_id) DO UPDATE SET
         relative_path = excluded.relative_path,
         version_token = excluded.version_token,
         state = excluded.state,
         reason = NULL,
         residency_state = excluded.residency_state,
         size = excluded.size,
         modified_ns = excluded.modified_ns,
         updated_at = excluded.updated_at`,
    )
      .bind(
        this.principal.ownerId,
        spaceId,
        connectionId,
        path,
        typeof result.version_token === "string" ? result.version_token : null,
        typeof result.residency_state === "string"
          ? result.residency_state
          : null,
        typeof result.size === "number" ? result.size : null,
        typeof result.modified_ns === "string" ? result.modified_ns : null,
        nowIso(),
      )
      .run();
  }

  async jobStatus(raw: unknown): Promise<Record<string, unknown>> {
    const { job_id: jobId } = corpusJobStatusSchema.parse(raw);
    const row = await this.env.STATE_DB.prepare(
      `SELECT job_id, operation, state, response_json, expires_at,
              created_at, updated_at, completed_at
       FROM sync_jobs WHERE owner_id = ? AND job_id = ?`,
    )
      .bind(this.principal.ownerId, jobId)
      .first<{
        job_id: string;
        operation: string;
        state: string;
        response_json: string | null;
        expires_at: string;
        created_at: string;
        updated_at: string;
        completed_at: string | null;
      }>();
    if (!row)
      throw new ContextError("job_not_found", "Sync job was not found", 404);
    if (
      (row.state === "queued" || row.state === "dispatched") &&
      Date.parse(row.expires_at) <= Date.now()
    ) {
      const expiredAt = nowIso();
      await this.env.STATE_DB.prepare(
        `UPDATE sync_jobs SET state = 'expired', updated_at = ?
         WHERE owner_id = ? AND job_id = ?
           AND state IN ('queued', 'dispatched') AND expires_at <= ?`,
      )
        .bind(expiredAt, this.principal.ownerId, jobId, expiredAt)
        .run();
      row.state = "expired";
      row.updated_at = expiredAt;
    }
    return {
      job_id: row.job_id,
      operation: row.operation,
      state: row.state,
      response: row.response_json ? parseObject(row.response_json) : null,
      expires_at: row.expires_at,
      created_at: row.created_at,
      updated_at: row.updated_at,
      completed_at: row.completed_at,
    };
  }
}
