import { authenticateMcp, requireScope, supportedScopes } from "./auth";
import { handlePreauthenticatedHttp as handleJournalHttp } from "personal-agent-journal-service/http";
import { handlePreauthenticatedHttp as handleLibraryHttp } from "personal-agent-library-service/http";
import { handlePreauthenticatedHttp as handleDesignHttp } from "personal-agent-design-service/http";
import { handleAdminSite } from "./admin-site";
import { executeContextSiteOperation, readJson } from "./context-site";
import { asContextError, ContextError } from "./errors";
import { callHost, hostFailureDetails, HOST_TOOLS, hostRequiredScope, transferReceipt } from "./host";
import type { Env } from "./types";

const TRANSFER_TOKEN = "X-Toolkit-Transfer-Token";
const WEB_HOST_TOOLS = new Set([
  "host_capabilities", "host_roots", "host_read", "host_write",
  "host_files", "host_transfer",
]);

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "private, no-store" } });
}

type WebProduct = "journal" | "library" | "design";

const PRODUCT_SCOPES: Record<WebProduct, readonly string[]> = {
  journal: ["journal.read", "journal.write", "journal.close"],
  library: ["library.read", "library.write"],
  design: ["design.read", "design.write"],
};

function productScopes(
  request: Request,
  product: WebProduct,
  pathname: string,
): readonly string[] {
  if (pathname.startsWith(`/web/${product}/api/v1/operations/`)) {
    return PRODUCT_SCOPES[product];
  }
  if (product === "journal") {
    if (request.method === "GET") return ["journal.read"];
    if (
      request.method === "POST"
      && /^\/web\/journal\/api\/v1\/weeks\/\d{4}-\d{2}-\d{2}:(?:prepare-close|confirm-close|close)$/.test(pathname)
    ) {
      return ["journal.close"];
    }
    return ["journal.write"];
  }
  if (request.method === "GET" || request.method === "HEAD") {
    return [`${product}.read`];
  }
  return [`${product}.write`];
}

function productRequest(request: Request, url: URL, prefix: string): Request {
  const target = new URL(url);
  target.pathname = url.pathname.slice(prefix.length);
  const headers = new Headers(request.headers);
  headers.delete("Authorization");
  headers.delete("Cookie");
  headers.delete("Origin");
  headers.delete("X-Personal-Agent-Site-User-Id");
  return new Request(target.href, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD"
      ? null
      : request.body,
    redirect: "manual",
  });
}

async function productHttp(
  request: Request,
  env: Env,
  product: WebProduct,
): Promise<Response> {
  const principal = await authenticateMcp(
    request,
    env,
    "toolkit",
    productScopes(request, product, new URL(request.url).pathname),
  );
  const internal = productRequest(
    request,
    new URL(request.url),
    `/web/${product}`,
  );
  if (product === "journal") {
    return handleJournalHttp(
      internal,
      {
        DB: env.JOURNAL_DB,
        JOURNAL_RESOURCE: env.TOOLKIT_RESOURCE,
        AUTH_ISSUER: env.AUTH_ISSUER,
      },
      {
        kind: "owner",
        id: principal.ownerId,
        scopes: principal.scopes,
        auth: "oauth",
      },
    );
  }
  const actor = {
    ownerId: principal.ownerId,
    clientId: principal.clientId,
    kind: "owner" as const,
    scopes: principal.scopes,
  };
  if (product === "library") {
    return handleLibraryHttp(
      internal,
      {
        DB: env.LIBRARY_DB,
        MEDIA: env.LIBRARY_MEDIA,
        ...(env.LIBRARY_MANAGEMENT_WRITE_ENABLED === undefined
          ? {}
          : { MANAGEMENT_WRITE_ENABLED: env.LIBRARY_MANAGEMENT_WRITE_ENABLED }),
      },
      actor,
      principal.owner,
    );
  }
  return handleDesignHttp(
    internal,
    {
      DB: env.DESIGN_DB,
      ASSETS: env.DESIGN_ASSETS,
      ...(env.DESIGN_MANAGEMENT_WRITE_ENABLED === undefined
        ? {}
        : { MANAGEMENT_WRITE_ENABLED: env.DESIGN_MANAGEMENT_WRITE_ENABLED }),
    },
    actor,
  );
}

async function hostTool(request: Request, env: Env, name: string): Promise<Response> {
  const tool = HOST_TOOLS.find((item) => item.name === name);
  if (!tool || !WEB_HOST_TOOLS.has(name)) throw new ContextError("not_found", "File operation was not found", 404);
  const principal = await authenticateMcp(
    request,
    env,
    "toolkit",
    ["host.read", "host.write"],
  );
  const parsed = tool.schema.safeParse(await readJson(request));
  if (!parsed.success) throw new ContextError("invalid_request", "File operation arguments are invalid", 400);
  requireScope(
    principal,
    hostRequiredScope(name, parsed.data as Record<string, unknown>),
  );
  if (name === "host_write" && parsed.data.delete === true) throw new ContextError("invalid_request", "Use the workspace trash operation", 400);
  if (name === "host_write" && typeof parsed.data.expected_version !== "string") throw new ContextError("invalid_request", "A file version is required", 400);
  let result = await callHost(env, name, parsed.data);
  if (name === "host_transfer") result = transferReceipt(result, env);
  if (result.isError) {
    const { code, message, status } = hostFailureDetails(result);
    return json({ error: { code, message } }, status);
  }
  return json(result.structuredContent);
}

async function hostTransfer(request: Request, env: Env, match: RegExpExecArray): Promise<Response> {
  const action = match[2]!;
  const scope = action === "content" || (action === "status" && request.method === "GET")
    ? "host.read"
    : "host.write";
  await authenticateMcp(request, env, "toolkit", [scope]);
  const token = request.headers.get(TRANSFER_TOKEN) ?? "";
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) return json({ error: { code: "not_found", message: "Transfer is unavailable" } }, 404);
  const methods: Record<string, string[]> = { status: ["GET", "DELETE"], chunk: ["PUT"], commit: ["POST"], content: ["GET"] };
  if (!methods[action]?.includes(request.method)) return json({ error: { code: "method_not_allowed" } }, 405);
  if (!env.HOST_VPC || !env.HOST_UPSTREAM_TOKEN) throw new ContextError("host_unavailable", "Host is not configured", 503);
  if (Number(request.headers.get("Content-Length") ?? "0") > 8 * 1024 * 1024) throw new ContextError("request_too_large", "Chunk exceeds 8 MiB", 413);
  const headers = new Headers({ Authorization: `Bearer ${env.HOST_UPSTREAM_TOKEN}`, [TRANSFER_TOKEN]: token });
  for (const name of ["Content-Type", "Upload-Offset", "Range"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const target = new URL(`http://spark-host/transfers/${match[1]!}/${action}`);
  if (new URL(request.url).searchParams.get("preview") === "1") {
    target.searchParams.set("preview", "1");
  }
  const response = await env.HOST_VPC.fetch(target.toString(), { method: request.method, headers, body: request.method === "PUT" ? request.body : null, redirect: "manual" });
  const outgoing = new Headers(response.headers);
  outgoing.delete("set-cookie");
  outgoing.set("Cache-Control", "private, no-store");
  outgoing.set("Referrer-Policy", "no-referrer");
  return new Response(response.body, { status: response.status, headers: outgoing });
}

export async function handleWeb(request: Request, env: Env): Promise<Response | null> {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/web/")) return null;
  try {
    if (url.pathname === "/web/v1/identity" && request.method === "GET") {
      const principal = await authenticateMcp(request, env, "toolkit", ["sense.read"]);
      return json({ ok: true, result: { owner_id: principal.ownerId } });
    }
    const admin = /^\/web\/admin\/v1\/[a-z][a-z0-9_]{0,95}$/.exec(url.pathname);
    if (admin) {
      const principal = await authenticateMcp(request, env, "toolkit", supportedScopes("toolkit"));
      const internal = new Request(
        new URL(url.pathname.replace(/^\/web/, ""), url).href,
        request,
      );
      return handleAdminSite(internal, env, principal);
    }
    if (/^\/web\/library\/media\/.+/.test(url.pathname)) {
      if (!["GET", "HEAD"].includes(request.method)) {
        throw new ContextError("method_not_allowed", "Media is read-only", 405);
      }
      return productHttp(request, env, "library");
    }
    const product = /^\/web\/(journal|library|design)(\/api\/v1(?:\/.*)?)$/.exec(url.pathname);
    if (product) return productHttp(request, env, product[1]! as WebProduct);
    const transfer = /^\/web\/site\/host\/v1\/transfers\/([A-Za-z0-9_-]{12,80})\/(status|chunk|commit|content)$/.exec(url.pathname);
    if (transfer) return hostTransfer(request, env, transfer);
    const host = /^\/web\/site\/host\/v1\/(host_[a-z_]+)$/.exec(url.pathname);
    if (host && request.method === "POST") return hostTool(request, env, host[1]!);
    const context = /^\/web\/site\/v1\/([a-z][a-z0-9_]{0,95})$/.exec(url.pathname);
    if (!context || request.method !== "POST") throw new ContextError("not_found", "Web operation was not found", 404);
    const principal = await authenticateMcp(request, env, "toolkit", supportedScopes("toolkit"));
    return executeContextSiteOperation(request, env, principal, context[1]!);
  } catch (error) {
    const normalized = asContextError(error);
    return json({ ok: false, error: { code: normalized.code, message: normalized.message } }, normalized.status);
  }
}
