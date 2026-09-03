import type { D1Migration } from "cloudflare:test";
import type { Env as WorkerEnv } from "../src/types";

declare module "vitest" {
  export interface ProvidedContext {
    migrations: D1Migration[];
  }
}

declare global {
  namespace Cloudflare {
    interface Env extends WorkerEnv {}
    interface GlobalProps {
      mainModule: typeof import("../src/worker");
    }
  }
}

export {};
