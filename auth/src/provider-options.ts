import type {
  OAuthHelpers,
  OAuthProviderOptions,
} from "@cloudflare/workers-oauth-provider";

import type { OAuthEnv } from "./types";

export const notFoundHandler = {
  fetch() {
    return new Response("Not found", { status: 404 });
  },
} satisfies ExportedHandler<OAuthEnv>;

export function createProviderOptions(
  defaultHandler: ExportedHandler<OAuthEnv>,
): OAuthProviderOptions<OAuthEnv> {
  return {
    apiRoute: "/__oauth_provider_reserved",
    apiHandler: notFoundHandler,
    defaultHandler,
    authorizeEndpoint: "/authorize",
    tokenEndpoint: "/oauth/token",
    clientRegistrationEndpoint: "/oauth/register",
    clientIdMetadataDocumentEnabled: true,
    allowPlainPKCE: false,
    allowImplicitFlow: false,
    allowTokenExchangeGrant: false,
    accessTokenTTL: 900,
    refreshTokenTTL: 2_592_000,
    resourceMatchOriginOnly: false,
  };
}

export type OAuthProviderBinding = OAuthHelpers;
