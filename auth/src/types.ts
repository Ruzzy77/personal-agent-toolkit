import type { OAuthHelpers } from "@cloudflare/workers-oauth-provider";

export interface OAuthEnv {
  OAUTH_KV: KVNamespace;
  OAUTH_PROVIDER: OAuthHelpers;
  RESOURCE_REGISTRY_JSON: string;
  AUTH_ISSUER: string;
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  OWNER_GOOGLE_SUBS?: string;
  OWNER_GOOGLE_EMAILS?: string;
}

export interface OwnerProps {
  provider: "google";
  subject: string;
  email?: string;
  emailVerified?: boolean;
  displayName?: string;
}

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

export type AccessValidationResult =
  | {
      ok: true;
      owner: AuthenticatedOwner;
    }
  | {
      ok: false;
      code:
        | "invalid_token"
        | "invalid_target"
        | "invalid_scope"
        | "insufficient_scope";
      status: 401 | 403 | 500;
      requiredScopes?: string[];
    };
