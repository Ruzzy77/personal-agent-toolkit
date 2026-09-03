import { SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";

const ORIGIN = "https://journal.example.test";
const SITE_AUTH = { Authorization: "Bearer test-site-token" };
const INGEST_AUTH = { Authorization: "Bearer test-ingest-token" };

async function json(response: Response): Promise<Record<string, unknown>> {
  expect(response.headers.get("content-type")).toContain("application/json");
  return response.json<Record<string, unknown>>();
}

async function mcpJson(response: Response): Promise<Record<string, unknown>> {
  const text = await response.text();
  if (response.headers.get("content-type")?.includes("application/json")) {
    return JSON.parse(text) as Record<string, unknown>;
  }
  const dataLine = text
    .split("\n")
    .find((line) => line.startsWith("data: "));
  expect(dataLine).toBeTypeOf("string");
  return JSON.parse((dataLine ?? "data: {}").slice(6)) as Record<
    string,
    unknown
  >;
}

async function prepareWeek(weekId: string): Promise<Record<string, unknown>> {
  const response = await SELF.fetch(
    `${ORIGIN}/api/v1/weeks/${weekId}:prepare-close`,
    { method: "POST", headers: SITE_AUTH },
  );
  expect(response.status, await response.clone().text()).toBe(200);
  const body = await json(response);
  return body.result as Record<string, unknown>;
}

async function confirmWeek(
  weekId: string,
  preparationVersion: string,
  idempotencyKey: string,
  occurredAt: string,
): Promise<Response> {
  return SELF.fetch(`${ORIGIN}/api/v1/weeks/${weekId}:confirm-close`, {
    method: "POST",
    headers: { ...SITE_AUTH, "Content-Type": "application/json" },
    body: JSON.stringify({
      preparationVersion,
      idempotencyKey,
      occurredAt,
    }),
  });
}

describe("Journal API and MCP spike", () => {
  it("allows only the configured browser origin and MCP host", async () => {
    const allowed = await SELF.fetch(`${ORIGIN}/api/v1/board`, {
      method: "OPTIONS",
      headers: { Origin: "https://journal-site.example.test" },
    });
    expect(allowed.status).toBe(204);
    expect(allowed.headers.get("access-control-allow-origin")).toBe(
      "https://journal-site.example.test",
    );

    const rejectedOrigin = await SELF.fetch(`${ORIGIN}/health`, {
      headers: { Origin: "https://untrusted.example" },
    });
    expect(rejectedOrigin.status).toBe(403);

    const rejectedHost = await SELF.fetch(`${ORIGIN}/mcp`, {
      method: "POST",
      headers: {
        ...SITE_AUTH,
        Host: "untrusted.example",
        Accept: "application/json, text/event-stream",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
    });
    expect(rejectedHost.status).toBe(403);
  });

  it("keeps one stable item across ingest, API, MCP, and owner resolution", async () => {
    const initial = {
      items: [
        {
          idempotencyKey: "test:gmail:agreement:1",
          sourceKind: "gmail",
          sourceKey: "agreement-thread",
          sourceRef: "gmail:message-1",
          sourceVersion: "message-1",
          weekId: "2026-08-31",
          projectKey: "industrial-ai",
          title: "협약변경 공문",
          summary: "연구지원팀 회신 대기",
          lane: "waiting",
          dueAt: null,
          durableOutcome: null,
          corpusTargetSpace: null,
          occurredAt: "2026-09-02T00:00:00.000Z"
        },
      ],
    };
    const createdResponse = await SELF.fetch(`${ORIGIN}/api/v1/items:ingest`, {
      method: "POST",
      headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
      body: JSON.stringify(initial),
    });
    expect(createdResponse.status).toBe(200);
    const createdBody = await json(createdResponse);
    const createdResult = createdBody.result as Array<{
      item: { id: string; version: number; responsibility: string };
      created: boolean;
    }>;
    const itemId = createdResult[0]?.item.id ?? "";
    expect(itemId).toMatch(/^[0-9a-f-]{36}$/);
    expect(createdResult[0]?.created).toBe(true);
    expect(createdResult[0]?.item.responsibility).toBe("counterparty");

    const refresh = structuredClone(initial);
    refresh.items[0]!.idempotencyKey = "test:gmail:agreement:2";
    refresh.items[0]!.sourceRef = "gmail:message-2";
    refresh.items[0]!.sourceVersion = "message-2";
    refresh.items[0]!.summary = "공문 발급 완료";
    const refreshedResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/items:ingest`,
      {
        method: "POST",
        headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify(refresh),
      },
    );
    const refreshedBody = await json(refreshedResponse);
    const refreshedResult = refreshedBody.result as Array<{
      item: { id: string; version: number; resolution: string };
    }>;
    expect(refreshedResult[0]?.item.id).toBe(itemId);
    expect(refreshedResult[0]?.item.version).toBe(2);
    expect(refreshedResult[0]?.item.resolution).toBe("active");

    const duplicateResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/items:ingest`,
      {
        method: "POST",
        headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify(refresh),
      },
    );
    const duplicateBody = await json(duplicateResponse);
    const duplicateResult = duplicateBody.result as Array<{
      item: { id: string; version: number };
      duplicate: boolean;
    }>;
    expect(duplicateResult[0]).toMatchObject({
      item: { id: itemId, version: 2 },
      duplicate: true,
    });

    const boardResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/board?week=2026-08-31`,
      { headers: SITE_AUTH },
    );
    const boardBody = await json(boardResponse);
    const board = boardBody.result as {
      items: Array<{ id: string; summary: string; version: number }>;
    };
    expect(board.items).toContainEqual(
      expect.objectContaining({
        id: itemId,
        summary: "공문 발급 완료",
        version: 2,
      }),
    );

    const rejectedResolution = await SELF.fetch(
      `${ORIGIN}/api/v1/items/${itemId}/resolution`,
      {
        method: "PATCH",
        headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify({
          resolution: "completed",
          idempotencyKey: "test:resolution:automation",
          expectedVersion: 2,
          occurredAt: null,
        }),
      },
    );
    expect(rejectedResolution.status).toBe(403);

    const resolutionPayload = {
      resolution: "completed",
      idempotencyKey: "test:resolution:owner:1",
      expectedVersion: 2,
      occurredAt: "2026-09-02T05:00:00.000Z",
    };
    const resolvedResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/items/${itemId}/resolution`,
      {
        method: "PATCH",
        headers: { ...SITE_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify(resolutionPayload),
      },
    );
    const resolvedBody = await json(resolvedResponse);
    expect(resolvedBody.result).toMatchObject({
      item: { id: itemId, resolution: "completed", version: 3 },
      duplicate: false,
    });

    const repeatedResolution = await SELF.fetch(
      `${ORIGIN}/api/v1/items/${itemId}/resolution`,
      {
        method: "PATCH",
        headers: { ...SITE_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify(resolutionPayload),
      },
    );
    const repeatedBody = await json(repeatedResolution);
    expect(repeatedBody.result).toMatchObject({
      item: { id: itemId, resolution: "completed", version: 3 },
      duplicate: true,
    });

    const afterOwnerRefresh = structuredClone(refresh);
    afterOwnerRefresh.items[0]!.idempotencyKey = "test:gmail:agreement:3";
    afterOwnerRefresh.items[0]!.sourceRef = "gmail:message-3";
    afterOwnerRefresh.items[0]!.sourceVersion = "message-3";
    afterOwnerRefresh.items[0]!.summary = "완료 상태 재확인";
    const afterOwnerResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/items:ingest`,
      {
        method: "POST",
        headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify(afterOwnerRefresh),
      },
    );
    const afterOwnerBody = await json(afterOwnerResponse);
    expect(afterOwnerBody.result).toMatchObject([
      {
        item: {
          id: itemId,
          resolution: "completed",
          responsibility: "counterparty",
          version: 4,
        },
      },
    ]);

    const toolsResponse = await SELF.fetch(`${ORIGIN}/mcp`, {
      method: "POST",
      headers: {
        ...SITE_AUTH,
        Host: "journal.example.test",
        Accept: "application/json, text/event-stream",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
    });
    expect(toolsResponse.status, await toolsResponse.clone().text()).toBe(200);
    const tools = await mcpJson(toolsResponse);
    expect(JSON.stringify(tools)).toContain("journal_get_board");
    expect(JSON.stringify(tools)).toContain("journal_find_items");
    expect(JSON.stringify(tools)).toContain("journal_get_item_history");
    expect(JSON.stringify(tools)).toContain("journal_set_resolution");

    const mcpBoardResponse = await SELF.fetch(`${ORIGIN}/mcp`, {
      method: "POST",
      headers: {
        ...SITE_AUTH,
        Host: "journal.example.test",
        Accept: "application/json, text/event-stream",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: {
          name: "journal_get_board",
          arguments: { weekId: "2026-08-31", includeResolved: true },
        },
      }),
    });
    const mcpBoard = await mcpJson(mcpBoardResponse);
    expect(JSON.stringify(mcpBoard)).toContain(itemId);
    expect(JSON.stringify(mcpBoard)).toContain("completed");

    const foundResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/items?week=2026-08-31&project=industrial-ai&query=%EA%B3%B5%EB%AC%B8`,
      { headers: SITE_AUTH },
    );
    const foundBody = await json(foundResponse);
    expect(foundBody.result).toMatchObject({
      count: 1,
      items: [{ id: itemId, responsibility: "counterparty" }],
    });

    const detailResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/items/${itemId}`,
      { headers: SITE_AUTH },
    );
    const detailBody = await json(detailResponse);
    expect(detailBody.result).toMatchObject({
      item: { id: itemId, sourceRef: "gmail:message-3" },
    });
    expect(JSON.stringify(detailBody.result)).toContain("resolution_changed");
  });

  it("starts a new weekly instance for the same source after close", async () => {
    const baseItem = {
      sourceKind: "calendar",
      sourceKey: "weekly-follow-up",
      sourceRef: "calendar:event-1",
      sourceVersion: "1",
      projectKey: "journal-test",
      title: "주간 후속 확인",
      summary: "첫 주 진행 중",
      lane: "direct",
      dueAt: null,
      durableOutcome: null,
      corpusTargetSpace: null,
      occurredAt: "2026-08-18T00:00:00.000Z",
    };
    const firstResponse = await SELF.fetch(`${ORIGIN}/api/v1/items:ingest`, {
      method: "POST",
      headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
      body: JSON.stringify({
        items: [
          {
            ...baseItem,
            idempotencyKey: "test:weekly-source:first",
            weekId: "2026-08-17",
          },
        ],
      }),
    });
    const firstBody = await json(firstResponse);
    const first = firstBody.result as Array<{
      item: { id: string; logicalItemId: string; weekId: string };
      created: boolean;
    }>;

    const holdResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/items/${first[0]?.item.id}/resolution`,
      {
        method: "PATCH",
        headers: { ...SITE_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify({
          resolution: "held",
          idempotencyKey: "test:weekly-source:hold",
          expectedVersion: 1,
          occurredAt: "2026-08-18T01:00:00.000Z",
        }),
      },
    );
    expect(holdResponse.status).toBe(200);

    const preparation = await prepareWeek("2026-08-17");
    const closeResponse = await confirmWeek(
      "2026-08-17",
      preparation.preparationVersion as string,
      "test:weekly-source:close",
      "2026-08-23T12:00:00.000Z",
    );
    expect(closeResponse.status).toBe(200);
    const closeBody = await json(closeResponse);
    expect(closeBody.result).toMatchObject({
      summary: { rolloverCount: 1, rolloverTitles: ["주간 후속 확인"] },
    });

    const nextBoardResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/board?week=2026-08-24&include_resolved=true`,
      { headers: SITE_AUTH },
    );
    const nextBoardBody = await json(nextBoardResponse);
    const nextBoard = nextBoardBody.result as {
      items: Array<{
        id: string;
        logicalItemId: string;
        weekId: string;
        resolution: string;
        version: number;
      }>;
    };
    const rolledOver = nextBoard.items.find(
      (item) => item.logicalItemId === first[0]?.item.logicalItemId,
    );
    expect(rolledOver).toMatchObject({
      weekId: "2026-08-24",
      resolution: "active",
      version: 1,
    });

    const carriedResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/items:ingest`,
      {
        method: "POST",
        headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify({
          items: [
            {
              ...baseItem,
              idempotencyKey: "test:weekly-source:carried",
              sourceRef: "calendar:event-2",
              sourceVersion: "2",
              weekId: "2026-08-24",
              summary: "다음 주로 이월",
              occurredAt: "2026-08-25T00:00:00.000Z",
            },
          ],
        }),
      },
    );
    expect(carriedResponse.status).toBe(200);
    const carriedBody = await json(carriedResponse);
    const carried = carriedBody.result as Array<{
      item: {
        id: string;
        logicalItemId: string;
        weekId: string;
        version: number;
      };
      created: boolean;
    }>;
    expect(first[0]).toMatchObject({
      item: { weekId: "2026-08-17" },
      created: true,
    });
    expect(carried[0]).toMatchObject({
      item: {
        id: rolledOver?.id,
        logicalItemId: first[0]?.item.logicalItemId,
        weekId: "2026-08-24",
        version: 2,
      },
      created: false,
    });
    expect(carried[0]?.item.id).not.toBe(first[0]?.item.id);
  });

  it("rejects a stale close preparation without rolling items forward", async () => {
    const ingestResponse = await SELF.fetch(`${ORIGIN}/api/v1/items:ingest`, {
      method: "POST",
      headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
      body: JSON.stringify({
        items: [
          {
            idempotencyKey: "test:stale-close:ingest",
            sourceKind: "codex",
            sourceKey: "stale-close-item",
            sourceRef: "codex:stale-close-item",
            sourceVersion: "1",
            weekId: "2026-09-14",
            projectKey: "journal-test",
            title: "마감 충돌 시험",
            summary: "진행 중",
            lane: "direct",
            dueAt: null,
            durableOutcome: null,
            corpusTargetSpace: null,
            occurredAt: "2026-09-15T00:00:00.000Z",
          },
        ],
      }),
    });
    const ingested = (await json(ingestResponse)).result as Array<{
      item: { id: string; version: number };
    }>;
    const itemId = ingested[0]?.item.id ?? "";
    const preparation = await prepareWeek("2026-09-14");

    const holdResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/items/${itemId}/resolution`,
      {
        method: "PATCH",
        headers: { ...SITE_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify({
          resolution: "held",
          idempotencyKey: "test:stale-close:hold",
          expectedVersion: 1,
          occurredAt: "2026-09-15T01:00:00.000Z",
        }),
      },
    );
    expect(holdResponse.status).toBe(200);

    const staleClose = await confirmWeek(
      "2026-09-14",
      preparation.preparationVersion as string,
      "test:stale-close:confirm",
      "2026-09-20T12:00:00.000Z",
    );
    expect(staleClose.status).toBe(409);
    expect(await json(staleClose)).toMatchObject({
      error: { code: "close_preparation_stale" },
    });

    const nextBoard = await SELF.fetch(
      `${ORIGIN}/api/v1/board?week=2026-09-21&include_resolved=true`,
      { headers: SITE_AUTH },
    );
    expect((await json(nextBoard)).result).toMatchObject({ items: [] });
  });

  it("freezes a week, accepts only correction events, and returns period totals", async () => {
    const preparation = await prepareWeek("2026-08-31");
    const closeResponse = await confirmWeek(
      "2026-08-31",
      preparation.preparationVersion as string,
      "test:close:2026-08-31",
      "2026-09-06T12:00:00.000Z",
    );
    expect(closeResponse.status).toBe(200);
    const closeBody = await json(closeResponse);
    expect(closeBody.result).toMatchObject({
      week: { id: "2026-08-31", status: "closed" },
      summary: { counts: { completed: 1 } },
      alreadyClosed: false,
    });

    const lateIngestResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/items:ingest`,
      {
        method: "POST",
        headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify({
          items: [
            {
              idempotencyKey: "test:late:1",
              sourceKind: "codex",
              sourceKey: "late-task",
              sourceRef: "codex:late-task",
              sourceVersion: "1",
              weekId: "2026-08-31",
              projectKey: null,
              title: "늦은 항목",
              summary: "마감 후 발견",
              lane: "attention",
              dueAt: null,
              durableOutcome: null,
              corpusTargetSpace: null,
              occurredAt: null,
            },
          ],
        }),
      },
    );
    expect(lateIngestResponse.status).toBe(409);

    const correctionResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/weeks/2026-08-31/corrections`,
      {
        method: "POST",
        headers: { ...SITE_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify({
          note: "마감 이후 확인된 설명을 덧붙임",
          sourceRef: "codex:correction",
          idempotencyKey: "test:correction:1",
          occurredAt: null,
        }),
      },
    );
    expect(correctionResponse.status).toBe(200);

    const periodResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/period?kind=quarter&anchor=2026-09-02`,
      { headers: SITE_AUTH },
    );
    const periodBody = await json(periodResponse);
    expect(periodBody.result).toMatchObject({
      kind: "quarter",
      startsOn: "2026-07-01",
      endsOn: "2026-09-30",
      totals: { completed: 1 },
    });

    const firstSummaryPayload = {
      kind: "quarter",
      anchor: "2026-09-02",
      body: "협약변경 공문 처리를 마쳤다.",
      expectedVersion: null,
      idempotencyKey: "test:period-summary:quarter:v1",
    };
    const rejectedSummary = await SELF.fetch(
      `${ORIGIN}/api/v1/period-summaries`,
      {
        method: "POST",
        headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify(firstSummaryPayload),
      },
    );
    expect(rejectedSummary.status).toBe(403);

    const firstSummary = await SELF.fetch(
      `${ORIGIN}/api/v1/period-summaries`,
      {
        method: "POST",
        headers: { ...SITE_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify(firstSummaryPayload),
      },
    );
    expect(await json(firstSummary)).toMatchObject({
      result: {
        summary: { version: 1, body: "협약변경 공문 처리를 마쳤다." },
        duplicate: false,
      },
    });

    const secondSummary = await SELF.fetch(
      `${ORIGIN}/api/v1/period-summaries`,
      {
        method: "POST",
        headers: { ...SITE_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify({
          ...firstSummaryPayload,
          body: "협약변경 공문 처리와 후속 정정을 기록했다.",
          expectedVersion: 1,
          idempotencyKey: "test:period-summary:quarter:v2",
        }),
      },
    );
    expect(await json(secondSummary)).toMatchObject({
      result: { summary: { version: 2 }, duplicate: false },
    });

    const summaryPeriod = await SELF.fetch(
      `${ORIGIN}/api/v1/period?kind=quarter&anchor=2026-09-02`,
      { headers: SITE_AUTH },
    );
    const summaryPeriodBody = await json(summaryPeriod);
    expect(summaryPeriodBody.result).toMatchObject({
      currentSummary: {
        version: 2,
        body: "협약변경 공문 처리와 후속 정정을 기록했다.",
      },
      summaryVersions: [{ version: 1 }, { version: 2 }],
    });
  });

  it("returns explicit Corpus candidates and de-duplicates promotion receipts", async () => {
    const ingestResponse = await SELF.fetch(`${ORIGIN}/api/v1/items:ingest`, {
      method: "POST",
      headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
      body: JSON.stringify({
        items: [
          {
            idempotencyKey: "test:corpus-candidate:ingest",
            sourceKind: "codex",
            sourceKey: "journal-release",
            sourceRef: "codex:journal-release",
            sourceVersion: "verified-v1",
            weekId: "2026-10-05",
            projectKey: "personal-agent-toolkit",
            title: "Journal 배포",
            summary: "검증 완료",
            lane: "direct",
            dueAt: null,
            durableOutcome: "Journal 1차 제품을 배포함",
            corpusTargetSpace: "toolkit-project",
            occurredAt: "2026-10-06T00:00:00.000Z",
          },
        ],
      }),
    });
    const ingestBody = await json(ingestResponse);
    const ingested = ingestBody.result as Array<{
      item: { id: string; version: number };
    }>;
    const itemId = ingested[0]?.item.id ?? "";

    const resolveResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/items/${itemId}/resolution`,
      {
        method: "PATCH",
        headers: { ...SITE_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify({
          resolution: "completed",
          idempotencyKey: "test:corpus-candidate:complete",
          expectedVersion: 1,
          occurredAt: "2026-10-06T01:00:00.000Z",
        }),
      },
    );
    expect(resolveResponse.status).toBe(200);

    const preparation = await prepareWeek("2026-10-05");
    expect(preparation).toMatchObject({
      summary: { rolloverCount: 0 },
      corpusCandidates: [
        {
          itemId,
          projectKey: "personal-agent-toolkit",
          targetSpace: "toolkit-project",
          durableOutcome: "Journal 1차 제품을 배포함",
        },
      ],
    });
    const candidates = preparation.corpusCandidates as Array<{
      itemId: string;
      contentHash: string;
    }>;
    const candidateHash = candidates[0]?.contentHash ?? "";

    const prematureClose = await confirmWeek(
      "2026-10-05",
      preparation.preparationVersion as string,
      "test:corpus-candidate:premature-close",
      "2026-10-11T11:00:00.000Z",
    );
    expect(prematureClose.status).toBe(409);
    expect(await json(prematureClose)).toMatchObject({
      error: { code: "corpus_reflection_pending" },
    });

    const receipt = {
      weekId: "2026-10-05",
      itemId,
      targetSpace: "toolkit-project",
      sourcePath: "docs/journal-release.md",
      contentHash: candidateHash,
      status: "applied",
      details: "verified",
      idempotencyKey: "test:corpus-candidate:receipt:1",
      occurredAt: "2026-10-12T00:00:00.000Z",
    };
    const rejectedReceipt = await SELF.fetch(
      `${ORIGIN}/api/v1/corpus-promotions`,
      {
        method: "POST",
        headers: { ...INGEST_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify(receipt),
      },
    );
    expect(rejectedReceipt.status).toBe(403);

    const receiptResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/corpus-promotions`,
      {
        method: "POST",
        headers: { ...SITE_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify(receipt),
      },
    );
    const receiptBody = await json(receiptResponse);
    expect(receiptBody.result).toMatchObject({ duplicate: false });

    const closeResponse = await confirmWeek(
      "2026-10-05",
      preparation.preparationVersion as string,
      "test:corpus-candidate:close",
      "2026-10-11T12:00:00.000Z",
    );
    const closeBody = await json(closeResponse);
    expect(closeBody.result).toMatchObject({
      week: { status: "closed" },
      corpusCandidates: [{ itemId, contentHash: candidateHash }],
    });

    const duplicateResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/corpus-promotions`,
      {
        method: "POST",
        headers: { ...SITE_AUTH, "Content-Type": "application/json" },
        body: JSON.stringify({
          ...receipt,
          idempotencyKey: "test:corpus-candidate:receipt:2",
        }),
      },
    );
    const duplicateBody = await json(duplicateResponse);
    expect(duplicateBody.result).toMatchObject({ duplicate: true });
  });
});
