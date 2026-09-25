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

  it("exposes registered Flow reads without opening execution to the site", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({
      jsonrpc: "2.0", id: "flow-list", result: {
        content: [], structuredContent: { workspaces: [{ id: "workspace", permission: "read_write" }] },
      },
    }));
    const response = await handleHostHttp(new Request(
      "https://worker.example/site/host/v1/flow_workspace_list", {
        method: "POST", headers: { ...siteHeaders, "Content-Type": "application/json" }, body: "{}",
      }), env(fetch));
    expect(response?.status).toBe(200);
    expect(await response?.json()).toMatchObject({ workspaces: [{ id: "workspace" }] });
    const [, init] = fetch.mock.calls[0]! as [string, RequestInit];
    const forwarded = JSON.parse(String(init.body));
    expect(forwarded.params.name).toBe("flow_workspace_list");
  });

  it("forwards an explicit Flow review action to the registered Host tool", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({
      jsonrpc: "2.0", id: "flow-action", result: {
        content: [], structuredContent: { change: { id: "change-1", status: "completed" } },
      },
    }));
    const response = await handleHostHttp(new Request(
      "https://worker.example/site/host/v1/flow_change_action", {
        method: "POST", headers: { ...siteHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({workspace_id:"workspace",change_id:"change-1",action:"apply"}),
      }), env(fetch));
    expect(response?.status).toBe(200);
    expect(await response?.json()).toMatchObject({change:{id:"change-1",status:"completed"}});
    const [, init] = fetch.mock.calls[0]! as [string, RequestInit];
    expect(JSON.parse(String(init.body)).params).toMatchObject({
      name: "flow_change_action",
      arguments: {workspace_id:"workspace",change_id:"change-1",action:"apply"},
    });
    await expect(handleHostHttp(new Request(
      "https://worker.example/site/host/v1/flow_change_action", {
        method: "POST", headers: { ...siteHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({workspace_id:"workspace",change_id:"change-1",action:"erase"}),
      }), env(fetch))).rejects.toMatchObject({code:"invalid_request",status:400});
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("reads saved Flow work through the registered Host tools", async () => {
    const fetch=vi.fn()
      .mockResolvedValueOnce(Response.json({jsonrpc:"2.0",id:"flow-saved-list",result:{content:[],structuredContent:{sources:[{id:"snapshot:one"}],nextOffset:null}}}))
      .mockResolvedValueOnce(Response.json({jsonrpc:"2.0",id:"flow-saved-read",result:{content:[],structuredContent:{source:{id:"snapshot:one",artifact:{kind:"content"}}}}}));
    const base="https://worker.example/site/host/v1/";
    const headers={...siteHeaders,"Content-Type":"application/json"};
    const listed=await handleHostHttp(new Request(base+"flow_snapshot_list",{method:"POST",headers,body:JSON.stringify({workspace_id:"workspace",limit:50})}),env(fetch));
    expect(await listed?.json()).toMatchObject({sources:[{id:"snapshot:one"}]});
    const saved=await handleHostHttp(new Request(base+"flow_snapshot_read",{method:"POST",headers,body:JSON.stringify({workspace_id:"workspace",source_id:"snapshot:one"})}),env(fetch));
    expect(await saved?.json()).toMatchObject({source:{artifact:{kind:"content"}}});
    expect(fetch.mock.calls.map(([,init])=>JSON.parse(String(init.body)).params.name)).toEqual(["flow_snapshot_list","flow_snapshot_read"]);
  });

  it("starts a Flow work from an exact saved source through the registered Host tool", async () => {
    const fetch=vi.fn().mockResolvedValue(Response.json({jsonrpc:"2.0",id:"flow-create",result:{
      content:[],structuredContent:{work:{id:"work-new",sourceIds:["snapshot:one"]}},
    }}));
    const path="https://worker.example/site/host/v1/flow_work_create";
    const input={workspace_id:"workspace",name:"이어갈 작업",source_id:"snapshot:one",idempotency_key:"create-attempt-1"};
    const response=await handleHostHttp(new Request(path,{method:"POST",headers:{...siteHeaders,"Content-Type":"application/json"},body:JSON.stringify(input)}),env(fetch));
    expect(response?.status).toBe(200);
    expect(await response?.json()).toMatchObject({work:{sourceIds:["snapshot:one"]}});
    const [,init]=fetch.mock.calls[0]! as [string,RequestInit];
    expect(JSON.parse(String(init.body)).params).toMatchObject({name:"flow_work_create",arguments:input});
    await expect(handleHostHttp(new Request(path,{method:"POST",headers:{...siteHeaders,"Content-Type":"application/json"},body:JSON.stringify({...input,artifact:{kind:"blank"}})}),env(fetch)))
      .rejects.toMatchObject({code:"invalid_request",status:400});
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("forwards a new work with its Toolkit source link and content block", async () => {
    const fetch=vi.fn().mockResolvedValue(Response.json({jsonrpc:"2.0",id:"flow-resource-create",result:{
      content:[],structuredContent:{work:{id:"work-from-resource"}},
    }}));
    const reference={kind:"journal-item",id:"123e4567-e89b-42d3-a456-426614174000"};
    const input={workspace_id:"workspace",name:"기록 검토",artifact:{kind:"content"},
      linked_resources:[reference],idempotency_key:"create-resource-1"};
    const path="https://worker.example/site/host/v1/flow_work_create";
    const response=await handleHostHttp(new Request(path,{method:"POST",headers:{...siteHeaders,"Content-Type":"application/json"},body:JSON.stringify(input)}),env(fetch));
    expect(response?.status).toBe(200);
    expect(await response?.json()).toMatchObject({work:{id:"work-from-resource"}});
    const [,init]=fetch.mock.calls[0]! as [string,RequestInit];
    expect(JSON.parse(String(init.body)).params).toMatchObject({name:"flow_work_create",arguments:input});
  });

  it("saves a Flow artifact version through the registered Host tool", async () => {
    const fetch=vi.fn().mockResolvedValue(Response.json({jsonrpc:"2.0",id:"flow-snapshot",result:{
      content:[],structuredContent:{source:{id:"snapshot:one",artifactId:"artifact-1",artifactRevision:2}},
    }}));
    const path="https://worker.example/site/host/v1/flow_snapshot_create";
    const input={workspace_id:"workspace",work_id:"work-1",artifact_id:"artifact-1",expected_revision:2,idempotency_key:"save-attempt-1"};
    const response=await handleHostHttp(new Request(path,{method:"POST",headers:{...siteHeaders,"Content-Type":"application/json"},body:JSON.stringify(input)}),env(fetch));
    expect(response?.status).toBe(200);
    expect(await response?.json()).toMatchObject({source:{id:"snapshot:one"}});
    const [,init]=fetch.mock.calls[0]! as [string,RequestInit];
    expect(JSON.parse(String(init.body)).params).toMatchObject({name:"flow_snapshot_create",arguments:input});
    await expect(handleHostHttp(new Request(path,{method:"POST",headers:{...siteHeaders,"Content-Type":"application/json"},body:JSON.stringify({...input,expected_revision:-1})}),env(fetch)))
      .rejects.toMatchObject({code:"invalid_request",status:400});
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("allows only a scoped registered Flow image import from the site", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({
      jsonrpc: "2.0", id: "asset-import", result: {
        content: [], structuredContent: {src:"/api/flow/assets/"+"a".repeat(64)+".png",mime:"image/png"},
      },
    }));
    const path="https://worker.example/site/host/v1/flow_asset_import";
    const input={workspace_id:"workspace",root:"workspace",path:"photos/part.png",expected_version:"sha256:"+"a".repeat(64)};
    const response=await handleHostHttp(new Request(path,{method:"POST",headers:{...siteHeaders,"Content-Type":"application/json"},body:JSON.stringify(input)}),env(fetch));
    expect(response?.status).toBe(200);
    expect(await response?.json()).toMatchObject({mime:"image/png"});
    const [,init]=fetch.mock.calls[0]! as [string,RequestInit];
    expect(JSON.parse(String(init.body)).params).toMatchObject({name:"flow_asset_import",arguments:input});
    await expect(handleHostHttp(new Request(path,{method:"POST",headers:{...siteHeaders,"Content-Type":"application/json"},body:JSON.stringify({...input,path:""})}),env(fetch)))
      .rejects.toMatchObject({code:"invalid_request",status:400});
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("keeps a changed Flow image file as a version conflict", async () => {
    const fetch=vi.fn().mockResolvedValue(Response.json({jsonrpc:"2.0",id:"flow-import",result:{
      isError:true,content:[{type:"text",text:"Error executing tool flow_asset_import: version_conflict: 이미지 파일이 변경되었습니다"}],
    }}));
    const response=await handleHostHttp(new Request("https://worker.example/site/host/v1/flow_asset_import",{
      method:"POST",headers:{...siteHeaders,"Content-Type":"application/json"},
      body:JSON.stringify({workspace_id:"workspace",root:"workspace",path:"photos/part.png",expected_version:"sha256:"+"a".repeat(64)}),
    }),env(fetch));
    expect(response?.status).toBe(409);
    expect(await response?.json()).toMatchObject({error:{code:"version_conflict"}});
  });

  it("serves registered Flow media through Host without exposing credentials", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("image-bytes", {
      status: 206,
      headers: { "Content-Type": "image/png", "Content-Range": "bytes 0-10/11", "Set-Cookie": "host=secret" },
    }));
    const path = "https://worker.example/site/host/v1/flow-media/workspace/examples/metal.png";
    await expect(handleHostHttp(new Request(path), env(fetch)))
      .rejects.toMatchObject({ code: "invalid_site_credential", status: 401 });
    const response = await handleHostHttp(new Request(path, {
      headers: { ...siteHeaders, Range: "bytes=0-10" },
    }), env(fetch));
    expect(response?.status).toBe(206);
    expect(new TextDecoder().decode(await response?.arrayBuffer())).toBe("image-bytes");
    expect(response?.headers.get("Set-Cookie")).toBeNull();
    expect(response?.headers.get("Content-Range")).toBe("bytes 0-10/11");
    const [target, init] = fetch.mock.calls[0]! as [string, RequestInit];
    expect(target).toBe("http://spark-host/flow-media/workspace/examples/metal.png");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer host-secret");
    expect(new Headers(init.headers).get("Range")).toBe("bytes=0-10");
    const invalid = await handleHostHttp(new Request(path, {
      headers: { ...siteHeaders, Range: "bytes=0-1,2-3" },
    }), env(fetch));
    expect(invalid?.status).toBe(400);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("forwards one PDF page and its page count without exposing Host credentials", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, {
      status: 200, headers: { "Content-Type": "image/png", "X-Flow-Pdf-Pages": "3" },
    }));
    const path = "https://worker.example/site/host/v1/flow-media/workspace/files/preview?path=docs%2Freport.pdf&page=2";
    const response = await handleHostHttp(new Request(path, {method:"HEAD",headers:siteHeaders}), env(fetch));
    expect(response?.status).toBe(200);
    expect(response?.headers.get("X-Flow-Pdf-Pages")).toBe("3");
    const [target,init] = fetch.mock.calls[0]! as [string,RequestInit];
    expect(target).toBe("http://spark-host/flow-media/workspace/files/preview?path=docs%2Freport.pdf&page=2");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer host-secret");
    const invalid = await handleHostHttp(new Request(path.replace("page=2","page=0"),{method:"HEAD",headers:siteHeaders}),env(fetch));
    expect(invalid?.status).toBe(400);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("forwards a registered text file preview without exposing Host credentials", async () => {
    const payload = { type: "text/plain", path: "notes/memo.md", content: "첫 메모" };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 200, headers: { "Content-Type": "application/json", "Set-Cookie": "host=secret" },
    }));
    const path = "https://worker.example/site/host/v1/flow-media/workspace/files/content?path=notes%2Fmemo.md";
    const response = await handleHostHttp(new Request(path, { headers: siteHeaders }), env(fetch));
    expect(response?.status).toBe(200);
    expect(response?.headers.get("Content-Type")).toBe("application/json");
    expect(response?.headers.get("Set-Cookie")).toBeNull();
    expect(await response?.json()).toEqual(payload);
    const [target, init] = fetch.mock.calls[0]! as [string, RequestInit];
    expect(target).toBe("http://spark-host/flow-media/workspace/files/content?path=notes%2Fmemo.md");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer host-secret");
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
