import type {
  AccessValidationResult,
  AuthServiceBinding,
} from "./types";

const DEFAULT_SCOPES = ["library.read", "library.write"];

export function extractBearerToken(request: Request): string | null {
  const value = request.headers.get("Authorization");
  const match = value ? /^Bearer ([^\s]+)$/i.exec(value) : null;
  return match?.[1] ?? null;
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
  return {
    resource,
    authorization_servers: [issuer],
    scopes_supported: DEFAULT_SCOPES,
    bearer_methods_supported: ["header"],
    resource_name: "Library",
  };
}

export function authorizationChallenge(
  resourceMetadata: string,
  result?: Extract<AccessValidationResult, { ok: false }>,
): string {
  const values = [
    `resource_metadata="${resourceMetadata}"`,
    `scope="${result?.requiredScopes?.join(" ") || DEFAULT_SCOPES.join(" ")}"`,
  ];
  if (result?.code === "insufficient_scope") {
    values.unshift('error="insufficient_scope"');
  } else if (result) {
    values.unshift('error="invalid_token"');
  }
  return `Bearer ${values.join(", ")}`;
}
