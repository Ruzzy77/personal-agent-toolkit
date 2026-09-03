import {
  hostHeaderValidationResponse,
} from "@modelcontextprotocol/server";

import {
  authorizationChallenge,
  authorizeRequest,
  LIBRARY_SCOPES,
  protectedResourceMetadata,
} from "./authorization";
import { asLibraryError } from "./errors";
import { handleHttp } from "./http";
import { handleMcp } from "./mcp";
import type { Env } from "./types";

const MCP_PATH = "/api/mcp";
const METADATA_PATH = "/.well-known/oauth-protected-resource";
const PATH_METADATA = `${METADATA_PATH}${MCP_PATH}`;

function json(value: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json; charset=utf-8");
  headers.set("Cache-Control", "private, no-store");
  return Response.json(value, { ...init, headers });
}

function mcpError(error: unknown, env: Env): Response {
  const normalized = asLibraryError(error);
  const metadataUrl = new URL(PATH_METADATA, env.RESOURCE_URI).href;
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
        "WWW-Authenticate": authorizationChallenge(metadataUrl),
      },
    },
  );
}

export default {
  async fetch(request, env): Promise<Response> {
    const url = new URL(request.url);
    const metadataUrl = new URL(PATH_METADATA, env.RESOURCE_URI).href;

    if (request.method === "GET" && url.pathname === "/health") {
      return json({
        ok: true,
        service: "personal-agent-library",
        version: "0.2.1",
      });
    }

    if (
      request.method === "GET"
      && (url.pathname === METADATA_PATH || url.pathname === PATH_METADATA)
    ) {
      return json(protectedResourceMetadata(env.RESOURCE_URI, env.AUTH_ISSUER));
    }

    if (url.pathname !== MCP_PATH) return handleHttp(request, env);

    try {
      const rejected = hostHeaderValidationResponse(request, [
        new URL(env.RESOURCE_URI).hostname,
        "localhost",
        "127.0.0.1",
      ]);
      if (rejected) return rejected;
      if (request.headers.get("Origin")) {
        return json(
          {
            jsonrpc: "2.0",
            error: {
              code: -32001,
              message: "browser-origin MCP requests are not allowed",
            },
            id: null,
          },
          { status: 403 },
        );
      }

      const authorization = await authorizeRequest(
        request,
        env.AUTH_SERVICE,
        env.RESOURCE_URI,
        LIBRARY_SCOPES,
      );
      if (!authorization.ok) {
        const headers = new Headers({
          "Cache-Control": "private, no-store",
          "WWW-Authenticate": authorizationChallenge(metadataUrl, authorization),
        });
        return new Response(null, { status: authorization.status, headers });
      }

      return handleMcp(request, authorization.owner, env);
    } catch (error) {
      return mcpError(error, env);
    }
  },
} satisfies ExportedHandler<Env>;
