import type { AuthRequest } from "@cloudflare/workers-oauth-provider";

import {
  RequestSecurityError,
  clearSecureCookie,
  randomToken,
  readCookie,
  secureCookie,
  sha256Base64Url,
} from "./security";

const FLOW_TTL_SECONDS = 600;
const KEY_PREFIX = "personal-agent-auth";

interface GoogleFlowRecord {
  kind: "google";
  oauthRequest: AuthRequest;
  resourceId: string;
  browserBindingHash: string;
  nonce: string;
  pkceVerifier: string;
}

export interface NewGoogleFlow {
  state: string;
  nonce: string;
  pkceVerifier: string;
  setCookie: string;
}

export interface ConsumedGoogleFlow {
  oauthRequest: AuthRequest;
  resourceId: string;
  nonce: string;
  pkceVerifier: string;
  clearCookie: string;
}

function googleKey(state: string): string {
  return `${KEY_PREFIX}:google:${state}`;
}

function googleCookieName(state: string): string {
  return `__Host-pa-flow-${state}`;
}

function isAuthRequest(value: unknown): value is AuthRequest {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const request = value as Partial<AuthRequest>;
  return (
    typeof request.clientId === "string" &&
    typeof request.redirectUri === "string" &&
    typeof request.state === "string" &&
    Array.isArray(request.scope)
  );
}

function isGoogleFlowRecord(value: unknown): value is GoogleFlowRecord {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Partial<GoogleFlowRecord>;
  return (
    record.kind === "google" &&
    isAuthRequest(record.oauthRequest) &&
    typeof record.resourceId === "string" &&
    typeof record.browserBindingHash === "string" &&
    typeof record.nonce === "string" &&
    typeof record.pkceVerifier === "string"
  );
}

export async function createGoogleFlow(
  kv: KVNamespace,
  oauthRequest: AuthRequest,
  resourceId: string,
): Promise<NewGoogleFlow> {
  const state = randomToken();
  const nonce = randomToken();
  const pkceVerifier = randomToken(48);
  const browserBinding = randomToken();
  const record: GoogleFlowRecord = {
    kind: "google",
    oauthRequest,
    resourceId,
    browserBindingHash: await sha256Base64Url(browserBinding),
    nonce,
    pkceVerifier,
  };
  await kv.put(googleKey(state), JSON.stringify(record), {
    expirationTtl: FLOW_TTL_SECONDS,
  });
  return {
    state,
    nonce,
    pkceVerifier,
    setCookie: secureCookie(
      googleCookieName(state),
      browserBinding,
      FLOW_TTL_SECONDS,
    ),
  };
}

export async function consumeGoogleFlow(
  kv: KVNamespace,
  request: Request,
  state: string,
): Promise<ConsumedGoogleFlow> {
  const record = await kv.get<GoogleFlowRecord>(googleKey(state), "json");
  if (!isGoogleFlowRecord(record)) {
    throw new RequestSecurityError(
      "invalid_request",
      "Google login request is missing or expired",
    );
  }

  const cookieName = googleCookieName(state);
  const browserBinding = readCookie(request, cookieName);
  const browserBindingHash = browserBinding
    ? await sha256Base64Url(browserBinding)
    : "";
  if (browserBindingHash !== record.browserBindingHash) {
    throw new RequestSecurityError(
      "invalid_request",
      "Google login is not bound to this browser",
    );
  }

  await kv.delete(googleKey(state));
  return {
    oauthRequest: record.oauthRequest,
    resourceId: record.resourceId,
    nonce: record.nonce,
    pkceVerifier: record.pkceVerifier,
    clearCookie: clearSecureCookie(cookieName),
  };
}
