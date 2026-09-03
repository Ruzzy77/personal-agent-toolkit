import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import {
  mcpTextError,
  mcpTextResult,
  shortLivedMcpAuth,
} from "@personal-agent/remote-runtime";

import { asDesignError, DesignError } from "./errors";
import {
  createRecipeSchema,
  listRecipesOutputSchema,
  listRecipesSchema,
  mutationOutputSchema,
  readFileOutputSchema,
  readFileSchema,
  readRecipeOutputSchema,
  readRecipeSchema,
  updateRecipeSchema,
  uploadFileOutputSchema,
  uploadFileSchema,
} from "./schemas";
import { DesignService } from "./service";
import type {
  AuthenticatedOwner,
  DesignMutationResult,
  Env,
} from "./types";

function toolError(error: unknown, id?: string) {
  const normalized = asDesignError(error);
  return mcpTextError({
    code: normalized.code,
    status: normalized.status,
    details: normalized.details,
    ...(id ? { id } : {}),
  });
}

function mutationResponse(result: DesignMutationResult) {
  return mcpTextResult({
    status: result.status,
    id: result.recipe.id,
    version: result.recipe.recipeVersion,
    revision: result.recipe.revision,
    updated_at: result.recipe.updatedAt,
  });
}

function toBase64(bytes: Uint8Array): string {
  let value = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    value += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(value);
}

function createServer(
  owner: AuthenticatedOwner,
  design: DesignService,
): McpServer {
  const server = new McpServer(
    { name: "personal-design", version: "0.3.0" },
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

  server.registerTool(
    "design_list_recipes",
    {
      title: "디자인 자산 목록",
      description: "개인 디자인 라이브러리에서 형식과 상태에 맞는 레시피를 찾습니다.",
      inputSchema: listRecipesSchema,
      outputSchema: listRecipesOutputSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (input) => {
      try {
        return mcpTextResult({ recipes: await design.listRecipes(input) });
      } catch (error) {
        return toolError(error);
      }
    },
  );

  server.registerTool(
    "design_read_recipe",
    {
      title: "디자인 레시피 읽기",
      description: "선택한 개인 디자인 레시피의 원칙, 형식 규칙과 파일 목록을 읽습니다.",
      inputSchema: readRecipeSchema,
      outputSchema: readRecipeOutputSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ id }) => {
      try {
        const recipe = await design.readRecipe(id);
        if (!recipe) {
          throw new DesignError("not_found", "the Design recipe was not found", 404, { id });
        }
        return mcpTextResult(recipe);
      } catch (error) {
        return toolError(error, id);
      }
    },
  );

  server.registerTool(
    "design_read_asset",
    {
      title: "디자인 자산 읽기",
      description: "선택한 레시피의 템플릿, CSS, 문서 또는 이미지 파일 하나를 읽습니다.",
      inputSchema: readFileSchema,
      outputSchema: readFileOutputSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ id, path, encoding }) => {
      try {
        const loaded = await design.readFile(id, path);
        if (!loaded) {
          throw new DesignError("not_found", "the Design asset was not found", 404, {
            id,
            path,
          });
        }
        const bytes = new Uint8Array(await loaded.object.arrayBuffer());
        let content: string;
        if (encoding === "utf8") {
          try {
            content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
          } catch {
            throw new DesignError(
              "asset_not_text",
              "this Design asset must be read with base64 encoding",
            );
          }
        } else {
          content = toBase64(bytes);
        }
        return mcpTextResult({
          id,
          path,
          content_type: loaded.record.content_type,
          encoding,
          content,
          sha256: loaded.record.sha256,
          file_revision: loaded.record.revision,
        }, encoding === "utf8" ? content : undefined);
      } catch (error) {
        return toolError(error, id);
      }
    },
  );

  if (!owner.scopes.includes("design.write")) return;

  server.registerTool(
    "design_create_recipe",
    {
      title: "디자인 레시피 만들기",
      description: "개인 디자인 라이브러리에 새 레시피의 메타데이터를 만듭니다.",
      inputSchema: createRecipeSchema,
      outputSchema: mutationOutputSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
      },
    },
    async ({ recipe }) => {
      try {
        return mutationResponse(await design.createRecipe(recipe));
      } catch (error) {
        return toolError(error, recipe.id);
      }
    },
  );

  server.registerTool(
    "design_update_recipe",
    {
      title: "디자인 레시피 편집",
      description: "현재 revision을 확인한 뒤 개인 디자인 레시피 메타데이터를 바꿉니다.",
      inputSchema: updateRecipeSchema,
      outputSchema: mutationOutputSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ id, expected_revision, recipe }) => {
      try {
        return mutationResponse(
          await design.updateRecipe(id, expected_revision, recipe),
        );
      } catch (error) {
        return toolError(error, id);
      }
    },
  );

  server.registerTool(
    "design_upload_asset",
    {
      title: "디자인 자산 저장",
      description: "레시피의 템플릿, CSS, 문서나 이미지 파일을 개인 저장소에 추가하거나 갱신합니다.",
      inputSchema: uploadFileSchema,
      outputSchema: uploadFileOutputSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: false,
        openWorldHint: false,
      },
    },
    async (input) => {
      try {
        const result = await design.uploadFile(input);
        return mcpTextResult({
          status: result.status,
          id: result.recipeId,
          path: result.file.path,
          sha256: result.file.sha256,
          file_revision: result.file.revision,
          bytes: result.file.byteSize,
        });
      } catch (error) {
        return toolError(error, input.id);
      }
    },
  );
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
