import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { JournalRepository, type EventRow } from "../src/repository";
import type { Env, ItemRecord } from "../src/types";

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
