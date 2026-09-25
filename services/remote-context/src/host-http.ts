import { authenticateSite, json, readJson } from "./context-site";
import { ContextError } from "./errors";
import { callHost, hostFailureDetails, HOST_TOOLS, transferReceipt } from "./host";
import type { Env } from "./types";

const TOKEN_HEADER = "X-Toolkit-Transfer-Token";
const SITE_TOOLS = new Set([
  "host_capabilities", "host_roots", "host_read", "host_write", "host_files", "host_transfer",
  "flow_library_list","flow_library_read","flow_library_upsert","flow_workspace_list", "flow_work_list", "flow_work_read",
  "flow_work_create", "flow_work_update", "flow_snapshot_list", "flow_snapshot_read", "flow_snapshot_create", "flow_asset_import", "flow_change_submit", "flow_change_action",
]);

const FLOW_MEDIA = /^\/site\/host\/v1\/flow-media\/([a-z0-9][a-z0-9._-]*)\/(assets|examples|files)\/([A-Za-z0-9._-]+)$/;

export async function forwardFlowMedia(request: Request, env: Env, match: RegExpExecArray): Promise<Response> {
  if (!["GET", "HEAD"].includes(request.method))
    return json({ error: { code: "method_not_allowed" } }, 405);
  const url = new URL(request.url);
  const path = url.searchParams.get("path");
  const page = url.searchParams.get("page");
  const validPage = page === null || match[3] === "preview" && /^(?:[1-9]\d{0,3}|10000)$/.test(page);
  if (match[2] === "files" && (!["content","preview"].includes(match[3] ?? "") || !path || path.length > 2048 || !validPage))
    return json({ error: { code: "invalid_path" } }, 400);
  if (match[2] !== "files" && (path !== null || page !== null))
    return json({ error: { code: "invalid_path" } }, 400);
  if (!env.HOST_VPC || !env.HOST_UPSTREAM_TOKEN)
    throw new ContextError("host_unavailable", "Host is not configured", 503);
  const headers = new Headers({ Authorization: `Bearer ${env.HOST_UPSTREAM_TOKEN}` });
  const range = request.headers.get("Range");
  if (range) {
    if (range.length > 80 || !/^bytes=\d*-\d*$/.test(range))
      return json({ error: { code: "invalid_range" } }, 400);
    headers.set("Range", range);
  }
  const target = new URL(`http://spark-host/flow-media/${match[1]}/${match[2]}/${match[3]}`);
  if (path) target.searchParams.set("path", path);
  if (page) target.searchParams.set("page", page);
  let response: Response;
  try {
    response = await env.HOST_VPC.fetch(target.toString(), {
      method: request.method, headers, redirect: "manual",
    });
  } catch {
    throw new ContextError("host_unreachable", "Host did not answer", 503);
  }
  const outgoing = new Headers(response.headers);
  outgoing.delete("set-cookie");
  outgoing.set("Cache-Control", "private, no-store");
  outgoing.set("Referrer-Policy", "no-referrer");
  return new Response(request.method === "HEAD" || [204, 205, 304].includes(response.status) ? null : response.body, { status: response.status, headers: outgoing });
}

export async function handleHostHttp(request: Request, env: Env): Promise<Response | null> {
  const url = new URL(request.url);
  const siteTool = /^\/site\/host\/v1\/((?:host|flow)_[a-z_]+)$/.exec(url.pathname);
  if (siteTool) {
    if (request.method !== "POST") return json({ error: { code: "method_not_allowed" } }, 405);
    authenticateSite(request, env);
    const name = siteTool[1]!;
    const tool = HOST_TOOLS.find(item => item.name === name);
    if (!tool || !SITE_TOOLS.has(name)) throw new ContextError("not_found", "File operation was not found", 404);
    const parsed = tool.schema.safeParse(await readJson(request));
    if (!parsed.success) throw new ContextError("invalid_request", "File operation arguments are invalid");
    // Browser deletes use host_files/trash, never the legacy permanent-delete flag.
    if (name === "host_write" && parsed.data.delete === true)
      throw new ContextError("invalid_request", "Use the workspace trash operation");
    if (name === "host_write" && typeof parsed.data.expected_version !== "string")
      throw new ContextError("invalid_request", "A file version is required");
    let result = await callHost(env, name, parsed.data);
    if (name === "host_transfer") result = transferReceipt(result, env);
    if (result.isError) {
      const { code, message, status } = hostFailureDetails(result);
      return json({ error: { code, message } }, status);
    }
    return json(result.structuredContent);
  }

  const media = FLOW_MEDIA.exec(url.pathname);
  if (media) {
    authenticateSite(request, env);
    return forwardFlowMedia(request, env, media);
  }

  const transfer = /^\/(host|site\/host)\/v1\/transfers\/([A-Za-z0-9_-]{12,80})\/(status|chunk|commit|content)$/.exec(url.pathname);
  if (!transfer) return null;
  if (transfer[1] === "site/host") authenticateSite(request, env);
  const token = request.headers.get(TOKEN_HEADER) ?? "";
  // A random, expiring, file-scoped capability issued by an authenticated owner
  // call authorizes public binary transport. The Host validates its stored hash.
  if (!/^[A-Za-z0-9_-]{43}$/.test(token))
    return json({ error: { code: "not_found", message: "Transfer is unavailable" } }, 404);
  const action = transfer[3]!;
  const allowed = { status: ["GET", "DELETE"], chunk: ["PUT"], commit: ["POST"], content: ["GET"] }[action];
  if (!allowed?.includes(request.method)) return json({ error: { code: "method_not_allowed" } }, 405);
  if (!env.HOST_VPC || !env.HOST_UPSTREAM_TOKEN)
    throw new ContextError("host_unavailable", "Host is not configured", 503);
  const headers = new Headers({
    Authorization: `Bearer ${env.HOST_UPSTREAM_TOKEN}`,
    [TOKEN_HEADER]: token,
  });
  for (const name of ["Content-Type", "Upload-Offset", "Range"]) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const declared = Number(request.headers.get("Content-Length") ?? "0");
  if (declared > 8 * 1024 * 1024) throw new ContextError("request_too_large", "Chunk exceeds 8 MiB", 413);
  const target = new URL(`http://spark-host/transfers/${transfer[2]}/${action}`);
  if (url.searchParams.get("preview") === "1") target.searchParams.set("preview", "1");
  try {
    const response = await env.HOST_VPC.fetch(target.toString(), {
      method: request.method, headers,
      body: request.method === "PUT" ? request.body : null,
      redirect: "manual",
    });
    const outgoing = new Headers(response.headers);
    outgoing.delete("set-cookie");
    outgoing.set("Cache-Control", "private, no-store");
    outgoing.set("Referrer-Policy", "no-referrer");
    return new Response(request.method === "HEAD" || [204, 205, 304].includes(response.status) ? null : response.body, { status: response.status, headers: outgoing });
  } catch {
    throw new ContextError("host_unreachable", "Host did not answer", 503);
  }
}
