import type { McpServer } from "@modelcontextprotocol/server";
import { mcpTextError } from "@personal-agent/remote-runtime";
import { z } from "zod";

import type { Env, Principal } from "./types";

/**
 * Host adapter: the eight `host_*` tools are declared here and every call is
 * forwarded to the owner's Host server over the Workers VPC binding as an
 * internal, sessionless MCP `tools/call`. Authorization ends in this Worker;
 * the Host only checks the upstream bearer.
 */

const HOST_PROTOCOL_VERSION = "2026-07-28";
const HOST_URL = "http://spark-host/mcp";
const WORKER_CLIENT = { name: "remote-context", version: "host-adapter/1" };

const READ_ONLY = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: false,
} as const;
const WRITE = {
  readOnlyHint: false,
  destructiveHint: true,
  idempotentHint: false,
  openWorldHint: false,
} as const;

const hostToolOutputSchema = z.looseObject({});
const rootId = z.string().min(1).max(256);
const relativePath = z.string().min(1).max(4096);

const readFile = z
  .object({
    path: relativePath,
    start_line: z.number().int().min(1).optional(),
    end_line: z.number().int().min(1).optional(),
  })
  .strict();

type HostScope = "host.read" | "host.write";

interface HostTool {
  name: string;
  title: string;
  description: string;
  scope: HostScope;
  annotations: typeof READ_ONLY | typeof WRITE;
  schema: z.ZodObject<z.ZodRawShape>;
}

export const HOST_TOOLS: readonly HostTool[] = [
  {
    name: "host_capabilities",
    title: "Host capabilities",
    description: "Return the Host version and the shared size and time limits.",
    scope: "host.read",
    annotations: READ_ONLY,
    schema: z.object({}).strict(),
  },
  {
    name: "host_roots",
    title: "List roots",
    description:
      "List the registered workspace roots on the host with their write permission, execution policy, and the Source roots each may read.",
    scope: "host.read",
    annotations: READ_ONLY,
    schema: z.object({}).strict(),
  },
  {
    name: "host_read",
    title: "Read files",
    description:
      "Read UTF-8 text files from a host root, optionally by line range. Returns each file's content and version; max_bytes caps the total returned content.",
    scope: "host.read",
    annotations: READ_ONLY,
    schema: z
      .object({
        root: rootId,
        files: z.array(readFile).min(1).max(32),
        max_bytes: z.number().int().min(1).max(2_097_152).optional(),
      })
      .strict(),
  },
];

const LEGACY_READ_SCOPES = [
  "sense.read",
  "corpus.read",
  "hypes.read",
  "journal.read",
  "library.read",
] as const;
const LEGACY_WRITE_SCOPES = [
  "sense.write",
  "corpus.write",
  "hypes.write",
  "journal.write",
  "library.write",
] as const;

/** Grants that predate Host inherit its scopes, like the Design bundle did. */
export function hostScopes(principal: Principal): Set<HostScope> {
  const granted = new Set<HostScope>();
  if (principal.scopes.has("host.read")) granted.add("host.read");
  if (principal.scopes.has("host.write")) granted.add("host.write");
  if (LEGACY_READ_SCOPES.every((scope) => principal.scopes.has(scope)))
    granted.add("host.read");
  if (LEGACY_WRITE_SCOPES.every((scope) => principal.scopes.has(scope)))
    granted.add("host.write");
  return granted;
}

function failure(code: string, message: string) {
  return mcpTextError({ ok: false as const, error: { code, message } });
}

interface HostCallResult {
  content?: unknown[];
  structuredContent?: unknown;
  isError?: boolean;
}

export async function callHost(
  env: Env,
  name: string,
  args: Record<string, unknown>,
) {
  if (!env.HOST_VPC || !env.HOST_UPSTREAM_TOKEN) {
    return failure("host_unavailable", "the Host connection is not configured");
  }
  const body = {
    jsonrpc: "2.0",
    id: crypto.randomUUID(),
    method: "tools/call",
    params: {
      name,
      arguments: args,
      _meta: {
        "io.modelcontextprotocol/protocolVersion": HOST_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": WORKER_CLIENT,
        "io.modelcontextprotocol/clientCapabilities": {},
      },
    },
  };
  let response: Response;
  try {
    response = await env.HOST_VPC.fetch(HOST_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.HOST_UPSTREAM_TOKEN}`,
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
        "MCP-Protocol-Version": HOST_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": name,
      },
      body: JSON.stringify(body),
    });
  } catch {
    return failure("host_unreachable", "the Host did not answer");
  }
  if (!response.ok) {
    return failure("host_error", `the Host answered HTTP ${response.status}`);
  }
  let payload: { result?: HostCallResult; error?: { message?: string } };
  try {
    payload = (await response.json()) as typeof payload;
  } catch {
    return failure("host_error", "the Host answered with an invalid body");
  }
  if (payload.error || !payload.result) {
    return failure(
      "host_rpc_error",
      payload.error?.message ?? "the Host returned no result",
    );
  }
  const result = payload.result;
  return {
    content: Array.isArray(result.content) ? result.content : [],
    structuredContent: result.structuredContent,
    isError: result.isError === true,
  } as ReturnType<typeof failure>;
}

export function registerHostTools(
  server: McpServer,
  env: Env,
  principal: Principal,
): void {
  const granted = hostScopes(principal);
  for (const tool of HOST_TOOLS) {
    server.registerTool(
      tool.name,
      {
        title: tool.title,
        description: tool.description,
        inputSchema: tool.schema,
        outputSchema: hostToolOutputSchema,
        annotations: tool.annotations,
      },
      async (input) => {
        if (!granted.has(tool.scope)) {
          return failure(
            "insufficient_scope",
            `the token does not grant the required ${tool.scope} scope`,
          );
        }
        return callHost(env, tool.name, input as Record<string, unknown>);
      },
    );
  }
}
