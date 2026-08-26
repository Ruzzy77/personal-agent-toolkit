import { getOAuthApi } from "@cloudflare/workers-oauth-provider";
import { WorkerEntrypoint } from "cloudflare:workers";

import { createProviderOptions, notFoundHandler } from "./provider-options";
import { parseResourceRegistry } from "./resource-registry";
import { validateTokenSummary } from "./token-policy";
import type {
  AccessValidationResult,
  OAuthEnv,
  OwnerProps,
} from "./types";

export class AuthService extends WorkerEntrypoint<OAuthEnv> {
  async validateAccessToken(
    token: string,
    resource: string,
    requiredScopes: string[],
  ): Promise<AccessValidationResult> {
    const registry = parseResourceRegistry(this.env.RESOURCE_REGISTRY_JSON);
    const oauth = getOAuthApi(
      createProviderOptions(notFoundHandler),
      this.env,
    );
    const summary = await oauth.unwrapToken<OwnerProps>(token);
    return validateTokenSummary(
      summary,
      resource,
      requiredScopes,
      registry,
    );
  }
}
