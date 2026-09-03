import {
  createMcpHandler,
  McpServer,
} from "@modelcontextprotocol/server";
import {
  mcpTextError,
  mcpTextResult,
  shortLivedMcpAuth,
} from "@personal-agent/remote-runtime";

import { asLibraryError, LibraryError } from "./errors";
import {
  createIssueSchema,
  listIssuesSchema,
  readIssueSchema,
  updateIssueSchema,
  uploadAssetSchema,
} from "./schemas";
import { LibraryService } from "./service";
import type {
  AuthenticatedOwner,
  Env,
  LibraryMutationResult,
} from "./types";

function maskEmail(value: string | undefined): string | null {
  if (!value?.includes("@")) return null;
  const [name, domain] = value.split("@", 2);
  return `${name.slice(0, 2)}${"*".repeat(Math.max(1, name.length - 2))}@${domain}`;
}

function toolError(error: unknown, id?: string) {
  const normalized = asLibraryError(error);
  const body = {
    code: normalized.code,
    status: normalized.status,
    details: normalized.details,
    ...(id ? { id } : {}),
  };
  return mcpTextError(body);
}

function mutationResponse(result: LibraryMutationResult) {
  const summary = {
    status: result.status,
    id: result.issue.id,
    title: result.issue.title,
    version: result.issue.version,
    updated_at: result.issue.updatedAt,
    canonical_path: result.issue.canonicalPath,
  };
  return mcpTextResult(summary);
}

function createServer(
  owner: AuthenticatedOwner,
  library: LibraryService,
): McpServer {
  const server = new McpServer(
    { name: "personal-library", version: "0.3.0" },
    {
      instructions:
        owner.scopes.includes("library.write")
          ? "소유자의 온라인 Library 원본을 읽고 편집합니다. 새 발간호의 바깥 구조와 화면 스타일은 색인 발간호 템플릿으로 통일해 저장합니다."
          : "소유자의 온라인 Library 원본을 읽습니다. 원문 HTML과 읽기용 텍스트를 제공합니다.",
    },
  );

  server.registerTool(
    "library_whoami",
    {
      title: "Library 인증 확인",
      description: "현재 OAuth 연결의 소유자 인증과 허용 권한을 확인합니다.",
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async () => {
      const identity = {
        authenticated: true,
        provider: owner.provider,
        email_hint: maskEmail(owner.email),
        scopes: owner.scopes,
        resource: owner.resource,
      };
      return mcpTextResult(identity);
    },
  );

  server.registerTool(
    "library_list_issues",
    {
      title: "Library 발간호 목록",
      description: "최신 Library 발간호의 제목, 날짜와 식별자를 조회합니다.",
      inputSchema: listIssuesSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ collection, limit }) => {
      try {
        const issues = await library.listIssues(collection ?? null, limit);
        return mcpTextResult({ issues }, JSON.stringify(issues));
      } catch (error) {
        return toolError(error);
      }
    },
  );

  server.registerTool(
    "library_read_issue",
    {
      title: "Library 발간호 읽기",
      description: "발간호 식별자로 본문을 읽습니다. 윤문에는 source_html 형식을 사용합니다.",
      inputSchema: readIssueSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ id, format }) => {
      try {
        const issue = await library.readIssue(id);
        if (!issue) {
          return toolError(
            new LibraryError("not_found", "the Library issue was not found", 404),
            id,
          );
        }
        const content = format === "source_html" ? issue.sourceHtml : issue.text;
        const result = {
          id: issue.id,
          collection: issue.collection,
          date: issue.date,
          publishedAt: issue.publishedAt,
          title: issue.title,
          canonicalPath: issue.canonicalPath,
          references: issue.references,
          coverPath: issue.coverPath,
          version: issue.version,
          updatedAt: issue.updatedAt,
          format,
          content,
        };
        return mcpTextResult(result, content);
      } catch (error) {
        return toolError(error, id);
      }
    },
  );

  if (owner.scopes.includes("library.write")) {
    server.registerTool(
      "library_update_issue",
      {
        title: "Library 발간호 편집",
        description: "발간호의 완전한 원본 HTML과 선택한 표지 경로·공개 참고자료를 온라인 정본에 바로 저장합니다. 새 템플릿으로 만든 호는 바깥 구조와 공용 본문 클래스를 유지합니다. references를 생략하면 기존 값을 유지하고 빈 배열을 보내면 모두 지웁니다.",
        inputSchema: updateIssueSchema,
        annotations: {
          readOnlyHint: false,
          destructiveHint: true,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async ({ id, source_html, expected_version, cover_path, references }) => {
        try {
          return mutationResponse(
            await library.updateIssue(id, {
              source_html,
              expected_version,
              ...(cover_path === undefined ? {} : { cover_path }),
              ...(references === undefined ? {} : { references }),
            }),
          );
        } catch (error) {
          return toolError(error, id);
        }
      },
    );

    server.registerTool(
      "library_create_issue",
      {
        title: "Library 발간호 만들기",
        description: "새 발간호를 온라인 Library 원본에 추가합니다. 식별자는 collection:YYYY-MM-DD:HH 형식이며 HH에는 예약 발행 시각을 두 자리로 넣습니다. 제목, 도입문과 article을 보내면 바깥 구조와 화면 스타일은 통일된 색인 발간호 템플릿으로 저장됩니다.",
        inputSchema: createIssueSchema,
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async ({ id, published_at, references, source_html, cover_path }) => {
        try {
          return mutationResponse(
            await library.createIssue({
              id,
              references,
              source_html,
              ...(published_at === undefined ? {} : { published_at }),
              ...(cover_path === undefined ? {} : { cover_path }),
            }),
          );
        } catch (error) {
          return toolError(error, id);
        }
      },
    );

    server.registerTool(
      "library_upload_asset",
      {
        title: "Library 이미지 올리기",
        description: "이미지 생성으로 만든 표지나 삽화를 온라인 Library 저장소에 올립니다.",
        inputSchema: uploadAssetSchema,
        annotations: {
          readOnlyHint: false,
          destructiveHint: true,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async ({ path, content_type, base64 }) => {
        try {
          const binary = atob(base64);
          const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
          const result = await library.uploadAsset(path, content_type, bytes);
          return mcpTextResult(result);
        } catch (error) {
          return toolError(error);
        }
      },
    );
  }

  return server;
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
