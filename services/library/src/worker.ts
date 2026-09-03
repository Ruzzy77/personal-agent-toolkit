import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { z } from "zod";

import {
  authorizationChallenge,
  authorizeRequest,
  protectedResourceMetadata,
} from "./authorization";
import { SitesLibraryClient, SitesLibraryError } from "./sites-client";
import type {
  AuthenticatedOwner,
  Env,
  LibraryMutationResult,
} from "./types";

const MCP_PATH = "/api/mcp";
const METADATA_PATH = "/.well-known/oauth-protected-resource";
const PATH_METADATA = `${METADATA_PATH}${MCP_PATH}`;
const ISSUE_ID = /^(daily|digest|research):\d{4}-\d{2}-\d{2}(?::(?:[01]\d|2[0-3]))?$/;
const NEW_ISSUE_ID = /^(daily|digest|research):\d{4}-\d{2}-\d{2}:(?:[01]\d|2[0-3])$/;
const SOURCE_HTML = z
  .string()
  .min(1)
  .max(2_000_000)
  .refine(
    (value) =>
      /<!doctype html>/i.test(value)
      && /<h1\b/i.test(value)
      && /<article\b/i.test(value),
    "완전한 발간호 HTML이 필요합니다.",
  )
  .describe("제목 h1, 도입문 .lead 또는 .standfirst, article을 포함한 완전한 HTML입니다. 새 발간호는 저장할 때 색인 발간호 템플릿으로 정규화되므로 별도 style과 읽기 도구를 만들지 않습니다.");

function json(value: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json; charset=utf-8");
  headers.set("Cache-Control", "private, no-store");
  return Response.json(value, { ...init, headers });
}

function maskEmail(value: string | undefined): string | null {
  if (!value?.includes("@")) return null;
  const [name, domain] = value.split("@", 2);
  return `${name.slice(0, 2)}${"*".repeat(Math.max(1, name.length - 2))}@${domain}`;
}

function toolError(error: unknown, id?: string) {
  const body = error instanceof SitesLibraryError
    ? { code: error.code, status: error.status, id }
    : { code: "library_gateway_error", id };
  return {
    isError: true,
    content: [{ type: "text" as const, text: JSON.stringify(body) }],
    structuredContent: body,
  };
}

function mutationResponse(result: LibraryMutationResult) {
  const summary = {
    status: result.status,
    id: result.issue.id,
    title: result.issue.title,
    updated_at: result.issue.updatedAt,
    canonical_path: result.issue.canonicalPath,
  };
  return {
    content: [{ type: "text" as const, text: JSON.stringify(summary) }],
    structuredContent: summary,
  };
}

function createServer(
  owner: AuthenticatedOwner,
  library: SitesLibraryClient,
): McpServer {
  const server = new McpServer(
    { name: "personal-library", version: "0.2.0" },
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
      return {
        content: [{ type: "text", text: JSON.stringify(identity) }],
        structuredContent: identity,
      };
    },
  );

  server.registerTool(
    "library_list_issues",
    {
      title: "Library 발간호 목록",
      description: "최신 Library 발간호의 제목, 날짜와 식별자를 조회합니다.",
      inputSchema: {
        collection: z.enum(["daily", "digest", "research"]).optional(),
        limit: z.number().int().min(1).max(200).default(20),
      },
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
        return {
          content: [{ type: "text", text: JSON.stringify(issues) }],
          structuredContent: { issues },
        };
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
      inputSchema: {
        id: z.string().regex(ISSUE_ID),
        format: z.enum(["text", "source_html"]).default("text"),
      },
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
        if (!issue) return toolError(new SitesLibraryError(404, "not_found"), id);
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
          updatedAt: issue.updatedAt,
          format,
          content,
        };
        return {
          content: [{ type: "text", text: content }],
          structuredContent: result,
        };
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
        inputSchema: {
          id: z.string().regex(ISSUE_ID),
          source_html: SOURCE_HTML,
          cover_path: z.string().max(500).optional(),
          references: z.array(z.string().max(1000)).max(100).optional(),
        },
        annotations: {
          readOnlyHint: false,
          destructiveHint: true,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async ({ id, source_html, cover_path, references }) => {
        try {
          return mutationResponse(
            await library.updateIssue(id, {
              source_html,
              cover_path,
              references,
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
        inputSchema: {
          id: z.string().regex(NEW_ISSUE_ID),
          published_at: z.string().max(80).optional(),
          references: z.array(z.string().max(1000)).max(100).default([]),
          source_html: SOURCE_HTML,
          cover_path: z.string().max(500).optional(),
        },
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async ({ id, published_at, references, source_html, cover_path }) => {
        try {
          return mutationResponse(await library.createIssue({
            id,
            published_at,
            references,
            source_html,
            cover_path,
          }));
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
        inputSchema: {
          path: z
            .string()
            .regex(/^[a-zA-Z0-9/_-]+\.(png|jpe?g|webp|gif|avif)$/i)
            .max(500),
          content_type: z.enum([
            "image/avif",
            "image/gif",
            "image/jpeg",
            "image/png",
            "image/webp",
          ]),
          base64: z.string().min(1).max(14_000_000),
        },
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
          return {
            content: [{ type: "text", text: JSON.stringify(result) }],
            structuredContent: result,
          };
        } catch (error) {
          return toolError(error);
        }
      },
    );
  }

  return server;
}

async function handleMcp(
  request: Request,
  owner: AuthenticatedOwner,
  env: Env,
): Promise<Response> {
  const server = createServer(owner, new SitesLibraryClient(env));
  const transport = new WebStandardStreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
    enableJsonResponse: true,
  });
  await server.connect(transport);
  const response = await transport.handleRequest(request);
  response.headers.set("Cache-Control", "private, no-store");
  return response;
}

export default {
  async fetch(request, env): Promise<Response> {
    const url = new URL(request.url);
    const metadataUrl = new URL(PATH_METADATA, env.RESOURCE_URI).href;

    if (
      request.method === "GET"
      && (url.pathname === METADATA_PATH || url.pathname === PATH_METADATA)
    ) {
      return json(protectedResourceMetadata(env.RESOURCE_URI, env.AUTH_ISSUER));
    }

    if (url.pathname !== MCP_PATH) {
      return new Response("Not found", { status: 404 });
    }

    const authorization = await authorizeRequest(
      request,
      env.AUTH_SERVICE,
      env.RESOURCE_URI,
      ["library.read"],
    );
    if (!authorization.ok) {
      const headers = new Headers({
        "Cache-Control": "private, no-store",
        "WWW-Authenticate": authorizationChallenge(metadataUrl, authorization),
      });
      return new Response(null, { status: authorization.status, headers });
    }

    return handleMcp(request, authorization.owner, env);
  },
} satisfies ExportedHandler<Env>;
