import { applyD1Migrations, env } from "cloudflare:test";
import { beforeAll, inject } from "vitest";

import type { D1Migration } from "cloudflare:test";
import type { Env } from "../src/types";

beforeAll(async () => {
  await applyD1Migrations(
    (env as unknown as Env).DB,
    inject("migrations") as D1Migration[],
  );
});
