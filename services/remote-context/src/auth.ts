import { ContextError } from "./errors";
import type { Env, Principal, ResourceKind } from "./types";

const RESOURCE_SCOPES: Record<ResourceKind, readonly string[]> = {
  sense: ["sense.read", "sense.write"],
  corpus: ["corpus.read", "corpus.write", "corpus.sync"],
  hypes: ["hypes.read", "hypes.write"],
};

function bearerToken(request: Request): string | null {
  const value = request.headers.get("Authorization");
  if (!value) return null;
  return /^Bearer ([^\s]+)$/i.exec(value)?.[1] ?? null;
}

function constantTimeEqual(left: string, right: string): boolean {
  const encoder = new TextEncoder();
  const a = encoder.encode(left);
  const b = encoder.encode(right);
  const length = Math.max(a.length, b.length);
  let difference = a.length ^ b.length;
  for (let index = 0; index < length; index += 1) {
    difference |= (a[index] ?? 0) ^ (b[index] ?? 0);
  }
  return difference === 0;
}

export function resourceUrl(env: Env, kind: ResourceKind): string {
  if (kind === "sense") return env.SENSE_RESOURCE;
  if (kind === "corpus") return env.CORPUS_RESOURCE;
  return env.HYPES_RESOURCE;
}

export function supportedScopes(kind: ResourceKind): readonly string[] {
  return RESOURCE_SCOPES[kind];
}

export async function authenticateMcp(
  request: Request,
  env: Env,
  kind: ResourceKind,
  requiredScopes: readonly string[],
): Promise<Principal> {
  const token = bearerToken(request);
  if (!token) {
    throw new ContextError("invalid_token", "bearer token is required", 401);
  }
  if (!env.AUTH_SERVICE) {
    throw new ContextError("invalid_token", "token is not recognized", 401);
  }
  for (const scope of requiredScopes) {
    const validation = await env.AUTH_SERVICE.validateAccessToken(
      token,
      resourceUrl(env, kind),
      [scope],
    );
    if (validation.ok) {
      return {
        ownerId: validation.owner.userId,
        scopes: new Set(validation.owner.scopes),
        clientId: validation.owner.clientId,
        auth: "oauth",
      };
    }
  }
  throw new ContextError(
    "insufficient_scope",
    `the token does not grant the required ${kind} scope`,
    403,
  );
}

export async function authenticateSync(request: Request, env: Env): Promise<Principal> {
  const token = bearerToken(request);
  const deviceId = request.headers.get("X-Personal-Agent-Device");
  let expectedToken: string | undefined;
  if (deviceId && env.SYNC_DEVICE_TOKENS_JSON) {
    try {
      const configured = JSON.parse(env.SYNC_DEVICE_TOKENS_JSON) as Record<string, unknown>;
      const candidate = configured[deviceId];
      if (typeof candidate === "string") expectedToken = candidate;
    } catch {
      throw new ContextError(
        "sync_configuration_error",
        "Sync device credentials are misconfigured",
        500,
      );
    }
  }
  if (
    !expectedToken &&
    deviceId &&
    env.SYNC_DEVICE_TOKEN &&
    (!env.SYNC_DEVICE_ID || env.SYNC_DEVICE_ID === deviceId)
  ) {
    expectedToken = env.SYNC_DEVICE_TOKEN;
  }
  if (
    !token ||
    !deviceId ||
    !expectedToken ||
    !env.SYNC_OWNER_ID ||
    !constantTimeEqual(token, expectedToken)
  ) {
    throw new ContextError(
      "invalid_sync_credential",
      "a valid Sync device credential is required",
      401,
    );
  }
  if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(deviceId)) {
    throw new ContextError("invalid_device_id", "device id is invalid", 400);
  }
  const device = await env.STATE_DB.prepare(
    "SELECT status FROM sync_devices WHERE owner_id = ? AND device_id = ?",
  )
    .bind(env.SYNC_OWNER_ID, deviceId)
    .first<{ status: string }>();
  if (device?.status === "revoked") {
    throw new ContextError("device_revoked", "Sync device has been revoked", 403);
  }
  return {
    ownerId: env.SYNC_OWNER_ID,
    scopes: new Set(["corpus.sync"]),
    clientId: "personal-agent-sync",
    auth: "sync-device",
    deviceId,
  };
}

export function requireScope(principal: Principal, scope: string): void {
  if (!principal.scopes.has(scope)) {
    throw new ContextError(
      "insufficient_scope",
      "the connection does not grant this operation",
      403,
    );
  }
}
