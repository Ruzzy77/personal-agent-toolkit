import { redirect } from "next/navigation";
import {
  OwnerSessionError,
  csrfMatches,
  ownerAccessToken,
  ownerSession,
  ownerSessionFromCookies,
} from "./owner-session";

const JSON_LIMIT = 16 * 1024 * 1024;
const BINARY_LIMIT = 8 * 1024 * 1024;

function webPath(path: string, method = "GET"): string {
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) {
    throw new OwnerSessionError(403, "owner request path is invalid");
  }
  const product = /^(\/site\/v1\/|\/admin\/v1\/|\/site\/host\/v1\/|\/journal\/api\/v1\/|\/library\/api\/v1\/|\/design\/api\/v1\/)/;
  const media = (/^\/library\/media\/.+/.test(path) || /^\/site\/host\/v1\/flow-media\/.+/.test(path)) && ["GET", "HEAD"].includes(method.toUpperCase());
  if (!product.test(path) && !media) {
    throw new OwnerSessionError(403, "owner request path is not available");
  }
  return "/web" + path;
}

function contextUrl(): string {
  const value = process.env.CONTEXT_SERVICE_URL;
  if (!value) throw new OwnerSessionError(500, "owner service is not configured");
  return value.replace(/\/$/, "");
}

function responseHeaders(response: Response): Headers {
  const headers = new Headers(response.headers);
  headers.delete("set-cookie");
  headers.set("Cache-Control", "private, no-store");
  headers.set("Referrer-Policy", "no-referrer");
  return headers;
}

async function readLimited(request: Request): Promise<ArrayBuffer | null> {
  if (!request.body) return null;
  const contentType = request.headers.get("Content-Type")?.toLowerCase() ?? "";
  const limit = contentType.includes("application/json") ? JSON_LIMIT : BINARY_LIMIT;
  const declared = Number(request.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(declared) && declared > limit) {
    throw new OwnerSessionError(413, "owner request is too large");
  }
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const item = await reader.read();
      if (item.done) break;
      size += item.value.byteLength;
      if (size > limit) {
        await reader.cancel();
        throw new OwnerSessionError(413, "owner request is too large");
      }
      chunks.push(item.value);
    }
  } finally {
    reader.releaseLock();
  }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body.buffer;
}

async function send(
  token: string,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return fetch(contextUrl() + webPath(path, init.method), {
    ...init,
    headers,
    cache: "no-store",
    redirect: "manual",
  });
}

export async function requireOwnerUser(returnTo = "/"): Promise<{ownerId: string}> {
  const session = await ownerSessionFromCookies();
  if (!session) redirect("/auth/login?returnTo=" + encodeURIComponent(returnTo));
  try { await ownerAccessToken(session); } catch (error) {
    if (error instanceof OwnerSessionError && error.status === 401) {
      redirect("/auth/login?returnTo=" + encodeURIComponent(returnTo));
    }
    throw error;
  }
  return {ownerId: session.ownerId};
}

export async function requireOwnerApiUser(request: Request): Promise<Response | null> {
  const session = await ownerSession(request);
  if (!session) {
    return Response.json({ error: "authentication_required" }, { status: 401 });
  }
  if (!csrfMatches(session, request)) {
    return Response.json({ error: "csrf_rejected" }, { status: 403 });
  }
  return null;
}

export async function ownerServiceFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const session = await ownerSessionFromCookies();
  if (!session) throw new OwnerSessionError(401, "owner login is required");
  const response = await send(await ownerAccessToken(session), path, init);
  return new Response(response.body, {status:response.status,headers:responseHeaders(response)});
}

export async function proxyOwnerRequest(
  request: Request,
  path: string,
): Promise<Response> {
  const session = await ownerSession(request);
  if (!session) return Response.json({ error: "authentication_required" }, { status: 401 });
  if (!csrfMatches(session, request)) {
    return Response.json({ error: "csrf_rejected" }, { status: 403 });
  }
  try {
    const body = await readLimited(request);
    const headers = new Headers();
    for (const name of ["Content-Type","Upload-Offset","X-Toolkit-Transfer-Token","Range"]) {
      const value = request.headers.get(name);if(value)headers.set(name,value);
    }
    const response = await send(await ownerAccessToken(session), path, {
      method: request.method,
      headers,
      body,
    });
    return new Response(response.body, {
      status: response.status,
      headers: responseHeaders(response),
    });
  } catch (error) {
    if (error instanceof OwnerSessionError) {
      return Response.json(
        { error: error.status === 401 ? "authentication_required" : "request_rejected" },
        { status: error.status },
      );
    }
    return Response.json({ error: "owner_service_unavailable" }, { status: 502 });
  }
}
