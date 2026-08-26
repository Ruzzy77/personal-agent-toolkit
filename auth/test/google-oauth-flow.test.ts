import { exports } from "cloudflare:workers";
import { beforeEach, describe, it } from "vitest";
import type { ExpectStatic } from "vitest";

import type { AuthService } from "../src/auth-service";

const ISSUER = "https://auth.example.test";
const LIBRARY = "https://library.example.test/api/mcp";
const REDIRECT_URI = "https://client.example.test/callback";

interface RegisteredClient {
  client_id: string;
}

interface TokenResponse {
  access_token: string;
  scope: string;
  resource: string;
}

interface GoogleStart {
  clientVerifier: string;
  googleState: string;
  googleNonce: string;
  googleChallenge: string;
  flowCookie: string;
}

const authService = exports.AuthService as unknown as Service<AuthService>;

function base64Url(bytes: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

async function pkce(): Promise<{ verifier: string; challenge: string }> {
  const verifier = `${crypto.randomUUID()}-${crypto.randomUUID()}`;
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return { verifier, challenge: base64Url(digest) };
}

function setCookies(headers: Headers): string[] {
  const withGetSetCookie = headers as Headers & {
    getSetCookie?: () => string[];
  };
  const values = withGetSetCookie.getSetCookie?.();
  if (values && values.length > 0) {
    return values;
  }
  const value = headers.get("Set-Cookie");
  return value ? [value] : [];
}

function cookiePair(setCookie: string): string {
  return setCookie.split(";", 1)[0];
}

async function registerClient(expect: ExpectStatic): Promise<string> {
  const response = await exports.default.fetch(`${ISSUER}/oauth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_name: "ChatGPT contract test",
      redirect_uris: [REDIRECT_URI],
      token_endpoint_auth_method: "none",
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
    }),
  });
  expect(response.status).toBe(201);
  return (await response.json<RegisteredClient>()).client_id;
}

async function startGoogleFlow(
  clientId: string,
  expect: ExpectStatic,
): Promise<GoogleStart> {
  const clientPkce = await pkce();
  const url = new URL(`${ISSUER}/authorize`);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", clientId);
  url.searchParams.set("redirect_uri", REDIRECT_URI);
  url.searchParams.set("scope", "library.read");
  url.searchParams.set("resource", LIBRARY);
  url.searchParams.set("state", crypto.randomUUID());
  url.searchParams.set("code_challenge", clientPkce.challenge);
  url.searchParams.set("code_challenge_method", "S256");

  const response = await exports.default.fetch(url.href, {
    redirect: "manual",
  });
  expect(response.status).toBe(302);
  const google = new URL(response.headers.get("Location") ?? "");
  expect(google.origin).toBe("https://accounts.google.com");
  expect(google.searchParams.get("redirect_uri")).toBe(
    `${ISSUER}/oauth/google/callback`,
  );
  expect(google.searchParams.get("scope")).toBe("openid email profile");
  expect(google.searchParams.get("code_challenge_method")).toBe("S256");
  const flowCookie = setCookies(response.headers).find((cookie) =>
    cookie.startsWith("__Host-pa-flow-"),
  );
  expect(flowCookie).toBeTypeOf("string");
  return {
    clientVerifier: clientPkce.verifier,
    googleState: google.searchParams.get("state") ?? "",
    googleNonce: google.searchParams.get("nonce") ?? "",
    googleChallenge: google.searchParams.get("code_challenge") ?? "",
    flowCookie: cookiePair(flowCookie ?? ""),
  };
}

async function completeGoogle(
  flow: GoogleStart,
  account: "owner" | "other",
): Promise<Response> {
  const callback = new URL(`${ISSUER}/oauth/google/callback`);
  callback.searchParams.set("state", flow.googleState);
  callback.searchParams.set(
    "code",
    `${account}.${flow.googleNonce}.${flow.googleChallenge}`,
  );
  return exports.default.fetch(callback.href, {
    redirect: "manual",
    headers: { Cookie: flow.flowCookie },
  });
}

async function exchangeClientCode(
  clientId: string,
  verifier: string,
  redirect: URL,
): Promise<Response> {
  return exports.default.fetch(`${ISSUER}/oauth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: clientId,
      code: redirect.searchParams.get("code") ?? "",
      redirect_uri: REDIRECT_URI,
      code_verifier: verifier,
      resource: LIBRARY,
    }),
  });
}

describe("Google owner OAuth flow", () => {
  let clientId: string;

  beforeEach(async ({ expect }) => {
    clientId = await registerClient(expect);
  });

  it("completes Google identity verification and token issuance", async ({
    expect,
  }) => {
    const flow = await startGoogleFlow(clientId, expect);
    const callback = await completeGoogle(flow, "owner");
    expect(callback.status).toBe(302);
    const clientRedirect = new URL(callback.headers.get("Location") ?? "");
    expect(clientRedirect.origin).toBe("https://client.example.test");
    expect(clientRedirect.searchParams.get("iss")).toBe(ISSUER);

    const tokenResponse = await exchangeClientCode(
      clientId,
      flow.clientVerifier,
      clientRedirect,
    );
    expect(tokenResponse.status).toBe(200);
    const token = await tokenResponse.json<TokenResponse>();
    expect(token.resource).toBe(LIBRARY);
    expect(token.scope).toBe("library.read");

    const validation = await authService.validateAccessToken(
      token.access_token,
      LIBRARY,
      ["library.read"],
    );
    expect(validation).toMatchObject({
      ok: true,
      owner: {
        provider: "google",
        subject: "google-sub-owner-123",
        email: "owner@example.test",
        displayName: "Owner",
      },
    });
  });

  it("blocks a verified Google account outside the owner allowlist", async ({
    expect,
  }) => {
    const flow = await startGoogleFlow(clientId, expect);
    const callback = await completeGoogle(flow, "other");
    expect(callback.status).toBe(403);
    expect(await callback.json()).toMatchObject({
      error: "invalid_request",
    });
  });

  it("requires the browser-bound callback cookie", async ({ expect }) => {
    const flow = await startGoogleFlow(clientId, expect);
    const callback = new URL(`${ISSUER}/oauth/google/callback`);
    callback.searchParams.set("state", flow.googleState);
    callback.searchParams.set(
      "code",
      `owner.${flow.googleNonce}.${flow.googleChallenge}`,
    );
    const response = await exports.default.fetch(callback.href, {
      redirect: "manual",
    });
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({
      error: "invalid_request",
    });
  });

  it("returns access_denied to the client when Google login is declined", async ({
    expect,
  }) => {
    const flow = await startGoogleFlow(clientId, expect);
    const callback = new URL(`${ISSUER}/oauth/google/callback`);
    callback.searchParams.set("state", flow.googleState);
    callback.searchParams.set("error", "access_denied");
    const response = await exports.default.fetch(callback.href, {
      redirect: "manual",
      headers: { Cookie: flow.flowCookie },
    });
    expect(response.status).toBe(302);
    const redirect = new URL(response.headers.get("Location") ?? "");
    expect(redirect.searchParams.get("error")).toBe("access_denied");
    expect(redirect.searchParams.get("iss")).toBe(ISSUER);
  });
});
