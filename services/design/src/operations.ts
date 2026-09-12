import { z } from "zod/v4";
import {
  defineOperation,
  executeOperation,
  operationCapabilities,
  type OperationActor,
  type OperationDefinition,
} from "@personal-agent/remote-runtime";
import { designManagementOperations } from "./management";
import { DesignError } from "./errors";
import * as schemas from "./schemas";
import type { DesignService } from "./service";
import type { DesignMutationResult, DesignFileResult } from "./types";

type WireResult = { value: Record<string, unknown>; text?: string };
export type DesignOperation = OperationDefinition<z.ZodObject> & {
  mcpOutput: z.ZodObject;
  mcpResult?: (value: Record<string, unknown>, input: unknown) => WireResult;
  destructiveHint?: boolean;
  idempotentHint?: boolean;
};
const output = z.looseObject({
  status: z.string().optional(),
  recipe: z.record(z.string(), z.unknown()).optional(),
});
export function designOperations(
  service: DesignService,
  actor: OperationActor,
): Record<string, DesignOperation> {
  const op = (
    schema: z.ZodObject,
    mcpOutput: z.ZodObject,
    effects: "read" | "revise",
    description: string,
    run: (raw: unknown) => Promise<Record<string, unknown>>,
    options: Partial<
      Pick<DesignOperation, "mcpResult" | "destructiveHint" | "idempotentHint">
    > = {},
  ): DesignOperation => ({
    ...defineOperation({
      schema,
      outputSchema: output,
      scope: effects === "read" ? "design.read" : "design.write",
      effects,
      retry: effects === "read" ? "read" : "version",
      surfaces: ["mcp", "http"],
      description,
      run,
    }),
    mcpOutput,
    ...options,
  });
  const summary = (raw: Record<string, unknown>): WireResult => {
    const result = raw as unknown as DesignMutationResult;
    return {
      value: {
        status: result.status,
        id: result.recipe.id,
        version: result.recipe.recipeVersion,
        revision: result.recipe.revision,
        updated_at: result.recipe.updatedAt,
      },
    };
  };
  const operations: Record<string, DesignOperation> = Object.fromEntries(
    Object.entries(designManagementOperations(service.management(), actor)).map(
      ([name, definition]) => [
        name,
        { ...definition, mcpOutput: definition.outputSchema as z.ZodObject },
      ],
    ),
  );
  Object.assign(operations, {
    design_list_recipes: op(
      schemas.listRecipesSchema,
      schemas.listRecipesOutputSchema,
      "read",
      "형식과 검증 상태에 맞는 디자인 레시피를 찾습니다. 기본 목록은 현재 자료만 반환합니다.",
      async (raw) => ({
        recipes: await service.listRecipes(
          schemas.listRecipesSchema.parse(raw),
        ),
      }),
    ),
    design_read_recipe: op(
      schemas.readRecipeSchema,
      schemas.readRecipeOutputSchema,
      "read",
      "레시피의 원칙, 형식 규칙과 현재 파일 목록을 읽습니다.",
      async (raw) => {
        const { id } = schemas.readRecipeSchema.parse(raw),
          recipe = await service.readRecipe(id);
        if (!recipe)
          throw new DesignError(
            "not_found",
            "the Design recipe was not found",
            404,
            { id },
          );
        return { ...recipe };
      },
    ),
    design_read_asset: op(
      schemas.readFileSchema,
      schemas.readFileOutputSchema,
      "read",
      "레시피의 자산 본문과 정확한 파일 revision을 읽습니다.",
      async (raw) => {
        const { id, path, encoding } = schemas.readFileSchema.parse(raw),
          loaded = await service.readFile(id, path);
        if (!loaded)
          throw new DesignError(
            "not_found",
            "the Design asset was not found",
            404,
            { id, path },
          );
        const bytes = new Uint8Array(await loaded.object.arrayBuffer());
        let content = "";
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
          for (let offset = 0; offset < bytes.length; offset += 0x8000)
            content += String.fromCharCode(
              ...bytes.subarray(offset, offset + 0x8000),
            );
          content = btoa(content);
        }
        return {
          id,
          path,
          encoding,
          content,
          content_type: loaded.record.content_type,
          sha256: loaded.record.sha256,
          file_revision: loaded.record.revision,
        };
      },
      {
        mcpResult: (value) => ({
          value,
          ...(value.encoding === "utf8"
            ? { text: value.content as string }
            : {}),
        }),
      },
    ),
    design_create_recipe: op(
      schemas.createRecipeSchema,
      schemas.mutationOutputSchema,
      "revise",
      "새 레시피의 메타데이터를 만듭니다.",
      async (raw) => ({
        ...(await service.createRecipe(
          schemas.createRecipeSchema.parse(raw).recipe,
        )),
      }),
      { mcpResult: summary },
    ),
    design_update_recipe: op(
      schemas.updateRecipeSchema,
      schemas.mutationOutputSchema,
      "revise",
      "현재 revision을 확인한 뒤 레시피 메타데이터를 바꿉니다.",
      async (raw) => {
        const input = schemas.updateRecipeSchema.parse(raw);
        return {
          ...(await service.updateRecipe(
            input.id,
            input.expected_revision,
            input.recipe,
          )),
        };
      },
      { mcpResult: summary, destructiveHint: true, idempotentHint: true },
    ),
    design_upload_asset: op(
      schemas.uploadFileSchema,
      schemas.uploadFileOutputSchema,
      "revise",
      "파일 revision을 대조해 자산을 저장합니다. 불변 객체를 재사용하며 다른 바이트나 MIME은 새 객체에 저장합니다.",
      async (raw) => ({
        ...(await service.uploadFile(schemas.uploadFileSchema.parse(raw))),
      }),
      {
        destructiveHint: true,
        mcpResult: (raw) => {
          const result = raw as unknown as DesignFileResult;
          return {
            value: {
              status: result.status,
              id: result.recipeId,
              path: result.file.path,
              sha256: result.file.sha256,
              file_revision: result.file.revision,
              bytes: result.file.byteSize,
            },
          };
        },
      },
    ),
  });
  const httpOp = (
    schema: z.ZodObject,
    effects: "read" | "revise",
    run: (raw: unknown) => Promise<Record<string, unknown>>,
  ) => ({
    ...op(schema, output, effects, "Owner Site compatibility operation", run),
    surfaces: ["http"] as const,
  });
  Object.assign(operations, {
    design_catalog: httpOp(z.object({}), "read", async () => ({
      ...(await service.catalog()),
    })),
    design_import_recipe: httpOp(
      schemas.importRecipeSchema,
      "revise",
      async (raw) => ({
        ...(await service.importRecipe(schemas.importRecipeSchema.parse(raw))),
      }),
    ),
    design_file_download: httpOp(
      schemas.readFileSchema.pick({ id: true, path: true }),
      "read",
      async (raw) => {
        const { id, path } = schemas.readFileSchema.parse(raw),
          loaded = await service.readFile(id, path);
        if (!loaded)
          throw new DesignError(
            "not_found",
            "the Design asset was not found",
            404,
          );
        return { loaded };
      },
    ),
  });
  operations.design_capabilities!.run = async () => ({
    operations: operationCapabilities(actor, operations),
  });
  return operations;
}
export async function executeDesignOperation(
  service: DesignService,
  actor: OperationActor,
  name: string,
  input: unknown,
) {
  const operation = designOperations(service, actor)[name];
  if (!operation)
    throw new DesignError(
      "operation_not_found",
      "Design operation was not found",
      404,
    );
  return executeOperation(actor, operation, input);
}
