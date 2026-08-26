import type { TokenSummary } from "@cloudflare/workers-oauth-provider";

import {
  findResource,
  type ResourceRegistry,
} from "./resource-registry";
import type {
  AccessValidationResult,
  OwnerProps,
} from "./types";

function isOwnerProps(value: unknown): value is OwnerProps {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const props = value as Partial<OwnerProps>;
  return (
    props.provider === "google" &&
    typeof props.subject === "string" &&
    props.subject !== "" &&
    (props.email === undefined || typeof props.email === "string") &&
    (props.displayName === undefined || typeof props.displayName === "string")
  );
}

function hasExactAudience(
  audience: string | string[] | undefined,
  expected: string,
): boolean {
  if (typeof audience === "string") {
    return audience === expected;
  }
  return (
    Array.isArray(audience) &&
    audience.length === 1 &&
    audience[0] === expected
  );
}

export function validateTokenSummary(
  token: TokenSummary<OwnerProps> | null,
  expectedResource: string,
  requiredScopes: string[],
  registry: ResourceRegistry,
  nowSeconds = Math.floor(Date.now() / 1000),
): AccessValidationResult {
  const resource = findResource(registry, expectedResource);
  if (!resource) {
    return { ok: false, code: "invalid_target", status: 500 };
  }
  if (requiredScopes.some((scope) => !resource.scopes.includes(scope))) {
    return { ok: false, code: "invalid_scope", status: 500 };
  }
  if (
    token === null ||
    token.expiresAt <= nowSeconds ||
    !isOwnerProps(token.grant.props)
  ) {
    return { ok: false, code: "invalid_token", status: 401 };
  }
  if (!hasExactAudience(token.audience, expectedResource)) {
    return { ok: false, code: "invalid_target", status: 401 };
  }
  if (token.scope.some((scope) => !resource.scopes.includes(scope))) {
    return { ok: false, code: "invalid_token", status: 401 };
  }

  const missing = requiredScopes.filter(
    (scope) => !token.scope.includes(scope),
  );
  if (missing.length > 0) {
    return {
      ok: false,
      code: "insufficient_scope",
      status: 403,
      requiredScopes: missing,
    };
  }

  return {
    ok: true,
    owner: {
      userId: token.userId,
      provider: token.grant.props.provider,
      subject: token.grant.props.subject,
      email: token.grant.props.email,
      displayName: token.grant.props.displayName,
      resource: expectedResource,
      scopes: [...token.scope],
      clientId: token.grant.clientId,
      expiresAt: token.expiresAt,
    },
  };
}
