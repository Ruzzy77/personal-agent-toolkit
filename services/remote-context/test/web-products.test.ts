import { env } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";

import { handleHttp as handleDesignLegacy } from "personal-agent-design-service/http";
import { handleHttp as handleJournalLegacy } from "personal-agent-journal-service/http";
import { handleHttp as handleLibraryLegacy } from "personal-agent-library-service/http";
import { handleHttp } from "../src/http";
import type { AuthServiceBinding, Env } from "../src/types";

const runtime = env as unknown as Env;
const ownerScopes = [
  "journal.read",
  "journal.write",
  "journal.close",
  "library.read",
  "library.write",
  "design.read",
  "design.write",
];
const tokenScopes: Record<string, string[]> = {
  "owner-token": ownerScopes,
  "read-token": ["journal.read", "library.read", "design.read"],
  "sense-token": ["sense.read"],
  "host-read-token": ["host.read"],
  "host-write-token": ["host.write"],
  "host-owner-token": ["host.read", "host.write"],
};

const auth: AuthServiceBinding = {
  async validateAccessToken(token, resource, requiredScopes) {
    const scopes = tokenScopes[token];
    if (!scopes) {
      return { ok: false, code: "invalid_token", status: 401 };
    }
    if (!requiredScopes.every((scope) => scopes.includes(scope))) {
      return {
        ok: false,
        code: "insufficient_scope",
        status: 403,
        requiredScopes,
      };
    }
    return {
      ok: true,
      owner: {
        userId: "oauth-owner",
        provider: "google",
        subject: "google-owner",
        email: "owner@example.test",
        resource,
        scopes,
        clientId: "web-client",
        expiresAt: 1_900_000_000,
      },
    };
  },
};

const webEnv: Env = { ...runtime, AUTH_SERVICE: auth };

async function body(response: Response): Promise<Record<string, unknown>> {
  return response.json<Record<string, unknown>>();
}

function webRequest(
  path: string,
  token?: string,
  init: Omit<RequestInit, "headers"> & { headers?: HeadersInit } = {},
): Request {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return new Request(`https://context.test${path}`, { ...init, headers });
}

describe("owner web product bridges", () => {
  it("requires a Toolkit bearer token and rejects unrelated scopes", async () => {
    for (const path of [
      "/web/journal/api/v1/board",
      "/web/library/api/v1/issues",
      "/web/library/media/daily/2026-09-20/08/cover.jpg",
      "/web/design/api/v1/catalog",
    ]) {
      const missing = await handleHttp(webRequest(path), webEnv);
      expect(missing.status, path).toBe(401);
      expect(await body(missing)).toMatchObject({
        ok: false,
        error: { code: "invalid_token" },
      });

      const wrong = await handleHttp(webRequest(path, "sense-token"), webEnv);
      expect(wrong.status, path).toBe(403);
      expect(await body(wrong)).toMatchObject({
        ok: false,
        error: { code: "insufficient_scope" },
      });
    }
  });

  it("serves scoped Library media with GET and bodyless HEAD only", async () => {
    const key = "daily/2026-09-20/08/cover.jpg";
    await runtime.LIBRARY_MEDIA.put(key, "cover-bytes", {
      httpMetadata: { contentType: "image/jpeg" },
    });
    try {
      const path = "/web/library/media/" + key;
      const get = await handleHttp(webRequest(path, "read-token"), webEnv);
      expect(get.status).toBe(200);
      expect(get.headers.get("Content-Type")).toBe("image/jpeg");
      expect(get.headers.get("Cache-Control")).toContain("private");
      expect(get.headers.get("ETag")).toBeTruthy();
      expect(get.headers.get("Set-Cookie")).toBeNull();
      expect(new Uint8Array(await get.arrayBuffer())).toEqual(new TextEncoder().encode("cover-bytes"));
      const head = await handleHttp(webRequest(path, "read-token", { method: "HEAD" }), webEnv);
      expect(head.status).toBe(200);
      expect(head.headers.get("ETag")).toBe(get.headers.get("ETag"));
      expect((await head.arrayBuffer()).byteLength).toBe(0);
      for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
        const rejected = await handleHttp(webRequest(path, "owner-token", { method }), webEnv);
        expect(rejected.status, method).toBe(405);
      }
      const missing = await handleHttp(webRequest("/web/library/media/missing.jpg", "read-token"), webEnv);
      expect(missing.status).toBe(404);
      const invalid = await handleHttp(webRequest("/web/library/media/bad%2F..%2Fkey.jpg", "read-token"), webEnv);
      expect(invalid.status).toBe(400);
      expect((await runtime.LIBRARY_MEDIA.get(key))?.size).toBe(11);
    } finally {
      await runtime.LIBRARY_MEDIA.delete(key);
    }
  });

  it("enforces read, write, and close scopes before product execution", async () => {
    const libraryWrite = await handleHttp(
      webRequest("/web/library/api/v1/issues", "read-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }),
      webEnv,
    );
    expect(libraryWrite.status).toBe(403);

    const designWrite = await handleHttp(
      webRequest("/web/design/api/v1/recipes", "read-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }),
      webEnv,
    );
    expect(designWrite.status).toBe(403);

    const journalClose = await handleHttp(
      webRequest(
        "/web/journal/api/v1/weeks/2026-09-14:prepare-close",
        "read-token",
        { method: "POST" },
      ),
      webEnv,
    );
    expect(journalClose.status).toBe(403);
  });

  it("does not expand OAuth scopes on the Web admin bridge", async () => {
    for (const operation of ["library_issue_trash", "design_recipe_trash"]) {
      const response = await handleHttp(
        webRequest(`/web/admin/v1/${operation}`, "read-token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        }),
        webEnv,
      );
      expect(response.status, operation).toBe(403);
      expect(await body(response)).toMatchObject({
        ok: false,
        error: { code: "insufficient_scope" },
      });
    }
  });

  it("routes all products and propagates the OAuth owner without forwarding the token", async () => {
    for (const path of [
      "/web/journal/api/v1/board",
      "/web/library/api/v1/issues",
      "/web/design/api/v1/catalog",
    ]) {
      const response = await handleHttp(webRequest(path, "read-token"), webEnv);
      expect(response.status, `${path}: ${await response.clone().text()}`).toBe(200);
      expect(await body(response)).toMatchObject({ ok: true });
    }

    const whoami = await handleHttp(
      webRequest(
        "/web/library/api/v1/operations/library_whoami",
        "read-token",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        },
      ),
      webEnv,
    );
    expect(whoami.status, await whoami.clone().text()).toBe(200);
    expect(await body(whoami)).toMatchObject({
      ok: true,
      result: {
        authenticated: true,
        provider: "google",
        email_hint: "ow***@example.test",
        resource: runtime.TOOLKIT_RESOURCE,
      },
    });

    const ingest = await handleHttp(
      webRequest("/web/journal/api/v1/items:ingest", "owner-token", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Origin: "https://browser.example.test",
          "X-Personal-Agent-Site-User-Id": "forged-browser-owner",
        },
        body: JSON.stringify({
          items: [{
            idempotencyKey: "web-owner-propagation",
            sourceKind: "web-test",
            sourceKey: "oauth-owner",
            sourceRef: null,
            sourceVersion: null,
            weekId: null,
            projectKey: null,
            title: "OAuth owner propagation",
            summary: "Bridge keeps the validated owner identity",
            lane: "today",
            responsibility: "user",
            dueAt: null,
            durableOutcome: null,
            corpusTargetSpace: null,
            occurredAt: "2026-09-20T00:00:00.000Z",
          }],
        }),
      }),
      webEnv,
    );
    expect(ingest.status, await ingest.clone().text()).toBe(200);
    expect(
      await runtime.JOURNAL_DB.prepare(
        "SELECT actor_kind, actor_ref FROM journal_events WHERE actor_ref=? ORDER BY created_at DESC LIMIT 1",
      )
        .bind("oauth:oauth-owner")
        .first(),
    ).toEqual({ actor_kind: "owner", actor_ref: "oauth:oauth-owner" });
  });



  it("serves Flow media only to owner sessions with Host read scope", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(new Uint8Array([1,2,3]), {
      headers: { "Content-Type": "image/png", "Set-Cookie": "private=1" },
    }));
    const hostEnv: Env = {
      ...webEnv,
      HOST_VPC: { fetch } as unknown as Fetcher,
      HOST_UPSTREAM_TOKEN: "server-host-secret",
    };
    const path = "/web/site/host/v1/flow-media/workspace/examples/metal.png";
    expect((await handleHttp(webRequest(path), hostEnv)).status).toBe(401);
    expect((await handleHttp(webRequest(path, "host-write-token"), hostEnv)).status).toBe(403);
    const response=await handleHttp(webRequest(path, "host-read-token"), hostEnv);
    expect(response.status).toBe(200);
    expect(response.headers.get("Set-Cookie")).toBeNull();
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(new Uint8Array([1,2,3]));
    expect(new Headers((fetch.mock.calls[0] as [string, RequestInit])[1].headers).get("Authorization"))
      .toBe("Bearer server-host-secret");
    const rejected=await handleHttp(webRequest(path, "host-owner-token", {method:"POST"}), hostEnv);
    expect(rejected.status).toBe(405);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("keeps Web Host operations scope-exact, bounded, and server-credentialed", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("ok"));
    const hostEnv: Env = {
      ...webEnv,
      HOST_VPC: { fetch } as unknown as Fetcher,
      HOST_UPSTREAM_TOKEN: "server-host-secret",
    };
    const toolCall = (
      name: string,
      token: string,
      value: unknown,
      headers: HeadersInit = {},
    ) => {
      const selected = new Headers(headers);
      selected.set("Content-Type", "application/json");
      return handleHttp(
        webRequest(`/web/site/host/v1/${name}`, token, {
          method: "POST",
          headers: selected,
          body: JSON.stringify(value),
        }),
        hostEnv,
      );
    };

    expect((await toolCall("host_files", "host-read-token", {
      root: "workspace",
      operation: "trash",
      path: "notes.txt",
      expected_version: "v1",
    })).status).toBe(403);
    expect((await toolCall("host_transfer", "host-read-token", {
      root: "workspace",
      path: "upload.bin",
      direction: "upload",
      size: 3,
      expected_version: "absent",
    })).status).toBe(403);
    expect((await toolCall("host_files", "host-write-token", {
      root: "workspace",
      operation: "list",
    })).status).toBe(403);
    expect((await toolCall("flow_work_list", "host-write-token", {
      workspace_id: "workspace",
    })).status).toBe(403);
    expect((await toolCall("flow_snapshot_create", "host-read-token", {
      workspace_id: "workspace", work_id: "work", artifact_id: "artifact",
      expected_revision: 0, idempotency_key: "save-attempt-1",
    })).status).toBe(403);
    expect((await toolCall("flow_asset_import", "host-read-token", {
      workspace_id: "workspace", root: "workspace", path: "photos/part.png", expected_version: "sha256:" + "a".repeat(64),
    })).status).toBe(403);
    expect((await toolCall("flow_change_submit", "host-read-token", {
      workspace_id: "workspace", work_id: "work", mode: "add",
      artifact: {}, idempotency_key: "flow-attempt-1",
    })).status).toBe(403);
    expect((await toolCall("flow_change_action", "host-read-token", {
      workspace_id: "workspace", change_id: "change", action: "apply",
    })).status).toBe(403);
    expect((await toolCall("host_exec", "host-write-token", {
      root: "workspace",
      argv: ["id"],
    })).status).toBe(404);
    expect(fetch).not.toHaveBeenCalled();

    const oversized = await toolCall(
      "host_write",
      "host-write-token",
      {},
      { "Content-Length": String(16 * 1024 * 1024 + 1) },
    );
    expect(oversized.status).toBe(413);
    expect(fetch).not.toHaveBeenCalled();

    const unboundedRequest = webRequest(
      "/web/site/host/v1/host_write",
      "host-write-token",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "x".repeat(16 * 1024 * 1024 + 1),
      },
    );
    expect(unboundedRequest.headers.get("Content-Length")).toBeNull();
    const unbounded = await handleHttp(unboundedRequest, hostEnv);
    expect(unbounded.status).toBe(413);
    expect(fetch).not.toHaveBeenCalled();

    const transferToken = "a".repeat(43);
    const preview = await handleHttp(
      webRequest(
        "/web/site/host/v1/transfers/abcdefghijkl/status?preview=1",
        "host-read-token",
        { headers: { "X-Toolkit-Transfer-Token": transferToken } },
      ),
      hostEnv,
    );
    expect(preview.status).toBe(200);
    expect(fetch).toHaveBeenCalledOnce();
    const [target, init] = fetch.mock.calls[0]! as [string, RequestInit];
    expect(target).toBe(
      "http://spark-host/transfers/abcdefghijkl/status?preview=1",
    );
    const forwarded = new Headers(init.headers);
    expect(forwarded.get("Authorization")).toBe("Bearer server-host-secret");
    expect(forwarded.get("X-Toolkit-Transfer-Token")).toBe(transferToken);
  });

  it("lets the owner find and open saved Flow work through scoped Web operations", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(Response.json({ jsonrpc: "2.0", id: "saved-list", result: {
        content: [], structuredContent: { sources: [{ id: "snapshot:one", title: "작업물" }], nextOffset: null },
      } }))
      .mockResolvedValueOnce(Response.json({ jsonrpc: "2.0", id: "saved-read", result: {
        content: [], structuredContent: { source: { id: "snapshot:one", artifact: { kind: "content" } } },
      } }));
    const hostEnv: Env = {
      ...webEnv,
      HOST_VPC: { fetch } as unknown as Fetcher,
      HOST_UPSTREAM_TOKEN: "server-host-secret",
    };
    const call = (name: string, token: string, args: unknown) => handleHttp(
      webRequest(`/web/site/host/v1/${name}`, token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(args),
      }), hostEnv,
    );
    expect((await call("flow_snapshot_list", "host-write-token", { workspace_id: "workspace" })).status).toBe(403);
    expect((await call("flow_snapshot_list", "host-read-token", { workspace_id: "workspace", limit: 0 })).status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();

    const listed = await call("flow_snapshot_list", "host-read-token", {
      workspace_id: "workspace", query: "작업", limit: 50,
    });
    expect(listed.status).toBe(200);
    expect(await body(listed)).toMatchObject({ sources: [{ id: "snapshot:one" }] });
    const opened = await call("flow_snapshot_read", "host-read-token", {
      workspace_id: "workspace", source_id: "snapshot:one",
    });
    expect(opened.status).toBe(200);
    expect(await body(opened)).toMatchObject({ source: { artifact: { kind: "content" } } });
    expect(fetch.mock.calls.map(([, init]) => JSON.parse(String(init.body)).params.name))
      .toEqual(["flow_snapshot_list", "flow_snapshot_read"]);
    for (const [, init] of fetch.mock.calls as Array<[string, RequestInit]>) {
      expect(new Headers(init.headers).get("Authorization")).toBe("Bearer server-host-secret");
    }
  });

  it("routes scoped Flow library reads and writes through the owner web path", async () => {
    const fetch = vi.fn(async (_url:string, _init:RequestInit) => Response.json({jsonrpc:"2.0",id:"library",result:{content:[],structuredContent:{entries:[],nextOffset:null}}}));
    const hostEnv: Env = {...webEnv,HOST_VPC:{fetch} as unknown as Fetcher,HOST_UPSTREAM_TOKEN:"server-host-secret"};
    const call = (name:string,token:string,args:unknown) => handleHttp(webRequest(`/web/site/host/v1/${name}`,token,{
      method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(args),
    }),hostEnv);
    const list={workspace_id:"workspace",query:"보고서",limit:100};
    const read={workspace_id:"workspace",entry_id:"material-one"};
    const upsert={workspace_id:"workspace",entry:{id:"material-one",title:"보고서",body:"내용",scope:{kind:"workspace"}},expected_revision:0,idempotency_key:"material-attempt-1"};
    expect((await call("flow_library_list","host-write-token",list)).status).toBe(403);
    expect((await call("flow_library_read","host-write-token",read)).status).toBe(403);
    expect((await call("flow_library_upsert","host-read-token",upsert)).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
    expect((await call("flow_library_list","host-read-token",list)).status).toBe(200);
    expect((await call("flow_library_read","host-read-token",read)).status).toBe(200);
    expect((await call("flow_library_upsert","host-write-token",upsert)).status).toBe(200);
    expect(fetch.mock.calls.map(([,init])=>JSON.parse(String(init.body)).params.name)).toEqual(["flow_library_list","flow_library_read","flow_library_upsert"]);
  });

  it("preserves wrapped Host conflict codes on the OAuth web route", async () => {
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
    const response = await handleHttp(
      webRequest("/web/site/host/v1/host_write", "host-owner-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          root: "workspace",
          path: "notes.md",
          content: "draft",
          expected_version: "old",
        }),
      }),
      {
        ...webEnv,
        HOST_VPC: { fetch } as unknown as Fetcher,
        HOST_UPSTREAM_TOKEN: "server-host-secret",
      },
    );
    expect(response.status).toBe(409);
    expect(await body(response)).toMatchObject({
      error: { code: "version_conflict" },
    });
  });

  it("keeps the legacy product site-token routes working", async () => {
    const journal = await handleJournalLegacy(
      new Request("https://journal.test/api/v1/board", {
        headers: { Authorization: "Bearer journal-site-token" },
      }),
      {
        DB: runtime.JOURNAL_DB,
        JOURNAL_RESOURCE: "https://journal.test/mcp",
        AUTH_ISSUER: "https://auth.test",
        JOURNAL_SITE_TOKEN: "journal-site-token",
      } as Parameters<typeof handleJournalLegacy>[1],
    );
    expect(journal.status).toBe(200);

    const library = await handleLibraryLegacy(
      new Request("https://library.test/api/v1/issues", {
        headers: { Authorization: "Bearer library-site-token" },
      }),
      {
        DB: runtime.LIBRARY_DB,
        MEDIA: runtime.LIBRARY_MEDIA,
        LIBRARY_SITE_TOKEN: "library-site-token",
      } as Parameters<typeof handleLibraryLegacy>[1],
    );
    expect(library.status).toBe(200);

    const design = await handleDesignLegacy(
      new Request("https://design.test/api/v1/catalog", {
        headers: { Authorization: "Bearer design-site-token" },
      }),
      {
        DB: runtime.DESIGN_DB,
        ASSETS: runtime.DESIGN_ASSETS,
        DESIGN_SITE_TOKEN: "design-site-token",
      } as Parameters<typeof handleDesignLegacy>[1],
    );
    expect(design.status).toBe(200);
  });
});
