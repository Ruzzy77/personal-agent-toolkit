import type { D1Migration } from "cloudflare:test";

declare module "vitest" {
  export interface ProvidedContext {
    migrations: D1Migration[];
  }
}

declare namespace Cloudflare {
  interface GlobalProps {
    mainModule: typeof import("../src/worker");
  }
}
