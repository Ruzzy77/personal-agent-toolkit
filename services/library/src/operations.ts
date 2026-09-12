import { z } from "zod/v4";
import {
  defineOperation,
  executeOperation,
  operationCapabilities,
  type OperationActor,
  type OperationDefinition,
} from "@personal-agent/remote-runtime";
import { libraryManagementOperations } from "./management";
import { LibraryError } from "./errors";
import * as schemas from "./schemas";
import type { LibraryService } from "./service";
import type {
  AuthenticatedOwner,
  LibraryIssue,
  LibraryMutationResult,
} from "./types";

type WireResult = { value: Record<string, unknown>; text?: string };
export type LibraryOperation = OperationDefinition<z.ZodObject> & {
  mcpOutput: z.ZodObject;
  mcpResult?: (value: Record<string, unknown>, input: unknown) => WireResult;
  destructiveHint?: boolean;
  idempotentHint?: boolean;
};
const output = z.looseObject({
  status: z.string().optional(),
  issue: z.record(z.string(), z.unknown()).optional(),
});
export function libraryOperations(
  service: LibraryService,
  actor: OperationActor,
  owner?: AuthenticatedOwner,
): Record<string, LibraryOperation> {
  const op = (
    schema: z.ZodObject,
    mcpOutput: z.ZodObject,
    effects: "read" | "revise",
    description: string,
    run: (raw: unknown) => Promise<Record<string, unknown>>,
    options: Partial<
      Pick<LibraryOperation, "mcpResult" | "destructiveHint" | "idempotentHint">
    > = {},
  ): LibraryOperation => ({
    ...defineOperation({
      schema,
      outputSchema: output,
      scope: effects === "read" ? "library.read" : "library.write",
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
    const result = raw as LibraryMutationResult;
    return {
      value: {
        status: result.status,
        id: result.issue.id,
        title: result.issue.title,
        version: result.issue.version,
        updated_at: result.issue.updatedAt,
        canonical_path: result.issue.canonicalPath,
      },
    };
  };
  const operations: Record<string, LibraryOperation> = Object.fromEntries(
    Object.entries(
      libraryManagementOperations(service.management(), actor),
    ).map(([name, definition]) => [
      name,
      { ...definition, mcpOutput: definition.outputSchema as z.ZodObject },
    ]),
  );
  Object.assign(operations, {
    library_whoami: op(
      z.object({}),
      schemas.whoamiOutputSchema,
      "read",
      "현재 소유자 인증과 허용 권한을 확인합니다.",
      async () => {
        const email = owner?.email,
          separator = email?.indexOf("@") ?? -1;
        return {
          authenticated: true,
          provider: owner?.provider ?? "google",
          email_hint:
            email && separator > 0
              ? `${email.slice(0, 2)}${"*".repeat(Math.max(1, separator - 2))}${email.slice(separator)}`
              : null,
          scopes: [...actor.scopes],
          resource: owner?.resource ?? "",
        };
      },
    ),
    library_list_issues: op(
      schemas.listIssuesSchema,
      schemas.listIssuesOutputSchema,
      "read",
      "발간호의 제목, 날짜와 식별자를 조회합니다. 기본 목록은 현재 자료만 반환합니다.",
      async (raw) => {
        const input = schemas.listIssuesSchema.parse(raw);
        return {
          issues: await service.listIssues(
            input.collection ?? null,
            input.limit,
            input.lifecycle,
            input.offset,
          ),
        };
      },
      { mcpResult: (value) => ({ value, text: JSON.stringify(value.issues) }) },
    ),
    library_read_issue: op(
      schemas.readIssueSchema,
      schemas.readIssueOutputSchema,
      "read",
      "발간호 본문을 읽습니다. 윤문에는 source_html 형식을 사용합니다.",
      async (raw) => {
        const { id } = schemas.readIssueSchema.parse(raw),
          issue = await service.readIssue(id);
        if (!issue)
          throw new LibraryError(
            "not_found",
            "the Library issue was not found",
            404,
            { id },
          );
        return { issue };
      },
      {
        mcpResult: (value, raw) => {
          const issue = value.issue as LibraryIssue,
            { format } = schemas.readIssueSchema.parse(raw),
            { text: _text, sourceHtml: _html, ...metadata } = issue;
          const content =
            format === "source_html" ? issue.sourceHtml : issue.text;
          return { value: { ...metadata, format, content }, text: content };
        },
      },
    ),
    library_update_issue: op(
      schemas.updateIssueSchema,
      schemas.mutationOutputSchema,
      "revise",
      "완전한 원본 HTML을 현재 버전과 대조해 저장합니다. 생략한 표지와 참고자료는 유지합니다.",
      async (raw) => {
        const { id, ...input } = schemas.updateIssueSchema.parse(raw);
        return service.updateIssue(id, input);
      },
      { mcpResult: summary, destructiveHint: true },
    ),
    library_create_issue: op(
      schemas.createIssueSchema,
      schemas.mutationOutputSchema,
      "revise",
      "새 발간호를 collection:YYYY-MM-DD:HH 식별자로 저장합니다. 통일된 발간호 템플릿을 사용합니다.",
      async (raw) => service.createIssue(schemas.createIssueSchema.parse(raw)),
      { mcpResult: summary },
    ),
    library_upload_asset: op(
      schemas.uploadAssetSchema,
      schemas.uploadAssetOutputSchema,
      "revise",
      "표지나 삽화를 새 경로에 저장합니다. 같은 경로·바이트·MIME 재시도만 재사용하며 덮어쓰지 않습니다.",
      async (raw) => {
        const input = schemas.uploadAssetSchema.parse(raw),
          bytes = Uint8Array.from(atob(input.base64), (char) =>
            char.charCodeAt(0),
          );
        return {
          ...(await service.uploadAsset(input.path, input.content_type, bytes)),
        };
      },
      { idempotentHint: true },
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
    library_import_issue: httpOp(
      schemas.importIssueSchema,
      "revise",
      async (raw) => service.importIssue(schemas.importIssueSchema.parse(raw)),
    ),
    library_issue_by_path: httpOp(
      z.object({ path: z.string().min(1).max(1000) }),
      "read",
      async (raw) => {
        const { path } = z.object({ path: z.string() }).parse(raw),
          issue = await service.readIssueByPath(path);
        if (!issue)
          throw new LibraryError(
            "not_found",
            "the Library issue was not found",
            404,
          );
        return { issue };
      },
    ),
    library_edit_fragments: httpOp(
      schemas.updateIssueFragmentsSchema.extend({ id: schemas.issueIdSchema }),
      "revise",
      async (raw) => {
        const { id, ...input } = schemas.updateIssueFragmentsSchema
          .extend({ id: schemas.issueIdSchema })
          .parse(raw);
        return service.updateIssueFragments(id, input);
      },
    ),
    library_media_read: httpOp(
      z.object({ path: z.string().min(1).max(500) }),
      "read",
      async (raw) => {
        const { path } = z.object({ path: z.string() }).parse(raw);
        return { object: await service.readAsset(path) };
      },
    ),
  });
  operations.library_capabilities!.run = async () => ({
    operations: operationCapabilities(actor, operations),
  });
  return operations;
}
export async function executeLibraryOperation(
  service: LibraryService,
  actor: OperationActor,
  name: string,
  input: unknown,
  owner?: AuthenticatedOwner,
) {
  const operation = libraryOperations(service, actor, owner)[name];
  if (!operation)
    throw new LibraryError(
      "operation_not_found",
      "Library operation was not found",
      404,
    );
  return executeOperation(actor, operation, input);
}
