import packageInfo from "../package.json";
import { handleHttp } from "./http";
import type { Env } from "./types";

const RETIRED_PATHS = new Set([
  "/mcp",
  "/.well-known/oauth-protected-resource",
  "/.well-known/oauth-protected-resource/mcp",
]);

function json(value: unknown, status = 200): Response {
  return Response.json(value, {
    status,
    headers: { "Cache-Control": "private, no-store" },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "personal-agent-design", version: packageInfo.version, mode: "legacy-recovery" });
    }
    if (RETIRED_PATHS.has(url.pathname)) {
      return json({
        error: "design_mcp_retired",
        message: "Design tools are no longer provided by Personal Agent Toolkit. Use UIKit for design skills and assets.",
        replacement: "https://personal-uikit.hiyaq77.workers.dev/mcp",
      }, 410);
    }
    // Existing authenticated records, shared files and recovery keep their own lifecycle.
    return handleHttp(request, env);
  },
} satisfies ExportedHandler<Env>;
