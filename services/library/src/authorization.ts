import {
  authorizationChallenge as buildAuthorizationChallenge,
  bearerToken,
  protectedResourceMetadata as buildProtectedResourceMetadata,
} from "@personal-agent/remote-runtime";

import type {
  AccessValidationResult,
  AuthServiceBinding,
} from "./types";

export const LIBRARY_SCOPES = ["library.read", "library.write"];

export function extractBearerToken(request: Request): string | null {
  return bearerToken(request);
}

export async function authorizeRequest(
  request: Request,
  auth: AuthServiceBinding,
  resource: string,
  requiredScopes: string[],
): Promise<AccessValidationResult> {
  const token = extractBearerToken(request);
  if (!token) {
    return { ok: false, code: "invalid_token", status: 401 };
  }
  return auth.validateAccessToken(token, resource, requiredScopes);
}

export function protectedResourceMetadata(
  resource: string,
  issuer: string,
) {
  return buildProtectedResourceMetadata({
    resource,
    authorizationServer: issuer,
    scopes: LIBRARY_SCOPES,
    resourceName: "Library",
  });
}

export function authorizationChallenge(
  resourceMetadata: string,
  result?: Extract<AccessValidationResult, { ok: false }>,
): string {
  return buildAuthorizationChallenge({
    resourceMetadata,
    scopes: LIBRARY_SCOPES,
    ...(result ? { failure: result } : {}),
  });
}
