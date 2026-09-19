import { authenticateSite, json, readJson } from "./context-site";
import { ContextError } from "./errors";
import { callHost, HOST_TOOLS, transferReceipt } from "./host";
import type { Env } from "./types";

const TOKEN_HEADER = "X-Toolkit-Transfer-Token";
const SITE_TOOLS = new Set(["host_capabilities", "host_roots", "host_read", "host_write", "host_files", "host_transfer"]);

export async function handleHostHttp(request: Request, env: Env): Promise<Response | null> {
  const url = new URL(request.url);
  const siteTool = /^\/site\/host\/v1\/(host_[a-z_]+)$/.exec(url.pathname);
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
      const blocks = result.content as Array<{ text?: string }>;
      const message = blocks.find(block => typeof block.text === "string")?.text ?? "File operation failed";
      const code = /\b([a-z_]+):/.exec(message)?.[1] ?? "host_error";
      const status = code === "version_conflict" ? 409 : code === "policy_denied" ? 403 : code === "not_found" ? 404 : 400;
      return json({ error: { code, message } }, status);
    }
    return json(result.structuredContent);
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
    return new Response(response.body, { status: response.status, headers: outgoing });
  } catch {
    throw new ContextError("host_unreachable", "Host did not answer", 503);
  }
}
