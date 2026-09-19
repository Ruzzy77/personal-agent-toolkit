import manifest from "../manifest.full.json";

// Remote counterpart of probe_server.py: the same frozen definitions over
// streamable HTTP so each client can be measured on the transport it will use.
// Every request is logged as one JSON line for `wrangler tail`.

type Mode = "BASE" | "FULL" | "PAGED";
const PAGE = 20;
const PROBE = {
  name: "surface_probe",
  title: "Surface probe",
  description: "Return a fresh receipt string. Used to mark a measurement trial.",
  inputSchema: { type: "object", properties: {}, additionalProperties: false },
  outputSchema: { type: "object", properties: {}, additionalProperties: true },
};

function tools(mode: Mode): unknown[] {
  return mode === "BASE" ? [PROBE] : [...manifest.tools, PROBE];
}

function log(entry: Record<string, unknown>) {
  console.log(JSON.stringify({ probe: true, at: new Date().toISOString(), ...entry }));
}

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const match = /^\/([a-z0-9-]+)\/(base|full|paged)\/mcp$/.exec(url.pathname);
    if (!match) return new Response("not found", { status: 404 });
    const [, secret, modeName] = match;
    if (secret !== "s7k2q9") return new Response("not found", { status: 404 });
    const mode = modeName.toUpperCase() as Mode;
    if (request.method === "GET") {
      // No server-initiated stream is needed for this experiment.
      return new Response(null, { status: 405, headers: { allow: "POST" } });
    }
    if (request.method === "DELETE") return new Response(null, { status: 204 });
    const body = (await request.json()) as {
      id?: unknown;
      method?: string;
      params?: Record<string, unknown>;
    };
    const { id, method, params = {} } = body;
    const connection = request.headers.get("mcp-session-id") ?? "stateless";
    // Clients that ask for a stream must receive SSE framing, like the real service.
    const wantsStream = (request.headers.get("accept") ?? "").includes(
      "text/event-stream",
    );
    const session = request.headers.get("mcp-session-id") ?? crypto.randomUUID();
    const envelope = (payload: unknown) => {
      const text = JSON.stringify(payload);
      if (!wantsStream) {
        return new Response(text, {
          headers: {
            "content-type": "application/json",
            "mcp-session-id": session,
          },
        });
      }
      return new Response(`event: message\ndata: ${text}\n\n`, {
        headers: {
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
          "mcp-session-id": session,
        },
      });
    };
    const reply = (result: unknown) => envelope({ jsonrpc: "2.0", id, result });

    if (method === "initialize") {
      log({
        mode,
        connection,
        event: "initialize",
        protocol_version: params.protocolVersion,
        client_info: params.clientInfo,
        client_capabilities: params.capabilities,
        user_agent: request.headers.get("user-agent"),
      });
      return reply({
        protocolVersion: params.protocolVersion ?? "2025-06-18",
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: `surface-${modeName}`, version: "1" },
      });
    }
    if (method === "tools/list") {
      const everything = tools(mode);
      const cursor = typeof params.cursor === "string" ? Number(params.cursor) : 0;
      const listed =
        mode === "PAGED" ? everything.slice(cursor, cursor + PAGE) : everything;
      const following =
        mode === "PAGED" && cursor + PAGE < everything.length
          ? String(cursor + PAGE)
          : undefined;
      log({
        mode,
        connection,
        event: "tools/list",
        cursor: params.cursor ?? null,
        returned: listed.length,
        bytes: JSON.stringify(listed).length,
        next_cursor: following ?? null,
      });
      return reply(following ? { tools: listed, nextCursor: following } : { tools: listed });
    }
    if (method === "tools/call") {
      const name = params.name as string;
      const receipt = crypto.randomUUID().slice(0, 10);
      log({ mode, connection, event: "tools/call", name, is_error: name !== "surface_probe" });
      if (name !== "surface_probe") {
        return reply({
          content: [{ type: "text", text: "probe_only: this surface carries definitions only" }],
          isError: true,
        });
      }
      return reply({
        content: [{ type: "text", text: receipt }],
        structuredContent: { receipt },
        isError: false,
      });
    }
    if (typeof method === "string" && method.startsWith("notifications/")) {
      log({ mode, connection, event: "notification", method });
      return new Response(null, { status: 202, headers: { "mcp-session-id": session } });
    }
    log({ mode, connection, event: "unsupported", method });
    return envelope({
      jsonrpc: "2.0",
      id,
      error: { code: -32601, message: `unsupported method ${method}` },
    });
  },
};
