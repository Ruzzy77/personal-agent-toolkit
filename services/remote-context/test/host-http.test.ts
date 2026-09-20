import { describe, expect, it, vi } from "vitest";

import { handleHostHttp } from "../src/host-http";
import type { Env } from "../src/types";

const token = "a".repeat(43);
const siteHeaders = {
  Authorization: "Bearer site-token",
  "X-Personal-Agent-Site-User-Id": "site-user",
};

function env(fetch = vi.fn()): Env {
  return {
    CONTEXT_SITE_TOKEN: "site-token",
    CONTEXT_SITE_USER_ID: "site-user",
    CONTEXT_SITE_OWNER_ID: "owner",
    HOST_UPSTREAM_TOKEN: "host-secret",
    HOST_VPC: { fetch } as unknown as Fetcher,
    TOOLKIT_RESOURCE: "https://tools.example",
  } as Env;
}

describe("Worker Host file HTTP boundary", () => {
  it("requires the server-to-server site credential", async () => {
    await expect(handleHostHttp(
      new Request("https://worker.example/site/host/v1/host_roots", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: "{}" }),
      env(),
    )).rejects.toMatchObject({ code: "invalid_site_credential", status: 401 });
  });

  it("does not expose host_exec through the site file API", async () => {
    await expect(handleHostHttp(
      new Request("https://worker.example/site/host/v1/host_exec", { method: "POST",
        headers: { ...siteHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({ root: "workspace", argv: ["id"] }) }),
      env(),
    )).rejects.toMatchObject({ code: "not_found", status: 404 });
  });

  it("requires versions and rejects permanent browser deletes", async () => {
    const base = "https://worker.example/site/host/v1/host_write";
    await expect(handleHostHttp(new Request(base, { method: "POST",
      headers: { ...siteHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({ root: "workspace", path: "a.txt", content: "x" }) }), env(),
    )).rejects.toMatchObject({ code: "invalid_request", status: 400 });
    await expect(handleHostHttp(new Request(base, { method: "POST",
      headers: { ...siteHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({ root: "workspace", path: "a.txt", delete: true, expected_version: "x" }) }), env(),
    )).rejects.toMatchObject({ code: "invalid_request", status: 400 });
  });

  it("forwards chunks with only the Host credential and strips cookies", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("ok", {
      headers: { "Set-Cookie": "host=secret", "X-Host": "yes" },
    }));
    const response = await handleHostHttp(new Request(
      "https://worker.example/site/host/v1/transfers/abcdefghijkl/chunk", {
        method: "PUT", headers: { ...siteHeaders, "X-Toolkit-Transfer-Token": token,
          "Content-Type": "application/octet-stream", "Upload-Offset": "0" }, body: "abc",
      }), env(fetch));
    expect(fetch).toHaveBeenCalledOnce();
    const [target, init] = fetch.mock.calls[0]! as [string, RequestInit];
    const forwarded = new Headers(init.headers);
    expect(target).toBe("http://spark-host/transfers/abcdefghijkl/chunk");
    expect(forwarded.get("Authorization")).toBe("Bearer host-secret");
    expect(forwarded.get("X-Toolkit-Transfer-Token")).toBe(token);
    expect(response?.headers.get("Set-Cookie")).toBeNull();
    expect(response?.headers.get("Cache-Control")).toBe("private, no-store");
  });

  it("rejects invalid transfer tokens and chunks over 8 MiB", async () => {
    const invalid = await handleHostHttp(new Request(
      "https://worker.example/host/v1/transfers/abcdefghijkl/status", { headers: { "X-Toolkit-Transfer-Token": "bad" } },
    ), env());
    expect(invalid?.status).toBe(404);
    await expect(handleHostHttp(new Request(
      "https://worker.example/host/v1/transfers/abcdefghijkl/chunk", {
        method: "PUT", headers: { "X-Toolkit-Transfer-Token": token, "Content-Length": String(8 * 1024 * 1024 + 1) },
        body: "x",
      }), env(),
    )).rejects.toMatchObject({ code: "request_too_large", status: 413 });
  });

  it("preserves a wrapped Host error code for browser conflict handling", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({
      jsonrpc: "2.0",
      id: "test",
      result: {
        isError: true,
        content: [{
          type: "text",
          text: "Error executing tool host_write: version_conflict: the file changed",
        }],
      },
    }));
    const response = await handleHostHttp(new Request(
      "https://worker.example/site/host/v1/host_write", {
        method: "POST",
        headers: { ...siteHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({
          root: "workspace",
          path: "notes.md",
          content: "draft",
          expected_version: "old",
        }),
      }), env(fetch));
    expect(response?.status).toBe(409);
    expect(await response?.json()).toMatchObject({
      error: { code: "version_conflict" },
    });
  });
});
