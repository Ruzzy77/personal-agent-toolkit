import { createRemoteJWKSet, jwtVerify } from "jose";

import {
  RequestSecurityError,
  sha256Base64Url,
} from "./security";

const GOOGLE_AUTHORIZATION_ENDPOINT =
  "https://accounts.google.com/o/oauth2/v2/auth";
const GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token";
const GOOGLE_JWKS = createRemoteJWKSet(
  new URL("https://www.googleapis.com/oauth2/v3/certs"),
);

interface GoogleTokenResponse {
  access_token?: string;
  id_token?: string;
  token_type?: string;
  expires_in?: number;
}

export interface GoogleIdentity {
  subject: string;
  email: string;
  emailVerified: true;
  displayName?: string;
}

export async function googleAuthorizationUrl(options: {
  clientId: string;
  redirectUri: string;
  state: string;
  nonce: string;
  pkceVerifier: string;
}): Promise<string> {
  const url = new URL(GOOGLE_AUTHORIZATION_ENDPOINT);
  url.searchParams.set("client_id", options.clientId);
  url.searchParams.set("redirect_uri", options.redirectUri);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", "openid email profile");
  url.searchParams.set("state", options.state);
  url.searchParams.set("nonce", options.nonce);
  url.searchParams.set(
    "code_challenge",
    await sha256Base64Url(options.pkceVerifier),
  );
  url.searchParams.set("code_challenge_method", "S256");
  return url.href;
}

export async function exchangeGoogleCode(options: {
  clientId: string;
  clientSecret: string;
  code: string;
  redirectUri: string;
  pkceVerifier: string;
}): Promise<string> {
  const response = await fetch(GOOGLE_TOKEN_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: options.clientId,
      client_secret: options.clientSecret,
      code: options.code,
      code_verifier: options.pkceVerifier,
      grant_type: "authorization_code",
      redirect_uri: options.redirectUri,
    }),
  });
  if (!response.ok) {
    throw new RequestSecurityError(
      "invalid_request",
      "Google login could not be completed",
    );
  }
  const tokens = await response.json<GoogleTokenResponse>();
  if (!tokens.id_token) {
    throw new RequestSecurityError(
      "invalid_request",
      "Google did not return an identity token",
    );
  }
  return tokens.id_token;
}

export async function verifyGoogleIdentity(options: {
  idToken: string;
  clientId: string;
  nonce: string;
}): Promise<GoogleIdentity> {
  let payload;
  try {
    ({ payload } = await jwtVerify(options.idToken, GOOGLE_JWKS, {
      algorithms: ["RS256"],
      audience: options.clientId,
      issuer: ["https://accounts.google.com", "accounts.google.com"],
    }));
  } catch {
    throw new RequestSecurityError(
      "invalid_request",
      "Google identity token is invalid",
    );
  }

  if (
    payload.nonce !== options.nonce ||
    typeof payload.sub !== "string" ||
    typeof payload.email !== "string" ||
    payload.email_verified !== true
  ) {
    throw new RequestSecurityError(
      "invalid_request",
      "Google identity claims could not be verified",
    );
  }

  return {
    subject: payload.sub,
    email: payload.email,
    emailVerified: true,
    displayName:
      typeof payload.name === "string" ? payload.name : undefined,
  };
}

function parseAllowlist(value: string | undefined): Set<string> {
  return new Set(
    (value ?? "")
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean),
  );
}

export function assertAllowedOwner(
  identity: GoogleIdentity,
  options: {
    allowedSubjects?: string;
    allowedEmails?: string;
  },
): void {
  const subjects = parseAllowlist(options.allowedSubjects);
  const emails = new Set(
    [...parseAllowlist(options.allowedEmails)].map((email) =>
      email.toLocaleLowerCase("en-US"),
    ),
  );
  if (subjects.size === 0 && emails.size === 0) {
    throw new RequestSecurityError(
      "server_error",
      "owner allowlist is not configured",
      500,
    );
  }
  if (
    !subjects.has(identity.subject) &&
    !emails.has(identity.email.toLocaleLowerCase("en-US"))
  ) {
    throw new RequestSecurityError(
      "invalid_request",
      "this Google account is not allowed",
      403,
    );
  }
}
