export interface AuthenticatedOwner {
  userId: string;
  provider: "google";
  subject: string;
  email?: string;
  displayName?: string;
  resource: string;
  scopes: string[];
  clientId: string;
  expiresAt: number;
}

export type AccessValidationFailure = {
  ok: false;
  code:
    | "invalid_token"
    | "invalid_target"
    | "invalid_scope"
    | "insufficient_scope";
  status: 401 | 403 | 500;
  requiredScopes?: string[];
};

export type AccessValidationResult =
  | { ok: true; owner: AuthenticatedOwner }
  | AccessValidationFailure;

export interface AuthServiceBinding {
  validateAccessToken(
    token: string,
    resource: string,
    requiredScopes: string[],
  ): Promise<AccessValidationResult>;
}

export function bearerToken(request: Request): string | null {
  const value = request.headers.get("Authorization");
  return value ? /^Bearer ([^\s]+)$/i.exec(value)?.[1] ?? null : null;
}

export function constantTimeEqual(left: string, right: string): boolean {
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

export function hasAnyScope(
  scopes: ReadonlySet<string>,
  required: readonly string[],
): boolean {
  return required.some((scope) => scopes.has(scope));
}

export function protectedResourceMetadata(options: {
  resource: string;
  authorizationServer: string;
  scopes: readonly string[];
  resourceName?: string;
  documentation?: string;
}): Record<string, unknown> {
  const metadata: Record<string, unknown> = {
    resource: options.resource,
    authorization_servers: [options.authorizationServer],
    scopes_supported: options.scopes,
    bearer_methods_supported: ["header"],
  };
  if (options.resourceName) metadata.resource_name = options.resourceName;
  if (options.documentation) {
    metadata.resource_documentation = options.documentation;
  }
  return metadata;
}

export function authorizationChallenge(options: {
  resourceMetadata: string;
  scopes: readonly string[];
  failure?: AccessValidationFailure;
}): string {
  const values = [
    `resource_metadata="${options.resourceMetadata}"`,
    `scope="${options.failure?.requiredScopes?.join(" ") || options.scopes.join(" ")}"`,
  ];
  if (options.failure?.code === "insufficient_scope") {
    values.unshift('error="insufficient_scope"');
  } else if (options.failure) {
    values.unshift('error="invalid_token"');
  }
  return `Bearer ${values.join(", ")}`;
}

export function mcpTextResult(
  structuredContent: unknown,
  text = JSON.stringify(structuredContent),
) {
  return {
    content: [{ type: "text" as const, text }],
    structuredContent: structuredContent as Record<string, unknown>,
  };
}

export function mcpTextError(
  error: Record<string, unknown>,
  includeStructuredContent = true,
) {
  const result: {
    content: Array<{ type: "text"; text: string }>;
    isError: true;
    structuredContent?: Record<string, unknown>;
  } = {
    content: [{ type: "text", text: JSON.stringify(error) }],
    isError: true,
  };
  if (includeStructuredContent) result.structuredContent = error;
  return result;
}

export function shortLivedMcpAuth(options: {
  token: string;
  clientId: string;
  scopes: Iterable<string>;
}) {
  return {
    token: options.token,
    clientId: options.clientId,
    scopes: [...options.scopes],
    expiresAt: Math.floor(Date.now() / 1000) + 300,
  };
}
