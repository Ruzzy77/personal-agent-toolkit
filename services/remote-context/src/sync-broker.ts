import { canonicalJson, nowIso } from "./canonical";
import { ContextError, asContextError } from "./errors";
import { syncHelloSchema, syncResultSchema } from "./schemas";
import type { Env, SyncJobRequest } from "./types";

interface SocketAttachment {
  ownerId: string;
  deviceId: string;
  ready: boolean;
  connectedAt: string;
}

interface PendingRequest {
  resolve: (response: Response) => void;
  timeout: ReturnType<typeof setTimeout>;
}

interface ExecuteInput {
  ownerId: string;
  deviceId: string;
  waitMs: number;
  job: SyncJobRequest;
}

function response(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

function errorResponse(error: unknown): Response {
  const normalized = asContextError(error);
  return response(
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

function attachment(socket: WebSocket): SocketAttachment {
  const value = socket.deserializeAttachment() as SocketAttachment | null;
  if (!value?.ownerId || !value.deviceId) {
    throw new ContextError("invalid_sync_session", "Sync session metadata is invalid", 500);
  }
  return value;
}

function parseExecuteInput(value: unknown): ExecuteInput {
  if (value === null || Array.isArray(value) || typeof value !== "object") {
    throw new ContextError("invalid_job", "Sync job request is invalid");
  }
  const input = value as Record<string, unknown>;
  const job = input.job as Record<string, unknown> | undefined;
  if (
    typeof input.ownerId !== "string" ||
    typeof input.deviceId !== "string" ||
    !Number.isInteger(input.waitMs) ||
    Number(input.waitMs) < 0 ||
    Number(input.waitMs) > 25_000 ||
    !job ||
    typeof job.jobId !== "string" ||
    !/^job_[0-9a-f]{32}$/.test(job.jobId) ||
    typeof job.operation !== "string" ||
    job.scope === null ||
    Array.isArray(job.scope) ||
    typeof job.scope !== "object" ||
    job.request === null ||
    Array.isArray(job.request) ||
    typeof job.request !== "object" ||
    !Number.isInteger(job.maximumResponseBytes) ||
    Number(job.maximumResponseBytes) < 1 ||
    Number(job.maximumResponseBytes) > 16 * 1024 * 1024 ||
    typeof job.expiresAt !== "string" ||
    !Number.isFinite(Date.parse(job.expiresAt))
  ) {
    throw new ContextError("invalid_job", "Sync job request is invalid");
  }
  return {
    ownerId: input.ownerId,
    deviceId: input.deviceId,
    waitMs: Number(input.waitMs),
    job: job as unknown as SyncJobRequest,
  };
}

export class SyncBroker {
  private readonly pending = new Map<string, PendingRequest>();

  constructor(
    private readonly state: DurableObjectState,
    private readonly env: Env,
  ) {}

  private sockets(ownerId: string, deviceId: string, ready = true): WebSocket[] {
    return this.state
      .getWebSockets()
      .filter((socket) => {
        try {
          const value = attachment(socket);
          return (
            value.ownerId === ownerId &&
            value.deviceId === deviceId &&
            (!ready || value.ready)
          );
        } catch {
          return false;
        }
      });
  }

  private async upsertDevice(
    ownerId: string,
    deviceId: string,
    displayName: string,
    capabilities: string[],
  ): Promise<void> {
    const now = nowIso();
    await this.env.STATE_DB.prepare(
      `INSERT INTO sync_devices(
         owner_id, device_id, display_name, credential_id, status,
         capabilities_json, last_seen_at, created_at, updated_at
       ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
       ON CONFLICT(owner_id, device_id) DO UPDATE SET
         display_name = excluded.display_name,
         capabilities_json = excluded.capabilities_json,
         last_seen_at = excluded.last_seen_at,
         updated_at = excluded.updated_at`,
    )
      .bind(
        ownerId,
        deviceId,
        displayName,
        `environment:${deviceId}`,
        canonicalJson(capabilities),
        now,
        now,
        now,
      )
      .run();
  }

  private async dispatchQueued(socket: WebSocket, ownerId: string, deviceId: string) {
    const now = nowIso();
    await this.env.STATE_DB.prepare(
      `UPDATE sync_jobs SET state = 'expired', updated_at = ?
       WHERE owner_id = ? AND device_id = ?
         AND state IN ('queued', 'dispatched') AND expires_at <= ?`,
    )
      .bind(now, ownerId, deviceId, now)
      .run();
    const queued = await this.env.STATE_DB.prepare(
      `SELECT job_id, operation, scope_json, request_json,
              maximum_response_bytes, expires_at
       FROM sync_jobs
       WHERE owner_id = ? AND device_id = ? AND state IN ('queued', 'dispatched')
         AND expires_at > ?
       ORDER BY created_at LIMIT 20`,
    )
      .bind(ownerId, deviceId, now)
      .all<{
        job_id: string;
        operation: string;
        scope_json: string;
        request_json: string;
        maximum_response_bytes: number;
        expires_at: string;
      }>();
    for (const row of queued.results) {
      socket.send(
        canonicalJson({
          type: "job",
          protocolVersion: 1,
          jobId: row.job_id,
          operation: row.operation,
          scope: JSON.parse(row.scope_json),
          request: JSON.parse(row.request_json),
          maximumResponseBytes: row.maximum_response_bytes,
          expiresAt: row.expires_at,
        }),
      );
      await this.env.STATE_DB.prepare(
        `UPDATE sync_jobs SET state = 'dispatched', updated_at = ?
         WHERE owner_id = ? AND job_id = ? AND state IN ('queued', 'dispatched')`,
      )
        .bind(nowIso(), ownerId, row.job_id)
        .run();
    }
  }

  private async connect(request: Request): Promise<Response> {
    if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
      throw new ContextError("upgrade_required", "Sync connect requires WebSocket", 426);
    }
    const ownerId = request.headers.get("X-Owner-Id");
    const deviceId = request.headers.get("X-Device-Id");
    if (!ownerId || !deviceId) {
      throw new ContextError("invalid_sync_session", "Sync identity is missing", 401);
    }
    for (const existing of this.sockets(ownerId, deviceId, false)) {
      existing.close(4001, "replaced by a newer Sync connection");
    }
    const pair = new WebSocketPair();
    const client = pair[0];
    const server = pair[1];
    server.serializeAttachment({
      ownerId,
      deviceId,
      ready: false,
      connectedAt: nowIso(),
    } satisfies SocketAttachment);
    this.state.acceptWebSocket(server, [deviceId]);
    return new Response(null, { status: 101, webSocket: client });
  }

  private async execute(value: unknown): Promise<Response> {
    const input = parseExecuteInput(value);
    const device = await this.env.STATE_DB.prepare(
      `SELECT status FROM sync_devices WHERE owner_id = ? AND device_id = ?`,
    )
      .bind(input.ownerId, input.deviceId)
      .first<{ status: string }>();
    if (device?.status === "revoked") {
      throw new ContextError("device_revoked", "Sync device has been revoked", 403);
    }
    const socket = this.sockets(input.ownerId, input.deviceId)[0];
    if (!socket) {
      return response(
        {
          ok: true,
          result: {
            jobId: input.job.jobId,
            state: "queued",
            deviceOnline: false,
          },
        },
        202,
      );
    }
    socket.send(canonicalJson({ type: "job", protocolVersion: 1, ...input.job }));
    await this.env.STATE_DB.prepare(
      `UPDATE sync_jobs SET state = 'dispatched', updated_at = ?
       WHERE owner_id = ? AND job_id = ? AND state = 'queued'`,
    )
      .bind(nowIso(), input.ownerId, input.job.jobId)
      .run();
    if (input.waitMs === 0) {
      return response(
        {
          ok: true,
          result: { jobId: input.job.jobId, state: "dispatched", deviceOnline: true },
        },
        202,
      );
    }
    return new Promise<Response>((resolve) => {
      const timeout = setTimeout(() => {
        this.pending.delete(input.job.jobId);
        resolve(
          response(
            {
              ok: true,
              result: { jobId: input.job.jobId, state: "dispatched", deviceOnline: true },
            },
            202,
          ),
        );
      }, input.waitMs);
      this.pending.set(input.job.jobId, { resolve, timeout });
    });
  }

  async fetch(request: Request): Promise<Response> {
    try {
      const path = new URL(request.url).pathname;
      if (path === "/connect" && request.method === "GET") return await this.connect(request);
      if (path === "/execute" && request.method === "POST") {
        return await this.execute(await request.json());
      }
      if (path === "/status" && request.method === "POST") {
        const body = (await request.json()) as { ownerId?: unknown; deviceId?: unknown };
        if (typeof body.ownerId !== "string" || typeof body.deviceId !== "string") {
          throw new ContextError("invalid_status", "Sync status request is invalid");
        }
        return response({
          ok: true,
          result: {
            deviceId: body.deviceId,
            online: this.sockets(body.ownerId, body.deviceId).length > 0,
          },
        });
      }
      throw new ContextError("not_found", "Sync broker route was not found", 404);
    } catch (error) {
      return errorResponse(error);
    }
  }

  async webSocketMessage(socket: WebSocket, message: string | ArrayBuffer): Promise<void> {
    const metadata = attachment(socket);
    let value: unknown;
    try {
      const text = typeof message === "string" ? message : new TextDecoder().decode(message);
      if (new TextEncoder().encode(text).length > 16 * 1024 * 1024) {
        throw new ContextError("message_too_large", "Sync message exceeds the limit", 413);
      }
      value = JSON.parse(text);
    } catch (error) {
      socket.close(4002, error instanceof ContextError ? error.code : "invalid JSON");
      return;
    }

    if (!metadata.ready) {
      const hello = syncHelloSchema.parse(value);
      await this.upsertDevice(
        metadata.ownerId,
        metadata.deviceId,
        hello.displayName,
        hello.capabilities,
      );
      socket.serializeAttachment({ ...metadata, ready: true } satisfies SocketAttachment);
      socket.send(
        canonicalJson({
          type: "hello_ack",
          protocolVersion: 1,
          deviceId: metadata.deviceId,
          serverTime: nowIso(),
        }),
      );
      await this.dispatchQueued(socket, metadata.ownerId, metadata.deviceId);
      return;
    }

    const result = syncResultSchema.parse(value);
    const row = await this.env.STATE_DB.prepare(
      `SELECT maximum_response_bytes FROM sync_jobs
       WHERE owner_id = ? AND device_id = ? AND job_id = ?
         AND state IN ('queued', 'dispatched')`,
    )
      .bind(metadata.ownerId, metadata.deviceId, result.jobId)
      .first<{ maximum_response_bytes: number }>();
    if (!row) {
      socket.send(canonicalJson({ type: "job_ack", jobId: result.jobId, accepted: false }));
      return;
    }
    const payload = canonicalJson(result.ok ? result.result : result.error);
    if (new TextEncoder().encode(payload).length > row.maximum_response_bytes) {
      await this.completeJob(metadata, result.jobId, false, {
        code: "response_too_large",
        message: "Sync job response exceeded the declared limit",
      });
    } else {
      await this.completeJob(
        metadata,
        result.jobId,
        result.ok,
        result.ok ? result.result! : result.error!,
      );
    }
    socket.send(canonicalJson({ type: "job_ack", jobId: result.jobId, accepted: true }));
  }

  private async completeJob(
    metadata: SocketAttachment,
    jobId: string,
    succeeded: boolean,
    payload: Record<string, unknown>,
  ): Promise<void> {
    const now = nowIso();
    await this.env.STATE_DB.prepare(
      `UPDATE sync_jobs
       SET state = ?, response_json = ?, updated_at = ?, completed_at = ?
       WHERE owner_id = ? AND device_id = ? AND job_id = ?
         AND state IN ('queued', 'dispatched')`,
    )
      .bind(
        succeeded ? "succeeded" : "failed",
        canonicalJson(payload),
        now,
        now,
        metadata.ownerId,
        metadata.deviceId,
        jobId,
      )
      .run();
    const pending = this.pending.get(jobId);
    if (pending) {
      clearTimeout(pending.timeout);
      this.pending.delete(jobId);
      pending.resolve(
        response({
          ok: true,
          result: {
            jobId,
            state: succeeded ? "succeeded" : "failed",
            response: payload,
            deviceOnline: true,
          },
        }),
      );
    }
  }

  async webSocketClose(
    socket: WebSocket,
    _code: number,
    _reason: string,
    _wasClean: boolean,
  ): Promise<void> {
    try {
      const metadata = attachment(socket);
      await this.env.STATE_DB.prepare(
        `UPDATE sync_devices SET last_seen_at = ?, updated_at = ?
         WHERE owner_id = ? AND device_id = ?`,
      )
        .bind(nowIso(), nowIso(), metadata.ownerId, metadata.deviceId)
        .run();
    } catch {
      // A malformed or already-closed socket has no durable state to update.
    }
  }

  webSocketError(socket: WebSocket): void {
    try {
      socket.close(1011, "Sync connection error");
    } catch {
      // The runtime may already have closed it.
    }
  }
}
