import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineProject } from "vitest/config";

export default defineProject({
  plugins: [
    cloudflareTest({
      wrangler: {
        configPath: "./wrangler.test.jsonc",
      },
    }),
  ],
  test: {
    include: ["test/**/*.test.ts"],
    exclude: ["test/google-oauth-flow.test.ts"],
    maxWorkers: 1,
  },
});
