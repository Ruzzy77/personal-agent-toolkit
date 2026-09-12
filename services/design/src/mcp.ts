import packageInfo from "../package.json";
import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import {
  executeOperation,
  operationAnnotations,
  mcpTextError,
  mcpTextResult,
  shortLivedMcpAuth,
} from "@personal-agent/remote-runtime";
import { designOperations } from "./operations";
import { asDesignError } from "./errors";
import { DesignService } from "./service";
import type { AuthenticatedOwner, Env } from "./types";
function createServer(
  owner: AuthenticatedOwner,
  design: DesignService,
): McpServer {
  const server = new McpServer(
    { name: "personal-design", version: packageInfo.version },
    {
      instructions:
        "Design keeps the owner's private design recipes, templates, examples, and reusable assets. Read only the assets needed for the current design task and preserve the target project's own brand and system.",
    },
  );
  registerDesignTools(server, owner, design);
  return server;
}

export function registerDesignTools(
  server: McpServer,
  owner: AuthenticatedOwner,
  design: DesignService,
): void {
  if (!owner.scopes.includes("design.read")) return;
  const actor = {
    ownerId: owner.userId,
    clientId: owner.clientId,
    kind: "owner" as const,
    scopes: new Set(owner.scopes),
  };
  for (const [name, operation] of Object.entries(
    designOperations(design, actor),
  )) {
    if (
      !actor.scopes.has(operation.scope) ||
      !operation.surfaces.includes("mcp")
    )
      continue;
    server.registerTool(
      name,
      {
        description: operation.description,
        inputSchema: operation.schema,
        outputSchema: operation.mcpOutput,
        annotations: {
          ...operationAnnotations(operation),
          openWorldHint: false,
          ...(operation.destructiveHint === undefined
            ? {}
            : { destructiveHint: operation.destructiveHint }),
          ...(operation.idempotentHint === undefined
            ? {}
            : { idempotentHint: operation.idempotentHint }),
        },
      },
      async (input) => {
        try {
          const result = await executeOperation(actor, operation, input),
            wire = operation.mcpResult?.(result, input) ?? { value: result };
          return mcpTextResult(wire.value, wire.text);
        } catch (error) {
          const normalized = asDesignError(error);
          return mcpTextError({
            code: normalized.code,
            status: normalized.status,
            details: normalized.details,
            ...("id" in input ? { id: input.id } : {}),
          });
        }
      },
    );
  }
}

export async function handleMcp(
  request: Request,
  owner: AuthenticatedOwner,
  env: Env,
): Promise<Response> {
  const handler = createMcpHandler(() =>
    createServer(owner, new DesignService(env)),
  );
  const response = await handler.fetch(request, {
    authInfo: shortLivedMcpAuth({
      token: owner.userId,
      clientId: owner.clientId,
      scopes: owner.scopes,
    }),
  });
  response.headers.set("Cache-Control", "private, no-store");
  return response;
}
