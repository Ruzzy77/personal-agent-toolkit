import {
  authorizationChallenge as buildAuthorizationChallenge,
  bearerToken,
  protectedResourceMetadata as buildProtectedResourceMetadata,
} from "@personal-agent/remote-runtime";

import type {
  AccessValidationResult,
  AuthServiceBinding,
} from "./types";

export const DESIGN_SCOPES = ["design.read", "design.write"];

export async function authorizeRequest(
  request: Request,
  auth: AuthServiceBinding,
  resource: string,
  requiredScopes: string[],
): Promise<AccessValidationResult> {
  const token = bearerToken(request);
  if (!token) return { ok: false, code: "invalid_token", status: 401 };
  return auth.validateAccessToken(token, resource, requiredScopes);
}

export function protectedResourceMetadata(resource: string, issuer: string) {
  return buildProtectedResourceMetadata({
    resource,
    authorizationServer: issuer,
    scopes: DESIGN_SCOPES,
    resourceName: "Design",
  });
}

export function authorizationChallenge(
  resourceMetadata: string,
  result?: Extract<AccessValidationResult, { ok: false }>,
): string {
  return buildAuthorizationChallenge({
    resourceMetadata,
    scopes: DESIGN_SCOPES,
    ...(result ? { failure: result } : {}),
  });
}
