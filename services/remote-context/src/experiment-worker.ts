import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import { mcpTextError } from "@personal-agent/remote-runtime";
import { registerDesignTools } from "personal-agent-design-service/mcp";
import { DesignService } from "personal-agent-design-service/service";
import { registerJournalTools } from "personal-agent-journal-service/mcp";
import { JournalService } from "personal-agent-journal-service/service";
import { registerLibraryTools } from "personal-agent-library-service/mcp";
import { LibraryService } from "personal-agent-library-service/service";
import { z } from "zod";

import { callHost, HOST_TOOLS } from "./host";
import { registerCorpusTools, registerHypesTools, registerSenseTools } from "./mcp";
import { registerToolkitProductTools } from "./toolkit-products";
import type { Env, Principal } from "./types";

/**
 * Surface experiment only. A serves the frozen production surface with Corpus,
 * Hypes, Host and the product switches wired to real handlers; every other
 * product keeps its definition and answers `experiment_out_of_scope`. B serves
 * the same capability through 13 tools: Host, the switches, and a discovery and
 * dispatch trio. Neither surface touches production data.
 */

const SECRET = "s7k2q9";
const OWNER = "owner_surface_experiment";
const SCOPES = [
  "sense.read", "sense.write", "corpus.read", "corpus.write", "corpus.sync",
  "hypes.read", "hypes.write", "journal.read", "journal.write", "journal.close",
  "library.read", "library.write", "design.read", "design.write",
  "host.read", "host.write",
];

function principal(): Principal {
  return {
    ownerId: OWNER,
    scopes: new Set(SCOPES),
    clientId: "surface-experiment",
    auth: "oauth",
    owner: {
      userId: OWNER,
      provider: "google",
      subject: "surface-experiment",
      resource: "https://surface-probe.hiyaq77.workers.dev/a/mcp",
      clientId: "surface-experiment",
      expiresAt: Math.floor(Date.now() / 1000) + 3600,
      scopes: SCOPES,
    },
  } as unknown as Principal;
}

function outOfScope(name: string) {
  return mcpTextError({
    ok: false as const,
    error: {
      code: "experiment_out_of_scope",
      message: `${name} is not wired in the surface experiment`,
    },
  });
}

/** Keeps a product's published definitions while replacing its behaviour. */
function definitionsOnly(server: McpServer): McpServer {
  return new Proxy(server, {
    get(target, property, receiver) {
      if (property === "registerTool") {
        return (name: string, config: unknown) =>
          (target as unknown as {
            registerTool: (
              name: string,
              config: unknown,
              handler: () => Promise<unknown>,
            ) => unknown;
          }).registerTool(name, config, async () => outOfScope(name));
      }
      return Reflect.get(target, property, receiver);
    },
  }) as McpServer;
}

type Entry = {
  name: string;
  config: { title?: string; description?: string; inputSchema?: unknown; outputSchema?: unknown; annotations?: unknown };
  handler: (input: unknown) => Promise<unknown>;
};

/** Runs the same registrations against a recorder so both surfaces share them. */
function registry(env: Env): Entry[] {
  const entries: Entry[] = [];
  const recorder = {
    registerTool(name: string, config: Entry["config"], handler: Entry["handler"]) {
      entries.push({ name, config, handler });
      return { enabled: true, enable() {}, disable() {}, update() {} };
    },
  } as unknown as McpServer;
  populate(recorder, env);
  return entries;
}

function populate(server: McpServer, env: Env): void {
  const who = principal();
  const stub = definitionsOnly(server);
  registerToolkitProductTools(server, env, who, new Set());
  registerSenseTools(stub, env, who);
  registerCorpusTools(server, env, who);
  registerHypesTools(server, env, who);
  registerJournalTools(stub, new JournalService(env.JOURNAL_DB), {
    kind: "owner",
    id: OWNER,
    scopes: who.scopes,
    auth: "oauth",
  } as never);
  registerLibraryTools(
    stub,
    who.owner as never,
    new LibraryService({ DB: env.LIBRARY_DB, MEDIA: env.LIBRARY_MEDIA } as never),
  );
  registerDesignTools(
    stub,
    { ownerId: OWNER, scopes: SCOPES } as never,
    new DesignService({ DB: env.DESIGN_DB, ASSETS: env.DESIGN_ASSETS } as never),
  );
  registerHostToolsForExperiment(server, env);
}

function surfaceA(env: Env): McpServer {
  const server = new McpServer(
    { name: "Surface A", version: "experiment" },
    { instructions: "Experiment surface A: the current toolkit tool list." },
  );
  for (const entry of registry(env)) {
    server.registerTool(entry.name, entry.config as never, entry.handler as never);
  }
  return server;
}

const readable = (name: string) =>
  /^(host_(capabilities|roots|search|read|job)|toolkit_products)$/.test(name) ||
  /_(read|list|get|search|find|overview|status|preview|whoami|capabilities|resolve|history|board|period)/.test(
    name,
  );

function surfaceB(env: Env): McpServer {
  const entries = registry(env);
  const byName = new Map(entries.map((entry) => [entry.name, entry]));
  const server = new McpServer(
    { name: "Surface B", version: "experiment" },
    {
      instructions:
        "Experiment surface B: Host tools are direct. Every other toolkit capability is " +
        "found with toolkit_discover and run with toolkit_call_read or toolkit_call_write " +
        "using the original tool name and input.",
    },
  );
  for (const entry of entries) {
    if (entry.name.startsWith("host_") || entry.name.startsWith("toolkit_products")) {
      server.registerTool(entry.name, entry.config as never, entry.handler as never);
    }
  }
  const products = [...new Set(entries.map((entry) => entry.name.split("_")[0]))];
  server.registerTool(
    "toolkit_discover",
    {
      title: "Discover toolkit tools",
      description:
        "Find the toolkit tools that are not exposed directly. Give product for that product's " +
        "tool names, descriptions and read/write kind, or give names for at most three tools " +
        "with their original input schema. Give exactly one of the two.",
      inputSchema: z
        .object({
          product: z.enum(products as [string, ...string[]]).optional(),
          names: z.array(z.string().min(1).max(120)).min(1).max(3).optional(),
        })
        .strict(),
      outputSchema: z.looseObject({}),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async (input) => {
      const { product, names } = input as { product?: string; names?: string[] };
      if ((product && names) || (!product && !names)) {
        return mcpTextError({
          ok: false as const,
          error: { code: "invalid_request", message: "give exactly one of product or names" },
        });
      }
      if (product) {
        const listed = entries
          .filter((entry) => entry.name.startsWith(`${product}_`))
          .map((entry) => ({
            name: entry.name,
            description: entry.config.description,
            kind: readable(entry.name) ? "read" : "write",
          }));
        return { content: [], structuredContent: { ok: true as const, result: { product, tools: listed } } };
      }
      const detailed = (names ?? []).map((name) => {
        const entry = byName.get(name);
        if (!entry) return { name, error: "unknown tool" };
        return {
          name,
          description: entry.config.description,
          kind: readable(name) ? "read" : "write",
          inputSchema: z.toJSONSchema(entry.config.inputSchema as never, { io: "input" }),
        };
      });
      return { content: [], structuredContent: { ok: true as const, result: { tools: detailed } } };
    },
  );
  const dispatch = (kind: "read" | "write") => async (input: unknown) => {
    const { name, arguments: args } = input as { name: string; arguments?: Record<string, unknown> };
    const entry = byName.get(name);
    if (!entry || name.startsWith("toolkit_call_")) {
      return mcpTextError({
        ok: false as const,
        error: { code: "unknown_tool", message: `${name} is not a toolkit tool` },
      });
    }
    if (kind === "read" && !readable(name)) {
      return mcpTextError({
        ok: false as const,
        error: { code: "write_tool", message: `${name} changes data; use toolkit_call_write` },
      });
    }
    const parsed = (entry.config.inputSchema as { parse?: (value: unknown) => unknown })?.parse;
    let checked: unknown = args ?? {};
    if (typeof parsed === "function") {
      try {
        checked = parsed.call(entry.config.inputSchema, args ?? {});
      } catch (error) {
        return mcpTextError({
          ok: false as const,
          error: { code: "invalid_arguments", message: String((error as Error).message).slice(0, 400) },
        });
      }
    }
    return entry.handler(checked);
  };
  for (const [name, kind, title] of [
    ["toolkit_call_read", "read", "Run a read tool"],
    ["toolkit_call_write", "write", "Run a write tool"],
  ] as const) {
    server.registerTool(
      name,
      {
        title,
        description:
          kind === "read"
            ? "Run one toolkit read tool by its original name and input, as returned by toolkit_discover."
            : "Run one toolkit tool that changes data, by its original name and input, as returned by toolkit_discover.",
        inputSchema: z
          .object({ name: z.string().min(1).max(120), arguments: z.looseObject({}).optional() })
          .strict(),
        outputSchema: z.looseObject({}),
        annotations:
          kind === "read"
            ? { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false }
            : { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: false },
      },
      dispatch(kind) as never,
    );
  }
  return server;
}

function registerHostToolsForExperiment(server: McpServer, env: Env): void {
  for (const tool of HOST_TOOLS) {
    server.registerTool(
      tool.name,
      {
        title: tool.title,
        description: tool.description,
        inputSchema: tool.schema,
        outputSchema: z.looseObject({}),
        annotations: tool.annotations,
      },
      async (input) => callHost(env, tool.name, input as Record<string, unknown>),
    );
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const match = /^\/([a-z0-9]+)\/(a|b)\/mcp$/.exec(url.pathname);
    if (!match || match[1] !== SECRET) return new Response("not found", { status: 404 });
    const handler = createMcpHandler(() =>
      match[2] === "a" ? surfaceA(env) : surfaceB(env),
    );
    return handler.fetch(request, {});
  },
};

