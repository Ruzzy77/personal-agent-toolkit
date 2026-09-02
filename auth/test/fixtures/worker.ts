import {
  AuthorizationError,
  OAuthProvider,
  type AuthRequest,
} from "@cloudflare/workers-oauth-provider";

import { AuthService } from "../../src/auth-service";
import {
  AuthorizationPolicyError,
  authorizeRequest,
} from "../../src/authorization-policy";
import { createProviderOptions } from "../../src/provider-options";
import { parseResourceRegistry } from "../../src/resource-registry";
import type { OAuthEnv } from "../../src/types";

const testAuthorizationHandler: ExportedHandler<OAuthEnv> = {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/authorize") {
      return new Response("Not found", { status: 404 });
    }

    let oauthRequest: AuthRequest;
    try {
      oauthRequest = await env.OAUTH_PROVIDER.parseAuthRequest(request);
    } catch (error) {
      if (!(error instanceof AuthorizationError)) {
        throw error;
      }
      return Response.json(
        { error: error.code, error_description: error.description },
        { status: 400 },
      );
    }

    try {
      const registry = parseResourceRegistry(env.RESOURCE_REGISTRY_JSON);
      const decision = authorizeRequest(oauthRequest, registry);
      const { redirectTo } = await env.OAUTH_PROVIDER.completeAuthorization({
        request: oauthRequest,
        userId: "google-sub-owner-123",
        metadata: {
          resourceId: decision.resource.id,
          testOnly: true,
        },
        scope: decision.grantedScopes,
        revokeExistingGrants: false,
        props: {
          provider: "google",
          subject: "google-sub-owner-123",
          email: "owner@example.test",
          emailVerified: true,
          displayName: "Owner",
        },
      });
      return Response.redirect(redirectTo, 302);
    } catch (error) {
      if (!(error instanceof AuthorizationPolicyError)) {
        throw error;
      }
      return Response.json(
        { error: error.code, error_description: error.message },
        { status: 400 },
      );
    }
  },
};

export { AuthService };

export default new OAuthProvider<OAuthEnv>(
  createProviderOptions(testAuthorizationHandler),
);
