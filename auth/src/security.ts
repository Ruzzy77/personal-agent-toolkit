export class RequestSecurityError extends Error {
  constructor(
    readonly code: "invalid_request" | "server_error",
    message: string,
    readonly status: 400 | 403 | 500 = 400,
  ) {
    super(message);
    this.name = "RequestSecurityError";
  }
}

export function randomToken(byteLength = 32): string {
  const bytes = crypto.getRandomValues(new Uint8Array(byteLength));
  return btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

export async function sha256Base64Url(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

export function readCookie(request: Request, name: string): string | null {
  const header = request.headers.get("Cookie") ?? "";
  for (const item of header.split(";")) {
    const [cookieName, ...rest] = item.trim().split("=");
    if (cookieName === name) {
      return rest.join("=") || null;
    }
  }
  return null;
}

export function secureCookie(
  name: string,
  value: string,
  maxAgeSeconds: number,
): string {
  return `${name}=${value}; HttpOnly; Secure; Path=/; SameSite=Lax; Max-Age=${maxAgeSeconds}`;
}

export function clearSecureCookie(name: string): string {
  return `${name}=; HttpOnly; Secure; Path=/; SameSite=Lax; Max-Age=0`;
}

export function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function canonicalIssuer(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new RequestSecurityError(
      "server_error",
      "AUTH_ISSUER must be a valid URL",
      500,
    );
  }

  const localHttp =
    url.protocol === "http:" &&
    (url.hostname === "localhost" || url.hostname === "127.0.0.1");
  if (
    (url.protocol !== "https:" && !localHttp) ||
    url.username !== "" ||
    url.password !== "" ||
    url.pathname !== "/" ||
    url.search !== "" ||
    url.hash !== ""
  ) {
    throw new RequestSecurityError(
      "server_error",
      "AUTH_ISSUER must be an HTTPS origin",
      500,
    );
  }
  return url.origin;
}

export function assertIssuerRequest(request: Request, issuer: string): void {
  if (new URL(request.url).origin !== issuer) {
    throw new RequestSecurityError(
      "invalid_request",
      "request origin does not match the configured issuer",
    );
  }
}

export function htmlResponse(body: string, status = 200): Response {
  return new Response(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Security-Policy":
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
      "Content-Type": "text/html; charset=utf-8",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export function jsonError(
  code: string,
  description: string,
  status: number,
): Response {
  return Response.json(
    { error: code, error_description: description },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
      },
    },
  );
}
