import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { authenticate } from "../src/auth";
import type { AuthServiceBinding, Env } from "../src/types";

const RESOURCE = "https://journal.example.test/mcp";

function testEnv(binding: AuthServiceBinding): Env {
  const {
    JOURNAL_SITE_TOKEN: _siteToken,
    JOURNAL_INGEST_TOKEN: _ingestToken,
    AUTH_SERVICE: _authService,
    ...base
  } = env as unknown as Env;
  return {
    ...base,
    JOURNAL_RESOURCE: RESOURCE,
    AUTH_ISSUER: "https://auth.example.test",
    AUTH_SERVICE: binding,
  };
}

describe("OAuth service binding", () => {
  it("accepts the exact Journal audience and required owner scope", async () => {
    let observed: unknown[] = [];
    const binding: AuthServiceBinding = {
      async validateAccessToken(token, resource, scopes) {
        observed = [token, resource, scopes];
        return {
          ok: true,
          owner: {
            userId: "owner-1",
            provider: "google",
            subject: "google-subject",
            resource,
            scopes: ["journal.read", "journal.write"],
            clientId: "client-1",
            expiresAt: 1_800_000_000,
          },
        };
      },
    };
    const principal = await authenticate(
      new Request("https://journal.example.test/api/v1/board", {
        headers: { Authorization: "Bearer oauth-token" },
      }),
      testEnv(binding),
      ["journal.read"],
    );
    expect(observed).toEqual(["oauth-token", RESOURCE, ["journal.read"]]);
    expect(principal).toMatchObject({
      kind: "owner",
      id: "owner-1",
      auth: "oauth",
    });
  });

  it("preserves an authorization service rejection", async () => {
    const binding: AuthServiceBinding = {
      async validateAccessToken() {
        return {
          ok: false,
          code: "insufficient_scope",
          status: 403,
          requiredScopes: ["journal.write"],
        };
      },
    };
    await expect(
      authenticate(
        new Request("https://journal.example.test/api/v1/board", {
          headers: { Authorization: "Bearer oauth-token" },
        }),
        testEnv(binding),
        ["journal.write"],
      ),
    ).rejects.toMatchObject({ code: "insufficient_scope", status: 403 });
  });
});
