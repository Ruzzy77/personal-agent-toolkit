import {
  bearerToken,
  constantTimeEqual,
  hasAnyScope,
} from "@personal-agent/remote-runtime";

import { JournalError } from "./errors";
import type { Env, Principal } from "./types";

const OWNER_SCOPES = new Set([
  "journal.read",
  "journal.write",
  "journal.ingest",
  "journal.close",
]);
const AUTOMATION_SCOPES = new Set(["journal.read", "journal.ingest"]);

export async function authenticate(
  request: Request,
  env: Env,
  anyScope: readonly string[],
): Promise<Principal> {
  const token = bearerToken(request);
  if (!token) {
    throw new JournalError("invalid_token", "bearer token is required", 401);
  }

  if (
    env.JOURNAL_SITE_TOKEN &&
    constantTimeEqual(token, env.JOURNAL_SITE_TOKEN)
  ) {
    const principal: Principal = {
      kind: "owner",
      id: "journal-site-owner",
      scopes: OWNER_SCOPES,
      auth: "site-token",
    };
    if (hasAnyScope(principal.scopes, anyScope)) return principal;
  }

  if (
    env.JOURNAL_INGEST_TOKEN &&
    constantTimeEqual(token, env.JOURNAL_INGEST_TOKEN)
  ) {
    const principal: Principal = {
      kind: "automation",
      id: "daily-monitoring",
      scopes: AUTOMATION_SCOPES,
      auth: "ingest-token",
    };
    if (hasAnyScope(principal.scopes, anyScope)) return principal;
    throw new JournalError(
      "insufficient_scope",
      "the automation token cannot perform this operation",
      403,
    );
  }

  if (!env.AUTH_SERVICE) {
    throw new JournalError("invalid_token", "token is not recognized", 401);
  }

  for (const scope of anyScope) {
    const validation = await env.AUTH_SERVICE.validateAccessToken(
      token,
      env.JOURNAL_RESOURCE,
      [scope],
    );
    if (validation.ok) {
      return {
        kind: "owner",
        id: validation.owner.userId,
        scopes: new Set(validation.owner.scopes),
        auth: "oauth",
      };
    }
  }

  throw new JournalError(
    "insufficient_scope",
    "the token does not grant the required Journal scope",
    403,
  );
}

export function requireOwner(principal: Principal): void {
  if (principal.kind !== "owner") {
    throw new JournalError(
      "owner_confirmation_required",
      "this state change requires owner confirmation",
      403,
    );
  }
}
