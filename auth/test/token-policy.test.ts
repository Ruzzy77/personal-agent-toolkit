import type { TokenSummary } from "@cloudflare/workers-oauth-provider";
import { describe, it } from "vitest";

import { parseResourceRegistry } from "../src/resource-registry";
import { validateTokenSummary } from "../src/token-policy";
import type { OwnerProps } from "../src/types";

const LIBRARY = "https://library.example.test/api/mcp";
const registry = parseResourceRegistry(
  JSON.stringify({
    resources: [
      {
        id: "library",
        name: "Library",
        resource: LIBRARY,
        scopes: ["library.read", "library.write"],
        baselineScopes: ["library.read"],
      },
    ],
  }),
);

function token(
  overrides: Partial<TokenSummary<OwnerProps>> = {},
): TokenSummary<OwnerProps> {
  return {
    id: "token-id",
    grantId: "grant-id",
    userId: "google-sub-owner-123",
    createdAt: 1_000,
    expiresAt: 2_000,
    audience: LIBRARY,
    scope: ["library.read"],
    grant: {
      clientId: "client-id",
      scope: ["library.read"],
      props: {
        provider: "google",
        subject: "google-sub-owner-123",
        email: "owner@example.test",
        emailVerified: true,
      },
    },
    ...overrides,
  };
}

describe("token policy", () => {
  it("rejects expired tokens even if storage returns one", ({ expect }) => {
    const result = validateTokenSummary(
      token({ expiresAt: 1_500 }),
      LIBRARY,
      ["library.read"],
      registry,
      1_500,
    );
    expect(result).toEqual({
      ok: false,
      code: "invalid_token",
      status: 401,
    });
  });

  it("rejects multi-audience tokens", ({ expect }) => {
    const result = validateTokenSummary(
      token({ audience: [LIBRARY, "https://corpus.example.test/mcp"] }),
      LIBRARY,
      ["library.read"],
      registry,
      1_200,
    );
    expect(result).toEqual({
      ok: false,
      code: "invalid_target",
      status: 401,
    });
  });

  it("rejects token scopes from another resource", ({ expect }) => {
    const result = validateTokenSummary(
      token({ scope: ["library.read", "corpus.read"] }),
      LIBRARY,
      ["library.read"],
      registry,
      1_200,
    );
    expect(result).toEqual({
      ok: false,
      code: "invalid_token",
      status: 401,
    });
  });
});
