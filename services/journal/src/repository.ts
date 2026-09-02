import { JournalError } from "./errors";
import { addDays } from "./time";
import type {
  CorpusCandidate,
  ItemRecord,
  Lane,
  PromotionReceiptInput,
  Resolution,
  WeekClosureSummary,
  WeekRecord,
} from "./types";

interface WeekRow {
  id: string;
  starts_on: string;
  ends_on: string;
  timezone: "Asia/Seoul";
  status: "open" | "closed";
  revision: number;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
}

interface ItemRow {
  id: string;
  logical_item_id: string;
  week_id: string;
  source_kind: string;
  source_key: string;
  source_ref: string | null;
  source_version: string | null;
  project_key: string | null;
  title: string;
  summary: string;
  lane: Lane;
  resolution: Resolution;
  due_at: string | null;
  durable_outcome: string | null;
  corpus_target_space: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface EventRow {
  id: string;
  week_id: string;
  item_id: string | null;
  event_type: string;
  actor_kind: string;
  actor_ref: string | null;
  payload_json: string;
  idempotency_key: string;
  occurred_at: string;
  created_at: string;
  item_title?: string | null;
}

interface ReceiptRow {
  idempotency_key: string;
  item_id: string;
}

interface ClosureRow {
  week_id: string;
  summary_json: string;
  corpus_candidates_json: string;
  closed_by: string;
  closed_at: string;
}

export function toWeek(row: WeekRow): WeekRecord {
  return {
    id: row.id,
    startsOn: row.starts_on,
    endsOn: row.ends_on,
    timezone: row.timezone,
    status: row.status,
    revision: row.revision,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    closedAt: row.closed_at,
  };
}

export function toItem(row: ItemRow): ItemRecord {
  return {
    id: row.id,
    logicalItemId: row.logical_item_id,
    weekId: row.week_id,
    sourceKind: row.source_kind,
    sourceKey: row.source_key,
    sourceRef: row.source_ref,
    sourceVersion: row.source_version,
    projectKey: row.project_key,
    title: row.title,
    summary: row.summary,
    lane: row.lane,
    resolution: row.resolution,
    dueAt: row.due_at,
    durableOutcome: row.durable_outcome,
    corpusTargetSpace: row.corpus_target_space,
    version: row.version,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export class JournalRepository {
  constructor(private readonly db: D1Database) {}

  async getWeek(id: string): Promise<WeekRecord | null> {
    const row = await this.db
      .prepare("SELECT * FROM weeks WHERE id = ?")
      .bind(id)
      .first<WeekRow>();
    return row ? toWeek(row) : null;
  }

  virtualWeek(id: string, now: string): WeekRecord {
    return {
      id,
      startsOn: id,
      endsOn: addDays(id, 6),
      timezone: "Asia/Seoul",
      status: "open",
      revision: 0,
      createdAt: now,
      updatedAt: now,
      closedAt: null,
    };
  }

  async ensureWeek(id: string, now: string): Promise<WeekRecord> {
    await this.db
      .prepare(
        `INSERT OR IGNORE INTO weeks
          (id, starts_on, ends_on, timezone, status, revision, created_at, updated_at)
         VALUES (?, ?, ?, 'Asia/Seoul', 'open', 1, ?, ?)`,
      )
      .bind(id, id, addDays(id, 6), now, now)
      .run();
    const week = await this.getWeek(id);
    if (!week) {
      throw new JournalError("storage_error", "week could not be created", 500);
    }
    return week;
  }

  async listItems(
    weekId: string,
    includeResolved: boolean,
  ): Promise<ItemRecord[]> {
    const where = includeResolved
      ? "week_id = ?"
      : "week_id = ? AND resolution IN ('active', 'held')";
    const rows = await this.db
      .prepare(
        `SELECT * FROM items
         WHERE ${where}
         ORDER BY
           CASE resolution WHEN 'active' THEN 0 WHEN 'held' THEN 1 ELSE 2 END,
           CASE lane
             WHEN 'today' THEN 0
             WHEN 'direct' THEN 1
             WHEN 'waiting' THEN 2
             ELSE 3
           END,
           updated_at DESC,
           id`,
      )
      .bind(weekId)
      .all<ItemRow>();
    return rows.results.map(toItem);
  }

  async listAllItems(weekId: string): Promise<ItemRecord[]> {
    return this.listItems(weekId, true);
  }

  async getItem(id: string): Promise<ItemRecord | null> {
    const row = await this.db
      .prepare("SELECT * FROM items WHERE id = ?")
      .bind(id)
      .first<ItemRow>();
    return row ? toItem(row) : null;
  }

  async getItemBySource(
    weekId: string,
    sourceKind: string,
    sourceKey: string,
  ): Promise<ItemRecord | null> {
    const row = await this.db
      .prepare(
        "SELECT * FROM items WHERE week_id = ? AND source_kind = ? AND source_key = ?",
      )
      .bind(weekId, sourceKind, sourceKey)
      .first<ItemRow>();
    return row ? toItem(row) : null;
  }

  async getLatestItemBySource(
    sourceKind: string,
    sourceKey: string,
  ): Promise<ItemRecord | null> {
    const row = await this.db
      .prepare(
        `SELECT * FROM items
         WHERE source_kind = ? AND source_key = ?
         ORDER BY week_id DESC, updated_at DESC
         LIMIT 1`,
      )
      .bind(sourceKind, sourceKey)
      .first<ItemRow>();
    return row ? toItem(row) : null;
  }

  async getReceipt(idempotencyKey: string): Promise<ReceiptRow | null> {
    return this.db
      .prepare(
        "SELECT idempotency_key, item_id FROM ingest_receipts WHERE idempotency_key = ?",
      )
      .bind(idempotencyKey)
      .first<ReceiptRow>();
  }

  async eventExists(idempotencyKey: string): Promise<boolean> {
    const row = await this.db
      .prepare("SELECT 1 AS found FROM journal_events WHERE idempotency_key = ?")
      .bind(idempotencyKey)
      .first<{ found: number }>();
    return row?.found === 1;
  }

  async insertItem(
    item: ItemRecord,
    event: EventRow,
    receipt: {
      idempotencyKey: string;
      sourceKind: string;
      sourceKey: string;
      sourceVersion: string | null;
      createdAt: string;
    },
  ): Promise<void> {
    await this.db.batch([
      this.insertItemStatement(item),
      this.insertEventForItemVersionStatement(
        event,
        item.id,
        item.version,
        item.updatedAt,
      ),
      this.db
        .prepare(
          `INSERT INTO ingest_receipts
            (idempotency_key, source_kind, source_key, source_version, item_id, created_at)
           SELECT ?, ?, ?, ?, ?, ?
           FROM items
           WHERE id = ? AND version = ? AND updated_at = ?`,
        )
        .bind(
          receipt.idempotencyKey,
          receipt.sourceKind,
          receipt.sourceKey,
          receipt.sourceVersion,
          item.id,
          receipt.createdAt,
          item.id,
          item.version,
          item.updatedAt,
        ),
    ]);
  }

  async updateItemObservation(
    item: ItemRecord,
    event: EventRow,
    receipt: {
      idempotencyKey: string;
      sourceKind: string;
      sourceKey: string;
      sourceVersion: string | null;
      createdAt: string;
    },
  ): Promise<void> {
    const results = await this.db.batch([
      this.db
        .prepare(
          `UPDATE items SET
            week_id = ?, source_ref = ?, source_version = ?, project_key = ?,
            title = ?, summary = ?, lane = ?, due_at = ?, durable_outcome = ?,
            corpus_target_space = ?, version = version + 1, updated_at = ?
           WHERE id = ? AND version = ?`,
        )
        .bind(
          item.weekId,
          item.sourceRef,
          item.sourceVersion,
          item.projectKey,
          item.title,
          item.summary,
          item.lane,
          item.dueAt,
          item.durableOutcome,
          item.corpusTargetSpace,
          item.updatedAt,
          item.id,
          item.version - 1,
        ),
      this.insertEventForItemVersionStatement(
        event,
        item.id,
        item.version,
        item.updatedAt,
      ),
      this.db
        .prepare(
          `INSERT INTO ingest_receipts
            (idempotency_key, source_kind, source_key, source_version, item_id, created_at)
           SELECT ?, ?, ?, ?, ?, ?
           FROM items
           WHERE id = ? AND version = ? AND updated_at = ?`,
        )
        .bind(
          receipt.idempotencyKey,
          receipt.sourceKind,
          receipt.sourceKey,
          receipt.sourceVersion,
          item.id,
          receipt.createdAt,
          item.id,
          item.version,
          item.updatedAt,
        ),
    ]);
    if ((results[0]?.meta.changes ?? 0) !== 1) {
      throw new JournalError(
        "version_conflict",
        "the item changed while the observation was being applied",
        409,
      );
    }
  }

  async setResolution(
    item: ItemRecord,
    previousResolution: Resolution,
    event: EventRow,
  ): Promise<void> {
    const results = await this.db.batch([
      this.db
        .prepare(
          `UPDATE items
           SET resolution = ?, version = version + 1, updated_at = ?
           WHERE id = ? AND version = ? AND resolution = ?`,
        )
        .bind(
          item.resolution,
          item.updatedAt,
          item.id,
          item.version - 1,
          previousResolution,
        ),
      this.insertEventForItemVersionStatement(
        event,
        item.id,
        item.version,
        item.updatedAt,
      ),
    ]);
    if ((results[0]?.meta.changes ?? 0) !== 1) {
      throw new JournalError(
        "version_conflict",
        "the item changed before the resolution was saved",
        409,
      );
    }
  }

  async listEvents(weekId: string, limit = 300): Promise<EventRow[]> {
    const rows = await this.db
      .prepare(
        `SELECT e.*, i.title AS item_title
         FROM journal_events e
         LEFT JOIN items i ON i.id = e.item_id
         WHERE e.week_id = ?
         ORDER BY e.occurred_at ASC, e.id ASC
         LIMIT ?`,
      )
      .bind(weekId, limit)
      .all<EventRow>();
    return rows.results;
  }

  async getClosure(weekId: string): Promise<{
    summary: WeekClosureSummary;
    corpusCandidates: CorpusCandidate[];
    closedBy: string;
    closedAt: string;
  } | null> {
    const row = await this.db
      .prepare("SELECT * FROM week_closures WHERE week_id = ?")
      .bind(weekId)
      .first<ClosureRow>();
    if (!row) return null;
    return {
      summary: JSON.parse(row.summary_json) as WeekClosureSummary,
      corpusCandidates: JSON.parse(
        row.corpus_candidates_json,
      ) as CorpusCandidate[],
      closedBy: row.closed_by,
      closedAt: row.closed_at,
    };
  }

  async closeWeek(
    weekId: string,
    summary: WeekClosureSummary,
    corpusCandidates: CorpusCandidate[],
    closedBy: string,
    closedAt: string,
    event: EventRow,
    rolloverItems: Array<{ item: ItemRecord; event: EventRow }>,
  ): Promise<void> {
    const statements: D1PreparedStatement[] = [
      this.db
        .prepare(
          `UPDATE weeks
           SET status = 'closed', closed_at = ?, updated_at = ?, revision = revision + 1
           WHERE id = ? AND status = 'open'`,
        )
        .bind(closedAt, closedAt, weekId),
      this.db
        .prepare(
          `INSERT INTO week_closures
            (week_id, summary_json, corpus_candidates_json, closed_by, closed_at)
           SELECT ?, ?, ?, ?, ?
           FROM weeks
           WHERE id = ? AND status = 'closed' AND closed_at = ?`,
        )
        .bind(
          weekId,
          JSON.stringify(summary),
          JSON.stringify(corpusCandidates),
          closedBy,
          closedAt,
          weekId,
          closedAt,
        ),
      this.insertEventForClosedWeekStatement(event, weekId, closedAt),
    ];
    for (const rollover of rolloverItems) {
      statements.push(
        this.insertItemStatement(rollover.item, true),
        this.insertEventForItemVersionStatement(
          rollover.event,
          rollover.item.id,
          rollover.item.version,
          rollover.item.updatedAt,
        ),
      );
    }
    const results = await this.db.batch(statements);
    if ((results[0]?.meta.changes ?? 0) !== 1) {
      throw new JournalError(
        "week_already_closed",
        "the week was already closed",
        409,
      );
    }
  }

  async addEvent(event: EventRow): Promise<void> {
    await this.insertEventStatement(event).run();
  }

  async listWeeksOverlapping(
    startsOn: string,
    endsOn: string,
  ): Promise<WeekRecord[]> {
    const rows = await this.db
      .prepare(
        `SELECT * FROM weeks
         WHERE starts_on <= ? AND ends_on >= ?
         ORDER BY starts_on ASC`,
      )
      .bind(endsOn, startsOn)
      .all<WeekRow>();
    return rows.results.map(toWeek);
  }

  async listItemsInWeeks(weekIds: string[]): Promise<ItemRecord[]> {
    if (weekIds.length === 0) return [];
    const placeholders = weekIds.map(() => "?").join(", ");
    const rows = await this.db
      .prepare(
        `SELECT * FROM items
         WHERE week_id IN (${placeholders})
         ORDER BY week_id ASC, updated_at ASC`,
      )
      .bind(...weekIds)
      .all<ItemRow>();
    return rows.results.map(toItem);
  }

  async findPromotionReceipt(input: PromotionReceiptInput): Promise<boolean> {
    const row = await this.db
      .prepare(
        `SELECT 1 AS found FROM corpus_promotion_receipts
         WHERE week_id = ? AND item_id IS ? AND target_space = ? AND content_hash = ?`,
      )
      .bind(input.weekId, input.itemId, input.targetSpace, input.contentHash)
      .first<{ found: number }>();
    return row?.found === 1;
  }

  async insertPromotionReceipt(
    input: PromotionReceiptInput,
    createdAt: string,
    event: EventRow,
  ): Promise<string> {
    const id = crypto.randomUUID();
    await this.db.batch([
      this.db
        .prepare(
          `INSERT INTO corpus_promotion_receipts
            (id, week_id, item_id, target_space, source_path, content_hash, status, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .bind(
          id,
          input.weekId,
          input.itemId,
          input.targetSpace,
          input.sourcePath,
          input.contentHash,
          input.status,
          input.details,
          createdAt,
        ),
      this.insertEventStatement(event),
    ]);
    return id;
  }

  private insertEventStatement(event: EventRow): D1PreparedStatement {
    return this.db
      .prepare(
        `INSERT INTO journal_events (
          id, week_id, item_id, event_type, actor_kind, actor_ref,
          payload_json, idempotency_key, occurred_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .bind(
        event.id,
        event.week_id,
        event.item_id,
        event.event_type,
        event.actor_kind,
        event.actor_ref,
        event.payload_json,
        event.idempotency_key,
        event.occurred_at,
        event.created_at,
      );
  }

  private insertItemStatement(
    item: ItemRecord,
    ignoreConflict = false,
  ): D1PreparedStatement {
    const insert = ignoreConflict ? "INSERT OR IGNORE" : "INSERT";
    return this.db
      .prepare(
        `${insert} INTO items (
          id, logical_item_id, week_id, source_kind, source_key, source_ref, source_version,
          project_key, title, summary, lane, resolution, due_at,
          durable_outcome, corpus_target_space, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .bind(
        item.id,
        item.logicalItemId,
        item.weekId,
        item.sourceKind,
        item.sourceKey,
        item.sourceRef,
        item.sourceVersion,
        item.projectKey,
        item.title,
        item.summary,
        item.lane,
        item.resolution,
        item.dueAt,
        item.durableOutcome,
        item.corpusTargetSpace,
        item.version,
        item.createdAt,
        item.updatedAt,
      );
  }

  private insertEventForItemVersionStatement(
    event: EventRow,
    itemId: string,
    version: number,
    updatedAt: string,
  ): D1PreparedStatement {
    return this.db
      .prepare(
        `INSERT INTO journal_events (
          id, week_id, item_id, event_type, actor_kind, actor_ref,
          payload_json, idempotency_key, occurred_at, created_at
        )
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        FROM items
        WHERE id = ? AND version = ? AND updated_at = ?`,
      )
      .bind(
        event.id,
        event.week_id,
        event.item_id,
        event.event_type,
        event.actor_kind,
        event.actor_ref,
        event.payload_json,
        event.idempotency_key,
        event.occurred_at,
        event.created_at,
        itemId,
        version,
        updatedAt,
      );
  }

  private insertEventForClosedWeekStatement(
    event: EventRow,
    weekId: string,
    closedAt: string,
  ): D1PreparedStatement {
    return this.db
      .prepare(
        `INSERT INTO journal_events (
          id, week_id, item_id, event_type, actor_kind, actor_ref,
          payload_json, idempotency_key, occurred_at, created_at
        )
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        FROM weeks
        WHERE id = ? AND status = 'closed' AND closed_at = ?`,
      )
      .bind(
        event.id,
        event.week_id,
        event.item_id,
        event.event_type,
        event.actor_kind,
        event.actor_ref,
        event.payload_json,
        event.idempotency_key,
        event.occurred_at,
        event.created_at,
        weekId,
        closedAt,
      );
  }
}
