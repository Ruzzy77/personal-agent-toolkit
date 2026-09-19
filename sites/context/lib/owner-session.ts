import { env } from "cloudflare:workers";
import { cookies } from "next/headers";

const SESSION_COOKIE = "__Host-personal-agent-owner";
const FLOW_COOKIE = "__Host-personal-agent-flow";
const SESSION_TTL = 30 * 24 * 60 * 60;
const FLOW_TTL = 10 * 60;
const APP_ORIGIN = "https://personal-agent-toolkit.hiyaq77.workers.dev";

type SessionRecord = {
  id?: string;
  validUntil: number;
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
  ownerId: string;
  csrf: string;
};

type FlowRecord = {
  verifier: string;
  bindingHash: string;
  returnTo: string;
};

type SessionEnv = {
  WEB_SESSION_KV?: KVNamespace;
};

export class OwnerSessionError extends Error {
  readonly status: 400 | 401 | 403 | 413 | 500;
  constructor(status: 400 | 401 | 403 | 413 | 500, message: string) {
    super(message); this.status = status;
  }
}

function sessionKv(): KVNamespace {
  const value = (env as unknown as SessionEnv).WEB_SESSION_KV;
  if (!value) throw new OwnerSessionError(500, "owner login is not configured");
  return value;
}

function token(bytes = 32): string {
  const value = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode(...value))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

async function hash(value: string): Promise<string> {
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

function cookie(header: string | null, name: string): string | null {
  for (const entry of (header ?? "").split(";")) {
    const [key, ...rest] = entry.trim().split("=");
    if (key === name) return rest.join("=") || null;
  }
  return null;
}

function cookieHeader(name: string, value: string, ttl: number): string {
  return `${name}=${value}; HttpOnly; Secure; Path=/; SameSite=Lax; Max-Age=${ttl}`;
}

function clearCookie(name: string): string {
  return `${name}=; HttpOnly; Secure; Path=/; SameSite=Lax; Max-Age=0`;
}

function key(kind: "session" | "flow", value: string): string {
  return `personal-agent-web:${kind}:${value}`;
}

function authIssuer(): string {
  return process.env.AUTH_ISSUER ?? "https://personal-agent-auth.hiyaq77.workers.dev";
}

function toolkitResource(): string {
  return process.env.TOOLKIT_RESOURCE
    ?? "https://personal-agent-context.hiyaq77.workers.dev/mcp";
}

function clientId(): string {
  const value = process.env.OAUTH_CLIENT_ID;
  if (!value) throw new OwnerSessionError(500, "owner login is not configured");
  return value;
}

function callbackUrl(): string {
  return new URL("/auth/callback", process.env.APP_ORIGIN ?? APP_ORIGIN).href;
}

function localReturnTo(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\") || /[\u0000-\u001f]/.test(value)) return "/";
  const parsed = new URL(value, appOrigin());
  return parsed.origin === appOrigin() ? parsed.pathname + parsed.search + parsed.hash : "/";
}

function scopes(): string {
  return [
    "sense.read", "sense.write", "corpus.read", "corpus.write",
    "journal.read", "journal.write",
    "journal.close", "library.read", "library.write", "design.read",
    "design.write", "host.read", "host.write",
  ].join(" ");
}

export async function startOwnerLogin(returnTo: string | null): Promise<Response> {
  const state = token();
  const verifier = token(48);
  const binding = token();
  await sessionKv().put(
    key("flow", state),
    JSON.stringify({
      verifier,
      bindingHash: await hash(binding),
      returnTo: localReturnTo(returnTo),
    } satisfies FlowRecord),
    { expirationTtl: FLOW_TTL },
  );
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  const challenge = btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
  const authorize = new URL("/authorize", authIssuer());
  authorize.searchParams.set("response_type", "code");
  authorize.searchParams.set("client_id", clientId());
  authorize.searchParams.set("redirect_uri", callbackUrl());
  authorize.searchParams.set("resource", toolkitResource());
  authorize.searchParams.set("scope", scopes());
  authorize.searchParams.set("state", state);
  authorize.searchParams.set("code_challenge", challenge);
  authorize.searchParams.set("code_challenge_method", "S256");
  return new Response(null, {
    status: 302,
    headers: {
      Location: authorize.href,
      "Cache-Control": "no-store",
      "Referrer-Policy": "no-referrer",
      "Set-Cookie": cookieHeader(FLOW_COOKIE, binding, FLOW_TTL),
    },
  });
}

async function tokenRequest(body: URLSearchParams): Promise<Record<string, unknown>> {
  const response = await fetch(new URL("/oauth/token", authIssuer()), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
    cache: "no-store",
    redirect: "error",
  });
  if (!response.ok) throw new OwnerSessionError(401, "owner login has expired");
  return response.json() as Promise<Record<string, unknown>>;
}

async function ownerId(accessToken: string): Promise<string> {
  const base = process.env.CONTEXT_SERVICE_URL;
  if (!base) throw new OwnerSessionError(500, "owner service is not configured");
  const response = await fetch(`${base.replace(/\/$/, "")}/web/v1/identity`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    cache: "no-store",
  });
  const body = await response.json() as { result?: { owner_id?: unknown } };
  if (!response.ok || typeof body.result?.owner_id !== "string") {
    throw new OwnerSessionError(401, "owner login is not authorized");
  }
  return body.result.owner_id;
}

export async function completeOwnerLogin(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const state = url.searchParams.get("state");
  const code = url.searchParams.get("code");
  if (!state || !/^[A-Za-z0-9_-]{43}$/.test(state) || !code || code.length > 4096 || url.searchParams.get("iss") !== authIssuer()) {
    throw new OwnerSessionError(401, "owner login could not be completed");
  }
  const flow = await sessionKv().get<FlowRecord>(key("flow", state), "json");
  const binding = cookie(request.headers.get("Cookie"), FLOW_COOKIE);
  if (!flow || !binding || (await hash(binding)) !== flow.bindingHash) {
    throw new OwnerSessionError(401, "owner login has expired");
  }
  await sessionKv().delete(key("flow", state));
  const issued = await tokenRequest(new URLSearchParams({
    grant_type: "authorization_code",
    client_id: clientId(),
    code,
    redirect_uri: callbackUrl(),
    code_verifier: flow.verifier,
    resource: toolkitResource(),
  }));
  const accessToken = issued.access_token;
  const refreshToken = issued.refresh_token;
  const expiresIn = issued.expires_in;
  if (
    typeof accessToken !== "string"
    || typeof refreshToken !== "string"
    || typeof expiresIn !== "number"
  ) throw new OwnerSessionError(401, "owner login could not be completed");
  const session = token();
  await sessionKv().put(
    key("session", session),
    JSON.stringify({
      validUntil: Math.floor(Date.now() / 1000) + SESSION_TTL,
      accessToken,
      refreshToken,
      expiresAt: Math.floor(Date.now() / 1000) + expiresIn,
      ownerId: await ownerId(accessToken),
      csrf: token(),
    } satisfies SessionRecord),
    { expirationTtl: SESSION_TTL },
  );
  const headers = new Headers({
    Location: localReturnTo(flow.returnTo),
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
  });
  headers.append("Set-Cookie", cookieHeader(SESSION_COOKIE, session, SESSION_TTL));
  headers.append("Set-Cookie", clearCookie(FLOW_COOKIE));
  return new Response(null, { status: 302, headers });
}

async function readSession(id: string | null | undefined): Promise<SessionRecord | null> {
  if (!id || !/^[A-Za-z0-9_-]{43}$/.test(id)) return null;
  const record = await sessionKv().get<SessionRecord>(key("session", id), "json");
  return record && record.validUntil > Math.floor(Date.now()/1000) ? {...record, id} : null;
}

export async function ownerSession(request: Request): Promise<SessionRecord | null> {
  return readSession(cookie(request.headers.get("Cookie"), SESSION_COOKIE));
}

export async function ownerSessionFromCookies(): Promise<SessionRecord | null> {
  return readSession((await cookies()).get(SESSION_COOKIE)?.value);
}

const refreshing = new Map<string, Promise<string>>();
export async function ownerAccessToken(session: SessionRecord): Promise<string> {
  if (session.expiresAt > Math.floor(Date.now() / 1000) + 60) return session.accessToken;
  if (!session.id) throw new OwnerSessionError(401, "owner login has expired");
  const pending = refreshing.get(session.id);
  if (pending) return pending;
  const refresh = (async () => {
    const current = await readSession(session.id);
    if (!current) throw new OwnerSessionError(401, "owner login has expired");
    if (current.expiresAt > Math.floor(Date.now()/1000)+60) return current.accessToken;
    const issued = await tokenRequest(new URLSearchParams({
      grant_type: "refresh_token", client_id: clientId(),
      refresh_token: current.refreshToken, resource: toolkitResource(),
    }));
    if (typeof issued.access_token !== "string" || typeof issued.expires_in !== "number"
        || !Number.isFinite(issued.expires_in) || issued.expires_in <= 0) {
      throw new OwnerSessionError(401, "owner login has expired");
    }
    const renewed: SessionRecord = {
      ...current, accessToken: issued.access_token,
      refreshToken: typeof issued.refresh_token === "string" ? issued.refresh_token : current.refreshToken,
      expiresAt: Math.floor(Date.now()/1000) + issued.expires_in,
    };
    delete renewed.id;
    if (!(await readSession(session.id))) throw new OwnerSessionError(401, "owner login has expired");
    await sessionKv().put(key("session", session.id!), JSON.stringify(renewed),
      {expirationTtl: Math.max(60,current.validUntil-Math.floor(Date.now()/1000))});
    return renewed.accessToken;
  })();
  refreshing.set(session.id,refresh);
  try { return await refresh; } finally { refreshing.delete(session.id); }
}

export async function deleteOwnerSession(request: Request): Promise<void> {
  const id = cookie(request.headers.get("Cookie"), SESSION_COOKIE);
  if (id) await sessionKv().delete(key("session", id));
}

export function ownerCookieClear(): string {
  return clearCookie(SESSION_COOKIE);
}

export function appOrigin(): string {
  return new URL(process.env.APP_ORIGIN ?? APP_ORIGIN).origin;
}

export function csrfMatches(session: SessionRecord, request: Request): boolean {
  if (["GET", "HEAD", "OPTIONS"].includes(request.method.toUpperCase())) return true;
  return request.headers.get("Origin") === appOrigin()
    && request.headers.get("X-Toolkit-CSRF") === session.csrf;
}
