import { env } from "cloudflare:test";

import { it } from "vitest";

import { handleMcp } from "../src/mcp";
import { MCP_SURFACES } from "../src/surfaces";
import type { Env, Principal } from "../src/types";

const runtime = env as unknown as Env;
const scopes = [
  "corpus.read",
  "corpus.write",
  "corpus.sync",
  "sense.read",
  "sense.write",
  "hypes.read",
  "hypes.write",
  "journal.read",
  "journal.write",
  "journal.close",
  "library.read",
  "library.write",
  "design.read",
  "design.write",
  "host.read",
  "host.write",
];
const principal: Principal = {
  ownerId: "owner_manifest",
  scopes: new Set(scopes),
  clientId: "manifest",
  auth: "oauth",
  owner: {
    userId: "owner_manifest",
    provider: "google",
    subject: "manifest",
    scopes,
  },
} as Principal;

// Freezes the unfiltered unified tool definitions for the surface experiment.
it("dumps the full toolkit manifest", async () => {
  const response = await handleMcp(
    new Request("https://context.test/mcp", {
      method: "POST",
      headers: {
        Accept: "application/json, text/event-stream",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
    }),
    runtime,
    principal,
    "toolkit",
  );
  const text = await response.text();
  const line = text
    .split("\n")
    .map((part) => part.trim())
    .find((part) => part.startsWith("data: "));
  const payload = JSON.parse(line ? line.slice(6) : text);
  const manifest = JSON.stringify({
    surface: MCP_SURFACES.toolkit,
    tools: payload.result.tools,
  });
  console.log(`MANIFEST_START${manifest}MANIFEST_END`);
});
