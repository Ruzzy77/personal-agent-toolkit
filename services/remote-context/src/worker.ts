import { hostHeaderValidationResponse } from "@modelcontextprotocol/server";

import { authenticateMcp, supportedScopes } from "./auth";
import { CorpusShard } from "./corpus-shard";
import { asContextError, ContextError } from "./errors";
import { handleHttp } from "./http";
import { handleMcp } from "./mcp";
import { SyncBroker } from "./sync-broker";
import type { Env, ResourceKind } from "./types";

export { CorpusShard, SyncBroker };

function mcpKind(path: string): ResourceKind | null {
  if (path === "/mcp") return "toolkit";
  if (path === "/sense/mcp") return "sense";
  if (path === "/corpus/mcp") return "corpus";
  if (path === "/hypes/mcp") return "hypes";
  return null;
}

function mcpError(error: unknown, env: Env, kind: ResourceKind): Response {
  const normalized = asContextError(error);
  const resource = new URL(
    kind === "toolkit"
      ? env.TOOLKIT_RESOURCE
      : kind === "sense"
      ? env.SENSE_RESOURCE
      : kind === "corpus"
        ? env.CORPUS_RESOURCE
        : env.HYPES_RESOURCE,
  );
  return Response.json(
    {
      jsonrpc: "2.0",
      error: { code: -32001, message: normalized.message },
      id: null,
    },
    {
      status: normalized.status,
      headers: {
        "Cache-Control": "no-store",
        "WWW-Authenticate":
          kind === "toolkit"
            ? `Bearer resource_metadata="${resource.origin}/.well-known/oauth-protected-resource/mcp"`
            : `Bearer resource_metadata="${resource.origin}/.well-known/oauth-protected-resource/${kind}/mcp"`,
      },
    },
  );
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const kind = mcpKind(url.pathname);
    if (!kind) return handleHttp(request, env);
    try {
      const resourceHost = new URL(
        kind === "toolkit"
          ? env.TOOLKIT_RESOURCE
          : kind === "sense"
          ? env.SENSE_RESOURCE
          : kind === "corpus"
            ? env.CORPUS_RESOURCE
            : env.HYPES_RESOURCE,
      ).hostname;
      const rejected = hostHeaderValidationResponse(request, [
        resourceHost,
        "localhost",
        "127.0.0.1",
      ]);
      if (rejected) return rejected;
      if (request.headers.get("Origin")) {
        throw new ContextError(
          "origin_not_allowed",
          "browser-origin MCP requests are not allowed",
          403,
        );
      }
      const principal = await authenticateMcp(
        request,
        env,
        kind,
        supportedScopes(kind),
      );
      return handleMcp(request, env, principal, kind);
    } catch (error) {
      return mcpError(error, env, kind);
    }
  },
} satisfies ExportedHandler<Env>;
