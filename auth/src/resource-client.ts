import type { AccessValidationResult } from "./types";

export interface AuthServiceBinding {
  validateAccessToken(
    token: string,
    resource: string,
    requiredScopes: string[],
  ): Promise<AccessValidationResult>;
}

export function extractBearerToken(request: Request): string | null {
  const authorization = request.headers.get("Authorization");
  if (!authorization) {
    return null;
  }
  const match = /^Bearer ([^\s]+)$/i.exec(authorization);
  return match?.[1] ?? null;
}

export async function validateBearerRequest(
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
