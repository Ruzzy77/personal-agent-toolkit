import { env } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";

import { JournalRepository, type EventRow } from "../src/repository";
import { JournalService } from "../src/service";
import { getBoardOutputSchema } from "../src/schemas";
import type { Env, ItemRecord, Principal, PromotionReceiptInput } from "../src/types";

const NOW = "2030-01-07T00:00:00.000Z";

function itemRecord(weekId: string, label: string): ItemRecord {
  const id = crypto.randomUUID();
  return {
    id,
    logicalItemId: id,
    weekId,
    sourceKind: "test",
    sourceKey: `source:${label}:${id}`,
    sourceRef: `test:${label}`,
    sourceVersion: "1",
    projectKey: "journal-consistency",
    title: `일관성 시험 ${label}`,
    summary: "변경 전",
    lane: "direct",
    resolution: "active",
    responsibility: "user",
    dueAt: null,
    durableOutcome: null,
    corpusTargetSpace: null,
    version: 1,
    createdAt: NOW,
    updatedAt: NOW,
  };
}

function itemEvent(
  item: ItemRecord,
  eventType:
    | "item_created"
    | "item_rolled_over"
    | "observation_updated"
    | "resolution_changed",
  idempotencyKey: string,
): EventRow {
  return {
    id: crypto.randomUUID(),
    week_id: item.weekId,
    item_id: item.id,
    event_type: eventType,
    actor_kind: "owner",
    actor_ref: "test:owner",
    payload_json: "{}",
    idempotency_key: idempotencyKey,
    occurred_at: item.updatedAt,
    created_at: item.updatedAt,
  };
}

function receipt(item: ItemRecord, idempotencyKey: string) {
  return {
    idempotencyKey,
    sourceKind: item.sourceKind,
    sourceKey: item.sourceKey,
    sourceVersion: item.sourceVersion,
    createdAt: item.updatedAt,
  };
}

async function closeWeek(db: D1Database, weekId: string): Promise<void> {
  await db
    .prepare(
      "UPDATE weeks SET status = 'closed', closed_at = ?, updated_at = ? WHERE id = ?",
    )
    .bind(NOW, NOW, weekId)
    .run();
}

describe("Journal repository week guards", () => {
  it("does not create an item after its week closes", async () => {
    const db = (env as unknown as Env).DB;
    const repository = new JournalRepository(db);
    const weekId = "2030-01-07";
    const item = itemRecord(weekId, "create");
    const idempotencyKey = `test:closed-create:${item.id}`;

    await repository.ensureWeek(weekId, NOW);
    await closeWeek(db, weekId);

    await expect(
      repository.insertItem(
        item,
        itemEvent(item, "item_created", idempotencyKey),
        receipt(item, idempotencyKey),
      ),
    ).rejects.toMatchObject({ code: "week_closed", status: 409 });
    await expect(repository.getItem(item.id)).resolves.toBeNull();
    await expect(repository.eventExists(idempotencyKey)).resolves.toBe(false);
    await expect(repository.getReceipt(idempotencyKey)).resolves.toBeNull();
  });

  it("does not update an observation or resolution after its week closes", async () => {
    const db = (env as unknown as Env).DB;
    const repository = new JournalRepository(db);
    const weekId = "2030-01-14";
    const initial = itemRecord(weekId, "update");
    const createKey = `test:closed-update:create:${initial.id}`;

    await repository.ensureWeek(weekId, NOW);
    await repository.insertItem(
      initial,
      itemEvent(initial, "item_created", createKey),
      receipt(initial, createKey),
    );
    await closeWeek(db, weekId);

    const updated = {
      ...initial,
      summary: "마감 뒤 변경",
      sourceVersion: "2",
      version: 2,
      updatedAt: "2030-01-07T01:00:00.000Z",
    };
    const updateKey = `test:closed-update:observation:${initial.id}`;
    await expect(
      repository.updateItemObservation(
        updated,
        itemEvent(updated, "observation_updated", updateKey),
        receipt(updated, updateKey),
      ),
    ).rejects.toMatchObject({ code: "week_closed", status: 409 });

    const resolved = {
      ...initial,
      resolution: "completed" as const,
      version: 2,
      updatedAt: "2030-01-07T02:00:00.000Z",
    };
    const resolutionKey = `test:closed-update:resolution:${initial.id}`;
    await expect(
      repository.setResolution(
        resolved,
        "active",
        itemEvent(resolved, "resolution_changed", resolutionKey),
      ),
    ).rejects.toMatchObject({ code: "week_closed", status: 409 });

    await expect(repository.getItem(initial.id)).resolves.toMatchObject({
      summary: "변경 전",
      resolution: "active",
      version: 1,
    });
    await expect(repository.eventExists(updateKey)).resolves.toBe(false);
    await expect(repository.eventExists(resolutionKey)).resolves.toBe(false);
    await expect(repository.getReceipt(updateKey)).resolves.toBeNull();
  });

  it("keeps the source week open if the rollover week closes first", async () => {
    const db = (env as unknown as Env).DB;
    const repository = new JournalRepository(db);
    const sourceWeekId = "2030-01-21";
    const rolloverWeekId = "2030-01-28";
    const source = itemRecord(sourceWeekId, "rollover-source");
    const createKey = `test:rollover-race:create:${source.id}`;

    await repository.ensureWeek(sourceWeekId, NOW);
    await repository.ensureWeek(rolloverWeekId, NOW);
    await repository.insertItem(
      source,
      itemEvent(source, "item_created", createKey),
      receipt(source, createKey),
    );
    await closeWeek(db, rolloverWeekId);

    const rollover = {
      ...source,
      id: crypto.randomUUID(),
      weekId: rolloverWeekId,
      resolution: "active" as const,
      version: 1,
      createdAt: "2030-01-07T03:00:00.000Z",
      updatedAt: "2030-01-07T03:00:00.000Z",
    };
    const closedAt = "2030-01-27T12:00:00.000Z";
    const closeKey = `test:rollover-race:close:${source.id}`;
    const closeEvent: EventRow = {
      id: crypto.randomUUID(),
      week_id: sourceWeekId,
      item_id: null,
      event_type: "week_closed",
      actor_kind: "owner",
      actor_ref: "test:owner",
      payload_json: "{}",
      idempotency_key: closeKey,
      occurred_at: closedAt,
      created_at: closedAt,
    };
    const rolloverKey = `test:rollover-race:item:${source.id}`;

    await expect(
      repository.closeWeek(
        sourceWeekId,
        {
          weekId: sourceWeekId,
          counts: { active: 1, held: 0, completed: 0, canceled: 0 },
          laneCounts: { today: 0, direct: 1, waiting: 0, attention: 0 },
          projectCounts: [{ projectKey: "journal-consistency", count: 1 }],
          completedTitles: [],
          rolloverCount: 1,
          rolloverTitles: [source.title],
        },
        [],
        "test:owner",
        closedAt,
        closeEvent,
        [
          {
            item: rollover,
            event: itemEvent(rollover, "item_rolled_over", rolloverKey),
          },
        ],
        [{ id: source.id, version: source.version }],
      ),
    ).rejects.toMatchObject({ code: "week_changed_during_close", status: 409 });

    await expect(repository.getWeek(sourceWeekId)).resolves.toMatchObject({
      status: "open",
    });
    await expect(repository.getClosure(sourceWeekId)).resolves.toBeNull();
    await expect(repository.getItem(rollover.id)).resolves.toBeNull();
    await expect(repository.eventExists(closeKey)).resolves.toBe(false);
    await expect(repository.eventExists(rolloverKey)).resolves.toBe(false);
  });
});


const owner: Principal = {
  kind: "owner", id: "test-owner", auth: "site-token", scopes: new Set(["journal.close", "journal.write"]),
};

async function candidateFixture(weekId: string) {
  const db = (env as unknown as Env).DB;
  const repository = new JournalRepository(db);
  const service = new JournalService(db, () => new Date(NOW));
  const item = { ...itemRecord(weekId, "reflection"), resolution: "held" as const,
    responsibility: "counterparty" as const, durableOutcome: "Confirmed outcome", corpusTargetSpace: "project" };
  await repository.ensureWeek(weekId, NOW);
  const key = `create:${item.id}`;
  await repository.insertItem(item, itemEvent(item, "item_created", key), receipt(item, key));
  const preparation = await service.prepareWeekClose(weekId, owner);
  const input: PromotionReceiptInput = {
    weekId, itemId: item.id, targetSpace: "project", sourcePath: "docs/result.md",
    contentHash: preparation.corpusCandidates[0]!.contentHash, status: "failed", details: "target unavailable",
    idempotencyKey: `promotion:${item.id}`, occurredAt: null,
  };
  return { db, repository, service, item, preparation, input };
}

describe("Corpus reflection independent of week close", () => {
  it("retains failures and frozen records while retrying or skipping after close", async () => {
    const { repository, service, item, preparation, input } = await candidateFixture("2031-01-06");
    const failed = await service.recordPromotion(input, owner);
    const closed = await service.confirmWeekClose(item.weekId, preparation.preparationVersion, `close:${item.id}`, null, owner);
    expect(closed.week.status).toBe("closed");
    const snapshot = await repository.getClosure(item.weekId);
    const frozenItem = await repository.getItem(item.id);
    const successor = await repository.getItemBySource("2031-01-13", item.sourceKind, item.sourceKey);
    expect(successor).toMatchObject({ resolution: "held", responsibility: "counterparty", durableOutcome: null, corpusTargetSpace: null });
    await expect(service.recordPromotion(input, owner)).resolves.toEqual({ ...failed, duplicate: true });
    await expect(service.recordPromotion({ ...input, details: "different" }, owner)).rejects.toMatchObject({ code: "idempotency_conflict" });
    const correction = await service.addCorrection(item.weekId, {
      itemId: item.id, note: "Do not apply the original claim; see corrected project context", sourceRef: null,
      idempotencyKey: `correction:${item.id}`, occurredAt: null,
    }, owner);
    const failedAgain = { ...input, idempotencyKey: `retry:${item.id}` };
    await service.recordPromotion(failedAgain, owner);
    const board = await service.getBoard(item.weekId, true);
    expect(getBoardOutputSchema.safeParse(board).success).toBe(true);
    expect(board.closure).toMatchObject({
      corpusCandidates: [{ itemId: item.id, reflectionStatus: "failed", contentHash: input.contentHash }],
      corrections: [{ id: correction.eventId }],
    });
    for (const mismatch of [{ contentHash: "sha256:wrong" }, { targetSpace: "wrong" }, { itemId: null }]) {
      await expect(service.recordPromotion({ ...input, ...mismatch, idempotencyKey: crypto.randomUUID() }, owner))
        .rejects.toMatchObject({ code: "promotion_candidate_mismatch" });
    }
    const skipped = { ...input, status: "skipped" as const, details: `Superseded; correction ${correction.eventId}`, idempotencyKey: `skip:${item.id}` };
    const completed = await service.recordPromotion(skipped, owner);
    await expect(service.recordPromotion({ ...input, idempotencyKey: `late-failure:${item.id}` }, owner))
      .resolves.toEqual({ ...completed, duplicate: true });
    expect((await service.getBoard(item.weekId)).closure?.corpusCandidates[0]?.reflectionStatus).toBe("skipped");
    expect(await repository.listPromotionReceipts(item.weekId)).toHaveLength(3);
    expect(await repository.getClosure(item.weekId)).toEqual(snapshot);
    expect(await repository.getItem(item.id)).toEqual(frozenItem);
    expect(await repository.getWeek(item.weekId)).toEqual(closed.week);
    expect(await repository.getItem(successor!.id)).toEqual(successor);
  });

  it.each([false, true])("guards a receipt when close wins the insert race (candidate changed: %s)", async (changed) => {
    const { repository, service, item, preparation, input } = await candidateFixture(changed ? "2031-02-10" : "2031-01-27");
    const insert = JournalRepository.prototype.insertPromotionReceipt;
    const spy = vi.spyOn(JournalRepository.prototype, "insertPromotionReceipt").mockImplementationOnce(async function (this: JournalRepository, ...args) {
      if (changed) {
        const updated = { ...item, durableOutcome: "Different confirmed outcome", version: item.version + 1 };
        const key = `change:${item.id}`;
        await repository.updateItemObservation(updated, itemEvent(updated, "observation_updated", key), receipt(updated, key));
      }
      const current = changed ? await service.prepareWeekClose(item.weekId, owner) : preparation;
      await service.confirmWeekClose(item.weekId, current.preparationVersion, `race-close:${item.id}`, null, owner);
      return insert.apply(this, args);
    });
    try {
      const result = service.recordPromotion({ ...input, status: "applied" }, owner);
      if (changed) {
        await expect(result).rejects.toMatchObject({ code: "promotion_candidate_mismatch" });
        expect(await repository.listPromotionReceipts(item.weekId)).toEqual([]);
        expect(await repository.eventExists(input.idempotencyKey)).toBe(false);
      } else {
        await expect(result).resolves.toMatchObject({ duplicate: false });
        expect(await repository.eventExists(input.idempotencyKey)).toBe(true);
      }
    } finally { spy.mockRestore(); }
  });
});
