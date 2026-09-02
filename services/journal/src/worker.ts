import { hostHeaderValidationResponse } from "@modelcontextprotocol/server";

import { authenticate } from "./auth";
import { JournalError, asJournalError } from "./errors";
import { handleHttp } from "./http";
import { handleMcp } from "./mcp";
import type { Env } from "./types";

function mcpError(error: unknown, env: Env): Response {
  const journalError = asJournalError(error);
  const resource = new URL(env.JOURNAL_RESOURCE);
  return Response.json(
    {
      jsonrpc: "2.0",
      error: { code: -32001, message: journalError.message },
      id: null,
    },
    {
      status: journalError.status,
      headers: {
        "Cache-Control": "no-store",
        "WWW-Authenticate": `Bearer resource_metadata="${resource.origin}/.well-known/oauth-protected-resource"`,
      },
    },
  );
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname !== "/mcp") return handleHttp(request, env);

    try {
      const resourceHost = new URL(env.JOURNAL_RESOURCE).hostname;
      const rejected = hostHeaderValidationResponse(request, [
        resourceHost,
        "localhost",
        "127.0.0.1",
      ]);
      if (rejected) return rejected;
      if (request.headers.get("Origin")) {
        throw new JournalError(
          "origin_not_allowed",
          "browser-origin MCP requests are not allowed",
          403,
        );
      }
      const principal = await authenticate(request, env, ["journal.read"]);
      return handleMcp(request, env, principal);
    } catch (error) {
      return mcpError(error, env);
    }
  },
} satisfies ExportedHandler<Env>;
