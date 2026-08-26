import type { AuthRequest } from "@cloudflare/workers-oauth-provider";

import {
  findResource,
  type ResourceDefinition,
  type ResourceRegistry,
} from "./resource-registry";

export type AuthorizationPolicyErrorCode =
  | "invalid_target"
  | "invalid_scope";

export class AuthorizationPolicyError extends Error {
  constructor(
    readonly code: AuthorizationPolicyErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "AuthorizationPolicyError";
  }
}

export interface AuthorizationDecision {
  resource: ResourceDefinition;
  grantedScopes: string[];
}

function singleResource(value: string | string[] | undefined): string {
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value) && value.length === 1) {
    return value[0];
  }
  throw new AuthorizationPolicyError(
    "invalid_target",
    "exactly one registered resource is required",
  );
}

export function authorizeRequest(
  request: Pick<AuthRequest, "resource" | "scope">,
  registry: ResourceRegistry,
): AuthorizationDecision {
  const requestedResource = singleResource(request.resource);
  const resource = findResource(registry, requestedResource);
  if (!resource) {
    throw new AuthorizationPolicyError(
      "invalid_target",
      "the requested resource is not registered",
    );
  }

  const requestedScopes = [...new Set(request.scope)];
  if (requestedScopes.some((scope) => !resource.scopes.includes(scope))) {
    throw new AuthorizationPolicyError(
      "invalid_scope",
      "the requested scope does not belong to this resource",
    );
  }
  if (
    resource.baselineScopes.some(
      (scope) => !requestedScopes.includes(scope),
    )
  ) {
    throw new AuthorizationPolicyError(
      "invalid_scope",
      "the requested scopes do not include the resource baseline",
    );
  }

  return { resource, grantedScopes: requestedScopes };
}
