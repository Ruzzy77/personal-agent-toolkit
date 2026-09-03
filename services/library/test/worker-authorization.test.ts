import { env } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";

import { handleMcp } from "../src/mcp";
import worker from "../src/worker";
import type { Env } from "../src/types";

async function mcpPayload(response: Response): Promise<Record<string, unknown>> {
  const text = await response.text();
  if (response.headers.get("content-type")?.includes("application/json")) {
    return JSON.parse(text) as Record<string, unknown>;
  }
  const line = text.split("\n").find((value) => value.startsWith("data: "));
  if (!line) throw new Error(`MCP response contains no data: ${text}`);
  return JSON.parse(line.slice(6)) as Record<string, unknown>;
}

describe("Library MCP connection scopes", () => {
  it("advertises object-rooted output schemas for every owner tool", async () => {
    const response = await handleMcp(
      new Request("https://library.example/api/mcp", {
        method: "POST",
        headers: {
          Accept: "application/json, text/event-stream",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
      }),
      {
        userId: "owner",
        provider: "google",
        subject: "subject",
        resource: "https://library.example/api/mcp",
        scopes: ["library.read", "library.write"],
        clientId: "client",
        expiresAt: 1,
      },
      env,
    );
    expect(response.status, await response.clone().text()).toBe(200);
    const payload = await mcpPayload(response);
    const tools = (payload.result as {
      tools: Array<{
        name: string;
        outputSchema: Record<string, unknown>;
      }>;
    }).tools;
    expect(tools.map((tool) => tool.name)).toEqual([
      "library_whoami",
      "library_list_issues",
      "library_read_issue",
      "library_update_issue",
      "library_create_issue",
      "library_upload_asset",
    ]);
    for (const tool of tools) {
      expect(tool.outputSchema).toMatchObject({ type: "object" });
      expect(Object.keys(tool.outputSchema.properties as object).length).toBeGreaterThan(0);
    }
  });

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
      LIBRARY_SITE_TOKEN: "site-token",
    } as unknown as Env;

    const request = new Request("https://library.example/api/mcp", {
      headers: {
        Authorization: "Bearer read-only-token",
        Host: "library.example",
      },
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
