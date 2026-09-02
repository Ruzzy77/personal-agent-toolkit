import { exports } from "cloudflare:workers";
import { beforeEach, describe, it } from "vitest";
import type { ExpectStatic } from "vitest";

import type { AuthService } from "../src/auth-service";

const ISSUER = "https://auth.example.test";
const LIBRARY = "https://library.example.test/api/mcp";
const CORPUS = "https://corpus.example.test/mcp";
const REDIRECT_URI = "https://client.example.test/callback";

interface RegisteredClient {
  client_id: string;
}

interface TokenResponse {
  access_token: string;
  refresh_token?: string;
  token_type: "bearer";
  expires_in: number;
  scope: string;
  resource: string;
}

const authService = exports.AuthService as unknown as Service<AuthService>;

async function registerClient(expect: ExpectStatic): Promise<string> {
  const response = await exports.default.fetch(`${ISSUER}/oauth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_name: "Personal Agent Auth contract test",
      redirect_uris: [REDIRECT_URI],
      token_endpoint_auth_method: "none",
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
    }),
  });
  expect(response.status).toBe(201);
  const client = await response.json<RegisteredClient>();
  expect(client.client_id).toBeTypeOf("string");
  return client.client_id;
}

function base64Url(bytes: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

async function createPkce(): Promise<{ verifier: string; challenge: string }> {
  const verifier = `${crypto.randomUUID()}-${crypto.randomUUID()}`;
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return { verifier, challenge: base64Url(digest) };
}

async function authorizeAndExchange(
  clientId: string,
  resource: string,
  scope: string,
  expect: ExpectStatic,
): Promise<TokenResponse> {
  const authorization = await authorizeForCode(
    clientId,
    resource,
    scope,
    expect,
  );
  const token = await exchangeAuthorizationCode(
    clientId,
    authorization.code,
    authorization.verifier,
    resource,
  );
  expect(token.status).toBe(200);
  return token.json<TokenResponse>();
}

async function authorizeForCode(
  clientId: string,
  resource: string,
  scope: string,
  expect: ExpectStatic,
): Promise<{ code: string; verifier: string }> {
  const { verifier, challenge } = await createPkce();
  const authorizeUrl = new URL(`${ISSUER}/authorize`);
  authorizeUrl.searchParams.set("response_type", "code");
  authorizeUrl.searchParams.set("client_id", clientId);
  authorizeUrl.searchParams.set("redirect_uri", REDIRECT_URI);
  authorizeUrl.searchParams.set("scope", scope);
  authorizeUrl.searchParams.set("resource", resource);
  authorizeUrl.searchParams.set("state", crypto.randomUUID());
  authorizeUrl.searchParams.set("code_challenge", challenge);
  authorizeUrl.searchParams.set("code_challenge_method", "S256");

  const authorization = await exports.default.fetch(authorizeUrl.toString(), {
    redirect: "manual",
  });
  expect(authorization.status).toBe(302);
  const redirect = new URL(authorization.headers.get("Location") ?? "");
  const code = redirect.searchParams.get("code");
  expect(code).toBeTypeOf("string");
  expect(redirect.searchParams.get("iss")).toBe(ISSUER);

  return { code: code ?? "", verifier };
}

function exchangeAuthorizationCode(
  clientId: string,
  code: string,
  verifier: string,
  resource: string,
): Promise<Response> {
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: clientId,
    code: code ?? "",
    redirect_uri: REDIRECT_URI,
    code_verifier: verifier,
    resource,
  });
  return exports.default.fetch(`${ISSUER}/oauth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
}

describe("OAuth provider contract", () => {
  let clientId: string;

  beforeEach(async ({ expect }) => {
    clientId = await registerClient(expect);
  });

  it("advertises S256 PKCE, CIMD, and authorization code flow", async ({
    expect,
  }) => {
    const response = await exports.default.fetch(
      `${ISSUER}/.well-known/oauth-authorization-server`,
    );
    expect(response.status).toBe(200);
    const metadata = await response.json<{
      issuer: string;
      code_challenge_methods_supported: string[];
      client_id_metadata_document_supported: boolean;
      grant_types_supported: string[];
    }>();
    expect(metadata.issuer).toBe(ISSUER);
    expect(metadata.code_challenge_methods_supported).toEqual(["S256"]);
    expect(metadata.client_id_metadata_document_supported).toBe(true);
    expect(metadata.grant_types_supported).toContain("authorization_code");
  });

  it("issues a Library token and validates it through the private RPC entrypoint", async ({
    expect,
  }) => {
    const token = await authorizeAndExchange(
      clientId,
      LIBRARY,
      "library.read",
      expect,
    );
    expect(token.resource).toBe(LIBRARY);
    expect(token.scope).toBe("library.read");

    const result = await authService.validateAccessToken(
      token.access_token,
      LIBRARY,
      ["library.read"],
    );
    expect(result).toMatchObject({
      ok: true,
      owner: {
        provider: "google",
        subject: "google-sub-owner-123",
        resource: LIBRARY,
        scopes: ["library.read"],
      },
    });
  });

  it("issues an independently scoped Corpus token", async ({ expect }) => {
    const token = await authorizeAndExchange(
      clientId,
      CORPUS,
      "corpus.read",
      expect,
    );
    const result = await authService.validateAccessToken(
      token.access_token,
      CORPUS,
      ["corpus.read"],
    );
    expect(result).toMatchObject({
      ok: true,
      owner: {
        resource: CORPUS,
        scopes: ["corpus.read"],
      },
    });
  });

  it("keeps independent resource grants active for the same client", async ({
    expect,
  }) => {
    const libraryToken = await authorizeAndExchange(
      clientId,
      LIBRARY,
      "library.read",
      expect,
    );
    const corpusToken = await authorizeAndExchange(
      clientId,
      CORPUS,
      "corpus.read",
      expect,
    );

    await expect(
      authService.validateAccessToken(libraryToken.access_token, LIBRARY, [
        "library.read",
      ]),
    ).resolves.toMatchObject({ ok: true });
    await expect(
      authService.validateAccessToken(corpusToken.access_token, CORPUS, [
        "corpus.read",
      ]),
    ).resolves.toMatchObject({ ok: true });
  });

  it("rejects the same token for another registered resource", async ({
    expect,
  }) => {
    const token = await authorizeAndExchange(
      clientId,
      LIBRARY,
      "library.read",
      expect,
    );
    const result = await authService.validateAccessToken(
      token.access_token,
      CORPUS,
      ["corpus.read"],
    );
    expect(result).toEqual({
      ok: false,
      code: "invalid_target",
      status: 401,
    });
  });

  it("rejects a token without the operation scope", async ({ expect }) => {
    const token = await authorizeAndExchange(
      clientId,
      LIBRARY,
      "library.read",
      expect,
    );
    const result = await authService.validateAccessToken(
      token.access_token,
      LIBRARY,
      ["library.write"],
    );
    expect(result).toEqual({
      ok: false,
      code: "insufficient_scope",
      status: 403,
      requiredScopes: ["library.write"],
    });
  });

  it("rejects cross-resource scopes during authorization", async ({
    expect,
  }) => {
    const { challenge } = await createPkce();
    const authorizeUrl = new URL(`${ISSUER}/authorize`);
    authorizeUrl.searchParams.set("response_type", "code");
    authorizeUrl.searchParams.set("client_id", clientId);
    authorizeUrl.searchParams.set("redirect_uri", REDIRECT_URI);
    authorizeUrl.searchParams.set("scope", "library.read corpus.read");
    authorizeUrl.searchParams.set("resource", LIBRARY);
    authorizeUrl.searchParams.set("state", crypto.randomUUID());
    authorizeUrl.searchParams.set("code_challenge", challenge);
    authorizeUrl.searchParams.set("code_challenge_method", "S256");

    const response = await exports.default.fetch(authorizeUrl.toString(), {
      redirect: "manual",
    });
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ error: "invalid_scope" });
  });

  it("rejects a token exchange that changes the authorized resource", async ({
    expect,
  }) => {
    const authorization = await authorizeForCode(
      clientId,
      LIBRARY,
      "library.read",
      expect,
    );
    const response = await exchangeAuthorizationCode(
      clientId,
      authorization.code,
      authorization.verifier,
      CORPUS,
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ error: "invalid_target" });
  });

  it("rejects plain PKCE", async ({ expect }) => {
    const authorizeUrl = new URL(`${ISSUER}/authorize`);
    authorizeUrl.searchParams.set("response_type", "code");
    authorizeUrl.searchParams.set("client_id", clientId);
    authorizeUrl.searchParams.set("redirect_uri", REDIRECT_URI);
    authorizeUrl.searchParams.set("scope", "library.read");
    authorizeUrl.searchParams.set("resource", LIBRARY);
    authorizeUrl.searchParams.set("state", crypto.randomUUID());
    authorizeUrl.searchParams.set("code_challenge", "plain-verifier");
    authorizeUrl.searchParams.set("code_challenge_method", "plain");

    const response = await exports.default.fetch(authorizeUrl.toString(), {
      redirect: "manual",
    });
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ error: "invalid_request" });
  });

  it("invalidates a revoked access token", async ({ expect }) => {
    const token = await authorizeAndExchange(
      clientId,
      LIBRARY,
      "library.read",
      expect,
    );
    const revocation = await exports.default.fetch(`${ISSUER}/oauth/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: clientId,
        token: token.access_token,
        token_type_hint: "access_token",
      }),
    });
    expect(revocation.status).toBe(200);

    const result = await authService.validateAccessToken(
      token.access_token,
      LIBRARY,
      ["library.read"],
    );
    expect(result).toEqual({
      ok: false,
      code: "invalid_token",
      status: 401,
    });
  });
});
