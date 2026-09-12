import packageInfo from "../package.json";
import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import {
  executeOperation,
  operationAnnotations,
  mcpTextError,
  mcpTextResult,
  shortLivedMcpAuth,
} from "@personal-agent/remote-runtime";
import { libraryOperations } from "./operations";
import { asLibraryError } from "./errors";
import { LibraryService } from "./service";
import type { AuthenticatedOwner, Env } from "./types";
function createServer(
  owner: AuthenticatedOwner,
  library: LibraryService,
): McpServer {
  const server = new McpServer(
    { name: "personal-library", version: packageInfo.version },
    {
      instructions: owner.scopes.includes("library.write")
        ? "소유자의 온라인 Library 원본을 읽고 편집합니다. 새 발간호의 바깥 구조와 화면 스타일은 색인 발간호 템플릿으로 통일해 저장합니다."
        : "소유자의 온라인 Library 원본을 읽습니다. 원문 HTML과 읽기용 텍스트를 제공합니다.",
    },
  );
  registerLibraryTools(server, owner, library);
  return server;
}

export function registerLibraryTools(
  server: McpServer,
  owner: AuthenticatedOwner,
  library: LibraryService,
): void {
  const actor = {
    ownerId: owner.userId,
    clientId: owner.clientId,
    kind: "owner" as const,
    scopes: new Set(owner.scopes),
  };
  for (const [name, operation] of Object.entries(
    libraryOperations(library, actor, owner),
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
          const normalized = asLibraryError(error);
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
    createServer(owner, new LibraryService(env)),
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
