import { z } from "zod/v4";
import { canonicalJson, contentSha256, nowIso } from "./canonical";
import { ContextError } from "./errors";
import { guard, guardedBatch } from "./management-db";
import type { Env, Principal, SyncJobRequest } from "./types";
const id = z
  .string()
  .min(1)
  .max(200)
  .regex(/^[A-Za-z0-9][A-Za-z0-9._@-]*$/);
export const registrationsListSchema = z.object({ space_id: id }).strict();
export const connectionDetachSchema = z
  .object({
    space_id: id,
    connection_id: id,
    expected_generation: z.number().int().positive(),
    idempotency_key: z.string().min(16).max(160),
  })
  .strict();
export const workspaceDetachSchema = z
  .object({
    space_id: id,
    host_id: id,
    workspace_id: id,
    device_id: id,
    expected_version: z.number().int().positive(),
    idempotency_key: z.string().min(16).max(160),
  })
  .strict();
type Retirement = {
  kind: "connection" | "workspace";
  registration_key: string;
  space_id: string;
  device_id: string;
  expected_version: number;
  job_id: string;
  state: string;
  fingerprint: string;
  result_json: string;
  request_key: string;
};
export class RegistrationManagementService {
  constructor(
    private env: Env,
    private principal: Principal,
  ) {}
  private async readableSpace(space: string, write = false) {
    if (!this.principal.scopes.has(write ? "corpus.write" : "corpus.read"))
      throw new ContextError(
        "insufficient_scope",
        "The required Corpus scope is missing",
        403,
      );
    if (
      !(await this.env.STATE_DB.prepare(
        "SELECT 1 FROM corpus_spaces WHERE owner_id=? AND space_id=? AND access_scope='remote_allowed'",
      )
        .bind(this.principal.ownerId, space)
        .first())
    )
      throw new ContextError("space_not_found", "Space does not exist", 404);
  }
  async list(raw: unknown): Promise<Record<string, unknown>> {
    const { space_id } = registrationsListSchema.parse(raw);
    await this.readableSpace(space_id);
    const db = this.env.STATE_DB,
      owner = this.principal.ownerId;
    const connections = await db
      .prepare(
        "SELECT connection_id,device_id,generation,configuration_state FROM corpus_connections WHERE owner_id=? AND space_id=? AND access_scope='remote_allowed' ORDER BY connection_id",
      )
      .bind(owner, space_id)
      .all();
    const workspaces = await db
      .prepare(
        "SELECT host_id,workspace_id,version FROM corpus_workspace_bindings WHERE owner_id=? AND space_id=? ORDER BY host_id,workspace_id",
      )
      .bind(owner, space_id)
      .all();
    const detachments = await db
      .prepare(
        "SELECT kind,registration_key,device_id,expected_version,state,result_json,job_id FROM corpus_registration_detachments WHERE owner_id=? AND space_id=?",
      )
      .bind(owner, space_id)
      .all();
    const devices = await db
      .prepare(
        "SELECT device_id,display_name FROM sync_devices WHERE owner_id=? AND status='active' AND EXISTS(SELECT 1 FROM json_each(capabilities_json) WHERE value='registration.detach')",
      )
      .bind(owner)
      .all();
    return {
      devices: devices.results,
      connections: connections.results,
      workspaces: workspaces.results,
      detachments: detachments.results.map(({ result_json, ...row }) => ({
        ...row,
        result: JSON.parse(String(result_json)),
      })),
    };
  }
  async detach(
    kind: "connection" | "workspace",
    raw: unknown,
  ): Promise<Record<string, unknown>> {
    if (this.env.CORPUS_MANAGEMENT_WRITE_ENABLED !== "true")
      throw new ContextError(
        "management_not_enabled",
        "Management writes are disabled",
        503,
      );
    const input =
      kind === "connection"
        ? connectionDetachSchema.parse(raw)
        : workspaceDetachSchema.parse(raw);
    const db = this.env.STATE_DB,
      owner = this.principal.ownerId,
      fingerprint = await contentSha256({ kind, input });
    await this.readableSpace(input.space_id, true);
    const previous = await db
      .prepare(
        "SELECT * FROM corpus_registration_detachments WHERE owner_id=? AND request_key=?",
      )
      .bind(owner, input.idempotency_key)
      .first<Retirement>();
    if (previous) {
      if (previous.fingerprint !== fingerprint)
        throw new ContextError(
          "request_key_conflict",
          "Request key belongs to another detach operation",
          409,
        );
      return this.dispatch(previous);
    }
    let registrationKey: string,
      device: string,
      expected: number,
      scope: Record<string, unknown>,
      identityGuard: D1PreparedStatement;
    if ("connection_id" in input) {
      const row = await db
        .prepare(
          "SELECT device_id,generation FROM corpus_connections WHERE owner_id=? AND space_id=? AND connection_id=? AND configuration_state='ready' AND access_scope='remote_allowed'",
        )
        .bind(owner, input.space_id, input.connection_id)
        .first<{ device_id: string | null; generation: number }>();
      if (!row?.device_id)
        throw new ContextError(
          "sync_registration_required",
          "An owner Sync registration is required; remote metadata alone cannot be detached",
          409,
        );
      device = row.device_id;
      expected = input.expected_generation;
      registrationKey = `${input.space_id}:${input.connection_id}`;
      scope = {
        spaceId: input.space_id,
        connectionId: input.connection_id,
        generation: expected,
      };
      identityGuard = guard(
        db,
        "EXISTS(SELECT 1 FROM corpus_connections WHERE owner_id=? AND space_id=? AND connection_id=? AND generation=? AND device_id=? AND configuration_state='ready' AND access_scope='remote_allowed')",
        [owner, input.space_id, input.connection_id, expected, device],
      );
    } else {
      device = input.device_id;
      expected = input.expected_version;
      registrationKey = `${input.host_id}:${input.workspace_id}`;
      scope = {
        spaceId: input.space_id,
        hostId: input.host_id,
        workspaceId: input.workspace_id,
        generation: expected,
      };
      identityGuard = guard(
        db,
        "EXISTS(SELECT 1 FROM corpus_workspace_bindings WHERE owner_id=? AND host_id=? AND workspace_id=? AND space_id=? AND version=?)",
        [owner, input.host_id, input.workspace_id, input.space_id, expected],
      );
    }
    const capable = await db
      .prepare(
        "SELECT 1 FROM sync_devices WHERE owner_id=? AND device_id=? AND status='active' AND EXISTS(SELECT 1 FROM json_each(capabilities_json) WHERE value='registration.detach')",
      )
      .bind(owner, device)
      .first();
    if (!capable)
      throw new ContextError(
        "sync_upgrade_required",
        "This Sync device has not advertised safe registration management; update it before detaching",
        409,
      );
    const job: SyncJobRequest = {
      jobId: `job_${crypto.randomUUID().replaceAll("-", "")}`,
      operation: "registration.detach",
      scope,
      request: {
        kind,
        registration_key: registrationKey,
        space_id: input.space_id,
        expected_version: expected,
      },
      maximumResponseBytes: 65536,
      expiresAt: "9999-12-31T00:00:00.000Z",
    };
    const now = nowIso(),
      pending = {
        state: "pending",
        job_id: job.jobId,
        filesystem_changed: false,
        reason: "waiting_for_sync_acknowledgement",
      };
    await guardedBatch(
      db,
      [
        identityGuard,
        guard(
          db,
          "EXISTS(SELECT 1 FROM corpus_spaces WHERE owner_id=? AND space_id=? AND access_scope='remote_allowed')",
          [owner, input.space_id],
        ),
        guard(
          db,
          "NOT EXISTS(SELECT 1 FROM corpus_registration_detachments WHERE owner_id=? AND kind=? AND registration_key=?)",
          [owner, kind, registrationKey],
        ),
      ],
      [
        db
          .prepare(
            "INSERT INTO corpus_registration_detachments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
          )
          .bind(
            owner,
            kind,
            registrationKey,
            input.space_id,
            device,
            expected,
            input.idempotency_key,
            fingerprint,
            job.jobId,
            "pending",
            canonicalJson(pending),
            now,
            now,
          ),
        ...("connection_id" in input
          ? [
              db
                .prepare(
                  "UPDATE corpus_connections SET configuration_state='detaching',updated_at=? WHERE owner_id=? AND space_id=? AND connection_id=?",
                )
                .bind(now, owner, input.space_id, input.connection_id),
            ]
          : []),
        db
          .prepare(
            "INSERT INTO sync_jobs(owner_id,job_id,device_id,operation,scope_json,request_json,idempotency_key,state,maximum_response_bytes,expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'queued',?,?,?,?)",
          )
          .bind(
            owner,
            job.jobId,
            device,
            job.operation,
            canonicalJson(job.scope),
            canonicalJson(job.request),
            `detach:${input.idempotency_key}`,
            job.maximumResponseBytes,
            job.expiresAt,
            now,
            now,
          ),
        db
          .prepare(
            "INSERT OR IGNORE INTO corpus_management_owners(owner_id) VALUES(?)",
          )
          .bind(owner),
      ],
    );
    return this.dispatch({
      kind,
      registration_key: registrationKey,
      device_id: device,
      expected_version: expected,
      space_id: input.space_id,
      job_id: job.jobId,
      state: "pending",
      result_json: canonicalJson(pending),
      request_key: input.idempotency_key,
      fingerprint,
    });
  }
  private async dispatch(row: Retirement): Promise<Record<string, unknown>> {
    if (row.state !== "pending") return JSON.parse(row.result_json);
    const job = await this.env.STATE_DB.prepare(
      "SELECT * FROM sync_jobs WHERE owner_id=? AND job_id=?",
    )
      .bind(this.principal.ownerId, row.job_id)
      .first<Record<string, unknown>>();
    if (!job || !["queued", "dispatched"].includes(String(job.state)))
      return {
        state: "blocked",
        job_id: row.job_id,
        reason: "sync_outcome_requires_review",
        filesystem_changed: false,
      };
    try {
      const broker = this.env.SYNC_BROKERS.get(
        this.env.SYNC_BROKERS.idFromName(
          `${this.principal.ownerId}:${row.device_id}`,
        ),
      );
      await broker.fetch("https://sync.internal/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: canonicalJson({
          ownerId: this.principal.ownerId,
          deviceId: row.device_id,
          waitMs: 0,
          job: {
            jobId: row.job_id,
            operation: job.operation,
            scope: JSON.parse(String(job.scope_json)),
            request: JSON.parse(String(job.request_json)),
            maximumResponseBytes: job.maximum_response_bytes,
            expiresAt: job.expires_at,
          },
        }),
      });
    } catch {
      /* A dispatch failure is not completion or permission to remove remote metadata. */
    }
    return JSON.parse(row.result_json);
  }
}
/** Runs only after the authenticated owning Sync device reports durable retirement. */
export async function finishRegistrationDetach(
  env: Env,
  owner: string,
  device: string,
  jobId: string,
  succeeded: boolean,
  payload: Record<string, unknown>,
) {
  const db = env.STATE_DB;
  const row = await db
    .prepare(
      "SELECT * FROM corpus_registration_detachments WHERE owner_id=? AND device_id=? AND job_id=?",
    )
    .bind(owner, device, jobId)
    .first<Retirement>();
  if (!row || row.state === "detached") return;
  const exact =
    succeeded &&
    payload.state === "detached" &&
    payload.registration_key === row.registration_key &&
    payload.generation === row.expected_version &&
    payload.filesystem_changed === false &&
    payload.local_jobs_drained === true;
  const result = exact
    ? {
        state: "detached",
        registration_key: row.registration_key,
        job_id: jobId,
        filesystem_changed: false,
      }
    : {
        state: "blocked",
        job_id: jobId,
        reason:
          typeof payload.code === "string"
            ? payload.code
            : "sync_acknowledgement_incomplete",
      };
  const scopeRow = await db
    .prepare("SELECT scope_json FROM sync_jobs WHERE owner_id=? AND job_id=?")
    .bind(owner, jobId)
    .first<{ scope_json: string }>();
  if (!scopeRow)
    throw new ContextError(
      "sync_outcome_unknown",
      "Detach job disappeared before acknowledgement",
      409,
    );
  const scope = JSON.parse(scopeRow.scope_json);
  await guardedBatch(
    db,
    [
      guard(
        db,
        "EXISTS(SELECT 1 FROM corpus_registration_detachments WHERE owner_id=? AND job_id=? AND state IN ('pending','blocked'))",
        [owner, jobId],
      ),
      ...(exact && row.kind === "connection"
        ? [
            guard(
              db,
              "EXISTS(SELECT 1 FROM corpus_connections WHERE owner_id=? AND space_id=? AND connection_id=? AND generation=? AND device_id=? AND configuration_state='detaching')",
              [
                owner,
                row.space_id,
                scope.connectionId,
                row.expected_version,
                device,
              ],
            ),
          ]
        : []),
      ...(exact && row.kind === "workspace"
        ? [
            guard(
              db,
              "EXISTS(SELECT 1 FROM corpus_workspace_bindings WHERE owner_id=? AND host_id=? AND workspace_id=? AND space_id=? AND version=?)",
              [
                owner,
                scope.hostId,
                scope.workspaceId,
                row.space_id,
                row.expected_version,
              ],
            ),
          ]
        : []),
    ],
    [
      ...(exact && row.kind === "connection"
        ? [
            db
              .prepare(
                "UPDATE corpus_connections SET configuration_state='detached',updated_at=? WHERE owner_id=? AND space_id=? AND connection_id=?",
              )
              .bind(nowIso(), owner, row.space_id, scope.connectionId),
          ]
        : []),
      ...(exact && row.kind === "workspace"
        ? [
            db
              .prepare(
                "DELETE FROM corpus_workspace_bindings WHERE owner_id=? AND host_id=? AND workspace_id=?",
              )
              .bind(owner, scope.hostId, scope.workspaceId),
          ]
        : []),
      db
        .prepare(
          "UPDATE corpus_registration_detachments SET state=?,result_json=?,updated_at=? WHERE owner_id=? AND job_id=?",
        )
        .bind(
          exact ? "detached" : "blocked",
          canonicalJson(result),
          nowIso(),
          owner,
          jobId,
        ),
    ],
  );
  return result;
}
