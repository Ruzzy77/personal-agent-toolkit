import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";

import { JournalError, asJournalError } from "./errors";
import {
  closeWeekToolSchema,
  correctionRequestSchema,
  getBoardToolSchema,
  ingestRequestSchema,
  periodToolSchema,
  promotionRequestSchema,
  setResolutionToolSchema,
} from "./schemas";
import { JournalService } from "./service";
import { currentWeekId, kstDate } from "./time";
import type { Env, Principal } from "./types";

function requireAnyScope(principal: Principal, scopes: string[]): void {
  if (!scopes.some((scope) => principal.scopes.has(scope))) {
    throw new JournalError(
      "insufficient_scope",
      "the connection does not grant this Journal operation",
      403,
    );
  }
}

function result(value: unknown) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(value) }],
    structuredContent: value as Record<string, unknown>,
  };
}

async function safeTool(operation: () => Promise<unknown>) {
  try {
    return result(await operation());
  } catch (error) {
    const journalError = asJournalError(error);
    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify({
            error: {
              code: journalError.code,
              message: journalError.message,
            },
          }),
        },
      ],
      isError: true,
    };
  }
}

function buildServer(env: Env, principal: Principal): McpServer {
  const service = new JournalService(env.DB);
  const server = new McpServer(
    { name: "Personal Agent Journal", version: "0.1.0" },
    {
      instructions:
        "Journal tracks the owner's current weekly work state and append-only history. " +
        "Read the current board before changing an item. Automated observations may update " +
        "classification and summaries but must not infer completion, hold, or cancellation. " +
        "Use source references instead of copying email or document bodies.",
    },
  );

  server.registerTool(
    "journal_get_board",
    {
      title: "Read Journal Board",
      description:
        "Read one KST week of Journal items, status counts, and concise event flow.",
      inputSchema: getBoardToolSchema,
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true },
    },
    async ({ weekId, includeResolved }) =>
      safeTool(async () => {
        requireAnyScope(principal, ["journal.read"]);
        return service.getBoard(weekId, includeResolved);
      }),
  );

  server.registerTool(
    "journal_ingest_items",
    {
      title: "Ingest Journal Observations",
      description:
        "Idempotently create or refresh concise work observations. This never changes resolution.",
      inputSchema: ingestRequestSchema,
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true },
    },
    async ({ items }) =>
      safeTool(async () => {
        requireAnyScope(principal, ["journal.ingest", "journal.write"]);
        return service.ingestItems(items, principal);
      }),
  );

  server.registerTool(
    "journal_set_resolution",
    {
      title: "Confirm Journal Item Resolution",
      description:
        "Confirm an item as active, held, completed, or canceled after the owner has decided.",
      inputSchema: setResolutionToolSchema,
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true },
    },
    async ({ itemId, ...input }) =>
      safeTool(async () => {
        requireAnyScope(principal, ["journal.write"]);
        return service.setResolution(itemId, input, principal);
      }),
  );

  server.registerTool(
    "journal_close_week",
    {
      title: "Close Journal Week",
      description:
        "Freeze the current state of a KST week and return only explicit Corpus reflection candidates.",
      inputSchema: closeWeekToolSchema,
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true },
    },
    async ({ weekId, idempotencyKey, occurredAt }) =>
      safeTool(async () => {
        requireAnyScope(principal, ["journal.close"]);
        return service.closeWeek(
          weekId,
          idempotencyKey,
          occurredAt,
          principal,
        );
      }),
  );

  server.registerTool(
    "journal_add_correction",
    {
      title: "Add Closed-week Correction",
      description:
        "Append a correction note to a closed week without rewriting its frozen items.",
      inputSchema: correctionRequestSchema.extend({
        weekId: closeWeekToolSchema.shape.weekId.unwrap(),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true },
    },
    async ({ weekId, ...input }) =>
      safeTool(async () => {
        requireAnyScope(principal, ["journal.write"]);
        return service.addCorrection(
          weekId ?? currentWeekId(),
          input,
          principal,
        );
      }),
  );

  server.registerTool(
    "journal_get_period",
    {
      title: "Read Journal Period",
      description:
        "Read daily, weekly, monthly, quarterly, or yearly Journal totals and project rollups.",
      inputSchema: periodToolSchema,
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true },
    },
    async ({ kind, anchor }) =>
      safeTool(async () => {
        requireAnyScope(principal, ["journal.read"]);
        return service.getPeriod(kind, anchor ?? kstDate());
      }),
  );

  server.registerTool(
    "journal_record_corpus_promotion",
    {
      title: "Record Corpus Reflection",
      description:
        "Record that a durable Journal outcome was applied to an existing project source and refreshed in Corpus.",
      inputSchema: promotionRequestSchema,
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true },
    },
    async (input) =>
      safeTool(async () => {
        requireAnyScope(principal, ["journal.ingest", "journal.write"]);
        return service.recordPromotion(input, principal);
      }),
  );

  return server;
}

export async function handleMcp(
  request: Request,
  env: Env,
  principal: Principal,
): Promise<Response> {
  const handler = createMcpHandler(() => buildServer(env, principal));
  return handler.fetch(request, {
    authInfo: {
      token: principal.id,
      clientId: principal.auth,
      scopes: [...principal.scopes],
      expiresAt: Math.floor(Date.now() / 1000) + 300,
    },
  });
}
