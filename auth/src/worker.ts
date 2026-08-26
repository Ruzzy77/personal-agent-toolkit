import { OAuthProvider } from "@cloudflare/workers-oauth-provider";

import { AuthService } from "./auth-service";
import { authorizationHandler } from "./authorization-handler";
import { createProviderOptions } from "./provider-options";
import type { OAuthEnv } from "./types";

export { AuthService };

export default new OAuthProvider<OAuthEnv>(
  createProviderOptions(authorizationHandler),
);
