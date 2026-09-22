import type { McpServer } from "@modelcontextprotocol/server";
import { mcpTextError } from "@personal-agent/remote-runtime";
import { z } from "zod";

import type { Env, Principal } from "./types";

/**
 * Host adapter: the `host_*` tools are declared here and every call is
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

const EXECUTE = { ...WRITE, openWorldHint: true } as const;

const hostToolOutputSchema = z.looseObject({});
const rootId = z.string().min(1).max(256);
const relativePath = z.string().min(1).max(4096);

const jobId = z.string().min(1).max(256);

const readFile = z
  .object({
    path: relativePath,
    start_line: z.number().int().min(1).optional(),
    end_line: z.number().int().min(1).optional(),
  })
  .strict();
const replacement = z
  .object({
    start_marker: z.string().min(1).max(4096),
    end_marker: z.string().min(1).max(4096),
    content: z.string(),
  })
  .strict();

type HostScope = "host.read" | "host.write";

interface HostTool {
  name: string;
  title: string;
  description: string;
  scope: HostScope;
  annotations: typeof READ_ONLY | typeof WRITE | typeof EXECUTE;
  schema: z.ZodObject<z.ZodRawShape>;
}

export const HOST_TOOLS: readonly HostTool[] = [
  {
    name: "host_capabilities",
    title: "Host capabilities",
    description: "Return limits, root execution policies, and installed tools available on the configured Spark host.",
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
    name: "host_search",
    title: "Search files",
    description:
      "Search file contents in a host root with a regular expression (ripgrep). paths are root-relative globs. Returns matching lines with context; truncated=true means a limit was hit.",
    scope: "host.read",
    annotations: READ_ONLY,
    schema: z
      .object({
        root: rootId,
        pattern: z.string().min(1).max(4096),
        paths: z.array(z.string().min(1).max(4096)).max(32).optional(),
        max_results: z.number().int().min(1).max(500).optional(),
        context: z.number().int().min(0).max(5).optional(),
        ignore_vcs: z.boolean().optional(),
      })
      .strict(),
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
  {
    name: "host_write",
    title: "Write a file",
    description:
      'Create or replace a file in a host root (content), replace the text between two unique markers (replace), or delete a file (delete=true). A normal write needs only root, path and content. expected_version (from host_read, or "absent" for new files) rejects the write when the file changed.',
    scope: "host.write",
    annotations: WRITE,
    schema: z
      .object({
        root: rootId,
        path: relativePath,
        content: z.string().max(2_097_152).optional(),
        replace: replacement.optional(),
        delete: z.boolean().optional(),
        expected_version: z.string().max(256).optional(),
      })
      .strict(),
  },
  {
    name: "host_files", title: "Workspace files",
    description: "List folders, stat, mkdir, move/rename, trash, trash_list, or restore within one root. Move/trash require expected_version; restore never overwrites.",
    scope: "host.read", annotations: WRITE,
    schema: z.object({
      root: rootId,
      operation: z.enum(["list", "stat", "mkdir", "move", "trash", "trash_list", "restore"]),
      path: relativePath.optional(), destination: relativePath.optional(),
      expected_version: z.string().max(256).optional(),
      trash_id: z.string().max(128).optional(),
      limit: z.number().int().min(1).max(200).optional(),
      cursor: z.string().max(4096).optional(),
    }).strict(),
  },
  {
    name: "host_transfer", title: "Start a file transfer",
    description: "Prepare an authenticated upload/download without binary MCP output. Upload needs size and expected_version (absent for new files). Transfer credentials go in HTTP headers, never URLs/logs. Max 1 GiB; chunks max 8 MiB.",
    scope: "host.read", annotations: WRITE,
    schema: z.object({
      root: rootId, path: relativePath, direction: z.enum(["upload", "download"]),
      size: z.number().int().min(0).max(1073741824).optional(),
      expected_version: z.string().max(256).optional(),
      sha256: z.string().regex(/^[0-9a-fA-F]{64}$/).optional(),
    }).strict(),
  },
  {
    name: "host_exec",
    title: "Run a command",
    description:
      "Run argv or shell directly as the configured Spark owner. cwd is relative to the selected root and resolves to its actual host path; the owner's HOME and installed software are available. Retired profile and https_hosts inputs are rejected. Poll host_job when still running.",
    scope: "host.write",
    annotations: EXECUTE,
    schema: z
      .object({
        root: rootId,
        cwd: z.string().max(4096).optional(),
        argv: z.array(z.string()).min(1).max(256).optional(),
        shell: z.string().min(1).max(65_536).optional(),
        stdin: z.string().max(1_048_576).optional(),
        keep_stdin_open: z.boolean().optional(),
        timeout_s: z.number().int().min(1).max(21_600).optional(),
        wait_s: z.number().int().min(0).max(50).optional(),
        // Accepted only so the Host can return a clear retired-input error.
        profile: z.string().min(1).max(64).optional(),
        https_hosts: z.array(z.string().min(1).max(253)).min(1).max(256).optional(),
      })
      .strict(),
  },
  {
    name: "host_job",
    title: "Job status and output",
    description:
      "Return a host job's status and a slice of its stored stdout or stderr. offset is a byte position; use next_offset to continue; limit=0 returns status only. eof is true once the job finished and the output is fully read.",
    scope: "host.read",
    annotations: READ_ONLY,
    schema: z
      .object({
        job_id: jobId,
        stream: z.enum(["stdout", "stderr"]).optional(),
        offset: z.number().int().min(0).optional(),
        limit: z.number().int().min(0).max(262_144).optional(),
      })
      .strict(),
  },
  {
    name: "host_job_input",
    title: "Write job input",
    description:
      "Write UTF-8 input to a running job whose host_exec request set keep_stdin_open=true. Set eof=true to close standard input.",
    scope: "host.write",
    annotations: WRITE,
    schema: z
      .object({
        job_id: jobId,
        stdin: z.string().max(1_048_576),
        eof: z.boolean().optional(),
      })
      .strict(),
  },
  {
    name: "host_job_cancel",
    title: "Cancel a job",
    description:
      "Cancel a queued or running host job. Finished jobs keep their status.",
    scope: "host.write",
    annotations: WRITE,
    schema: z.object({ job_id: jobId }).strict(),
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

function utf8Base64(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  return btoa(binary);
}

export function hostForwardArguments(name: string, args: Record<string, unknown>) {
  if (!["host_exec", "host_job_input"].includes(name) || typeof args.stdin !== "string") return args;
  const forwarded: Record<string, unknown> = {
    ...args, stdin_base64: utf8Base64(args.stdin),
  };
  delete forwarded.stdin;
  return forwarded;
}

export function hostFailureDetails(result: { content?: unknown[] }) {
  const blocks = Array.isArray(result.content)
    ? result.content as Array<{ text?: string }>
    : [];
  const message =
    blocks.find((block) => typeof block.text === "string")?.text
    ?? "File operation failed";
  // A connector may prefix the Host's `code: message` with the tool name.
  const wrapped =
    /\bhost_[a-z0-9_]+:\s*([a-z][a-z0-9_]+):/.exec(message)?.[1];
  const direct = /^\s*([a-z][a-z0-9_]+):/.exec(message)?.[1];
  const code = wrapped ?? direct ?? "host_error";
  const status =
    code === "version_conflict" ? 409
      : code === "policy_denied" ? 403
        : code === "not_found" ? 404
          : 400;
  return { code, message, status };
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
      arguments: hostForwardArguments(name, args),
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
        const required = hostRequiredScope(tool.name, input as Record<string, unknown>);
        if (!granted.has(required)) {
          return failure(
            "insufficient_scope",
            `the token does not grant the required ${required} scope`,
          );
        }
        const result = await callHost(env, tool.name, input as Record<string, unknown>);
        return tool.name === "host_transfer" ? transferReceipt(result, env) : result;
      },
    );
  }
}

export function hostRequiredScope(name: string, input: Record<string, unknown>): HostScope {
  if (name === "host_files")
    return ["list", "stat", "trash_list"].includes(String(input.operation)) ? "host.read" : "host.write";
  if (name === "host_transfer") return input.direction === "download" ? "host.read" : "host.write";
  return HOST_TOOLS.find(tool => tool.name === name)?.scope ?? "host.write";
}

export function transferReceipt(result: Awaited<ReturnType<typeof callHost>>, env: Env) {
  const data = result.structuredContent as Record<string, unknown> | undefined;
  if (result.isError || typeof data?.transfer_id !== "string") return result;
  const origin = new URL(env.TOOLKIT_RESOURCE).origin;
  const receipt = {
    ...data, transfer_url: `${origin}/host/v1/transfers/${encodeURIComponent(data.transfer_id)}`,
    token_header: "X-Toolkit-Transfer-Token",
    actions: { upload: "PUT /chunk (Upload-Offset header)", download: "GET /content", status: "GET /status", commit: "POST /commit", cancel: "DELETE /status" },
  };
  return { ...result, structuredContent: receipt, content: [{ type: "text" as const, text: JSON.stringify(receipt) }] };
}
