import { describe, expect, it, vi } from "vitest";

import worker from "../src/worker";
import type { Env } from "../src/types";

describe("Library MCP connection scopes", () => {
  it("requires both read and write access for the owner product", async () => {
    const validateAccessToken = vi.fn().mockResolvedValue({
      ok: false,
      code: "insufficient_scope",
      status: 403,
      requiredScopes: ["library.read", "library.write"],
    });
    const env = {
      AUTH_SERVICE: { validateAccessToken },
      AUTH_ISSUER: "https://auth.example",
      RESOURCE_URI: "https://library.example/api/mcp",
      SITES_ORIGIN: "https://site.example",
      SITES_BYPASS_TOKEN: "sites-token",
      LIBRARY_BRIDGE_SECRET: "bridge-secret",
    } as unknown as Env;

    const request = new Request("https://library.example/api/mcp", {
      headers: { Authorization: "Bearer read-only-token" },
    }) as unknown as Parameters<typeof worker.fetch>[0];

    const response = await worker.fetch(
      request,
      env,
    );

    expect(response.status).toBe(403);
    expect(validateAccessToken).toHaveBeenCalledWith(
      "read-only-token",
      "https://library.example/api/mcp",
      ["library.read", "library.write"],
    );
    expect(response.headers.get("WWW-Authenticate")).toContain(
      'scope="library.read library.write"',
    );
  });
});
