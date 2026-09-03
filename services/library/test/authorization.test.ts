import { describe, expect, it, vi } from "vitest";

import {
  authorizationChallenge,
  authorizeRequest,
  protectedResourceMetadata,
} from "../src/authorization";

describe("Library MCP authorization boundary", () => {
  it("advertises the exact resource and authorization server", () => {
    expect(
      protectedResourceMetadata(
        "https://library.example/api/mcp",
        "https://auth.example",
      ),
    ).toEqual({
      resource: "https://library.example/api/mcp",
      authorization_servers: ["https://auth.example"],
      scopes_supported: ["library.read", "library.write"],
      bearer_methods_supported: ["header"],
      resource_name: "Library",
    });
  });

  it("rejects a request without a bearer token before RPC", async () => {
    const validateAccessToken = vi.fn();
    const result = await authorizeRequest(
      new Request("https://library.example/api/mcp"),
      { validateAccessToken },
      "https://library.example/api/mcp",
      ["library.read"],
    );
    expect(result).toEqual({
      ok: false,
      code: "invalid_token",
      status: 401,
    });
    expect(validateAccessToken).not.toHaveBeenCalled();
  });

  it("passes the exact token, resource and scope to private RPC", async () => {
    const validation = {
      ok: true as const,
      owner: {
        userId: "owner",
        provider: "google" as const,
        subject: "subject",
        resource: "https://library.example/api/mcp",
        scopes: ["library.read"],
        clientId: "client",
        expiresAt: 1,
      },
    };
    const validateAccessToken = vi.fn().mockResolvedValue(validation);
    const result = await authorizeRequest(
      new Request("https://library.example/api/mcp", {
        headers: { Authorization: "Bearer opaque-token" },
      }),
      { validateAccessToken },
      "https://library.example/api/mcp",
      ["library.read"],
    );
    expect(result).toEqual(validation);
    expect(validateAccessToken).toHaveBeenCalledWith(
      "opaque-token",
      "https://library.example/api/mcp",
      ["library.read"],
    );
  });

  it("returns an RFC 9728 scope challenge", () => {
    expect(
      authorizationChallenge(
        "https://library.example/.well-known/oauth-protected-resource/api/mcp",
        {
          ok: false,
          code: "insufficient_scope",
          status: 403,
          requiredScopes: ["library.read", "library.write"],
        },
      ),
    ).toBe(
      'Bearer error="insufficient_scope", resource_metadata="https://library.example/.well-known/oauth-protected-resource/api/mcp", scope="library.read library.write"',
    );
  });
});
