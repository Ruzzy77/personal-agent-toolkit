import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineProject } from "vitest/config";

const migrations = await readD1Migrations("./migrations");
const libraryMigrations = await readD1Migrations("../library/migrations");
const designMigrations = await readD1Migrations("../design/migrations");

export default defineProject({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.test.jsonc" },
    }),
  ],
  test: {
    include: ["test/**/*.test.ts"],
    setupFiles: ["./test/setup.ts"],
    maxWorkers: 1,
    provide: { migrations, libraryMigrations, designMigrations },
  },
});
