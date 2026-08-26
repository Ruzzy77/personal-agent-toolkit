import {
  AuthorizationError,
  type AuthRequest,
} from "@cloudflare/workers-oauth-provider";

import {
  AuthorizationPolicyError,
  authorizeRequest,
} from "./authorization-policy";
import {
  consumeGoogleFlow,
  createGoogleFlow,
} from "./flow-state";
import {
  assertAllowedOwner,
  exchangeGoogleCode,
  googleAuthorizationUrl,
  verifyGoogleIdentity,
} from "./google-identity";
import { parseResourceRegistry } from "./resource-registry";
import {
  RequestSecurityError,
  assertIssuerRequest,
  canonicalIssuer,
  jsonError,
} from "./security";
import type { OAuthEnv } from "./types";

function redirectResponse(location: string, cookies: string[] = []): Response {
  const headers = new Headers({
    "Cache-Control": "no-store",
    Location: location,
    "Referrer-Policy": "no-referrer",
  });
  for (const cookie of cookies) {
    headers.append("Set-Cookie", cookie);
  }
  return new Response(null, { status: 302, headers });
}

function authorizationErrorRedirect(
  oauthRequest: Pick<AuthRequest, "redirectUri" | "state">,
  issuer: string,
  code: string,
  description: string,
): Response {
  const redirect = new URL(oauthRequest.redirectUri);
  redirect.searchParams.set("error", code);
  redirect.searchParams.set("error_description", description);
  redirect.searchParams.set("state", oauthRequest.state);
  redirect.searchParams.set("iss", issuer);
  return redirectResponse(redirect.href);
}

function providerAuthorizationError(error: AuthorizationError): Response {
  if (!error.redirectUri) {
    return jsonError(error.code, error.description, 400);
  }
  const redirect = new URL(error.redirectUri);
  redirect.searchParams.set("error", error.code);
  redirect.searchParams.set("error_description", error.description);
  if (error.state) {
    redirect.searchParams.set("state", error.state);
  }
  if (error.issuer) {
    redirect.searchParams.set("iss", error.issuer);
  }
  return redirectResponse(redirect.href);
}

async function handleAuthorizeGet(
  request: Request,
  env: OAuthEnv,
  issuer: string,
): Promise<Response> {
  let oauthRequest: AuthRequest;
  try {
    oauthRequest = await env.OAUTH_PROVIDER.parseAuthRequest(request);
  } catch (error) {
    if (error instanceof AuthorizationError) {
      return providerAuthorizationError(error);
    }
    throw error;
  }

  const registry = parseResourceRegistry(env.RESOURCE_REGISTRY_JSON);
  let decision;
  try {
    decision = authorizeRequest(oauthRequest, registry);
  } catch (error) {
    if (error instanceof AuthorizationPolicyError) {
      return authorizationErrorRedirect(
        oauthRequest,
        issuer,
        error.code,
        error.message,
      );
    }
    throw error;
  }

  const googleFlow = await createGoogleFlow(
    env.OAUTH_KV,
    oauthRequest,
    decision.resource.id,
  );
  const redirectUri = new URL("/oauth/google/callback", issuer).href;
  const googleUrl = await googleAuthorizationUrl({
    clientId: env.GOOGLE_CLIENT_ID,
    redirectUri,
    state: googleFlow.state,
    nonce: googleFlow.nonce,
    pkceVerifier: googleFlow.pkceVerifier,
  });
  return redirectResponse(googleUrl, [googleFlow.setCookie]);
}

async function handleGoogleCallback(
  request: Request,
  env: OAuthEnv,
  issuer: string,
): Promise<Response> {
  const url = new URL(request.url);
  const state = url.searchParams.get("state");
  if (!state) {
    throw new RequestSecurityError(
      "invalid_request",
      "Google login state is missing",
    );
  }
  const flow = await consumeGoogleFlow(env.OAUTH_KV, request, state);

  const upstreamError = url.searchParams.get("error");
  if (upstreamError) {
    const response = authorizationErrorRedirect(
      flow.oauthRequest,
      issuer,
      "access_denied",
      "Google login was not completed",
    );
    response.headers.append("Set-Cookie", flow.clearCookie);
    return response;
  }

  const code = url.searchParams.get("code");
  if (!code) {
    throw new RequestSecurityError(
      "invalid_request",
      "Google authorization code is missing",
    );
  }
  const redirectUri = new URL("/oauth/google/callback", issuer).href;
  const idToken = await exchangeGoogleCode({
    clientId: env.GOOGLE_CLIENT_ID,
    clientSecret: env.GOOGLE_CLIENT_SECRET,
    code,
    redirectUri,
    pkceVerifier: flow.pkceVerifier,
  });
  const identity = await verifyGoogleIdentity({
    idToken,
    clientId: env.GOOGLE_CLIENT_ID,
    nonce: flow.nonce,
  });
  assertAllowedOwner(identity, {
    allowedSubjects: env.OWNER_GOOGLE_SUBS,
    allowedEmails: env.OWNER_GOOGLE_EMAILS,
  });

  const registry = parseResourceRegistry(env.RESOURCE_REGISTRY_JSON);
  const policy = authorizeRequest(flow.oauthRequest, registry);
  if (policy.resource.id !== flow.resourceId) {
    throw new RequestSecurityError(
      "invalid_request",
      "authorization resource changed",
    );
  }

  const { redirectTo } = await env.OAUTH_PROVIDER.completeAuthorization({
    request: flow.oauthRequest,
    userId: `google-${encodeURIComponent(identity.subject)}`,
    metadata: {
      provider: "google",
      resourceId: policy.resource.id,
    },
    scope: policy.grantedScopes,
    props: {
      provider: "google",
      subject: identity.subject,
      email: identity.email,
      emailVerified: identity.emailVerified,
      displayName: identity.displayName,
    },
  });
  return redirectResponse(redirectTo, [flow.clearCookie]);
}

export const authorizationHandler: ExportedHandler<OAuthEnv> = {
  async fetch(request, env) {
    try {
      const issuer = canonicalIssuer(env.AUTH_ISSUER);
      assertIssuerRequest(request, issuer);
      const url = new URL(request.url);

      if (url.pathname === "/authorize" && request.method === "GET") {
        return await handleAuthorizeGet(request, env, issuer);
      }
      if (
        url.pathname === "/oauth/google/callback" &&
        request.method === "GET"
      ) {
        return await handleGoogleCallback(request, env, issuer);
      }
      if (url.pathname === "/" && request.method === "GET") {
        return Response.json(
          { service: "Personal Agent Auth", status: "ready" },
          { headers: { "Cache-Control": "no-store" } },
        );
      }
      return new Response("Not found", { status: 404 });
    } catch (error) {
      if (error instanceof RequestSecurityError) {
        console.warn(
          "Personal Agent Auth request rejected",
          error.code,
          error.message,
        );
        return jsonError(error.code, error.message, error.status);
      }
      console.error(
        "Personal Agent Auth request failed",
        error instanceof Error ? error.name : "UnknownError",
      );
      return jsonError("server_error", "request could not be completed", 500);
    }
  },
};
