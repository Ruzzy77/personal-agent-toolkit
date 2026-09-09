import { JournalError } from "./errors";
import { JournalRepository, type EventRow } from "./repository";
import {
  addDays,
  currentWeekId,
  kstDate,
  kstEventDate,
  normalizeTimestamp,
  periodRange,
  validateWeekId,
  weekIdForDate,
} from "./time";
import type {
  BoardResult,
  BoardSummary,
  CorpusCandidate,
  CorrectionInput,
  IngestItemInput,
  IngestResult,
  ItemDetailResult,
  ItemRecord,
  ItemSearchInput,
  ItemSearchResult,
  JournalEventRecord,
  Lane,
  PeriodKind,
  PeriodResult,
  PeriodSummaryVersion,
  Principal,
  PromotionReceiptInput,
  Resolution,
  ResolutionInput,
  Responsibility,
  SavePeriodSummaryInput,
  WeekClosePreparation,
  WeekClosure,
  WeekClosureSummary,
  WeekFlowEntry,
} from "./types";

const LANES: Lane[] = ["today", "direct", "waiting", "attention"];
const RESOLUTIONS: Resolution[] = ["active", "held", "completed", "canceled"];

function emptySummary(): BoardSummary {
  return {
    today: 0,
    direct: 0,
    waiting: 0,
    attention: 0,
    completed: 0,
    held: 0,
    canceled: 0,
  };
}

function eventLabel(event: EventRow): string {
  let payload: Record<string, unknown> = {};
  try {
    payload = JSON.parse(event.payload_json) as Record<string, unknown>;
  } catch {
    return event.event_type;
  }
  if (event.event_type === "item_created") return "항목 추가";
  if (event.event_type === "item_rolled_over") return "다음 주 이월";
  if (event.event_type === "observation_updated") {
    return typeof payload.summary === "string" ? payload.summary : "상태 갱신";
  }
  if (event.event_type === "resolution_changed") {
    const resolution = payload.resolution;
    const labels: Record<string, string> = {
      active: "재개",
      held: "보류",
      completed: "완료",
      canceled: "취소",
    };
    return typeof resolution === "string"
      ? (labels[resolution] ?? "처리 상태 변경")
      : "처리 상태 변경";
  }
  if (event.event_type === "week_closed") return "주간 마감";
  if (event.event_type === "correction_added") return "정정 기록";
  if (event.event_type === "corpus_promoted") return "Corpus 반영";
  return event.event_type;
}

function actorRef(principal: Principal): string {
  return `${principal.auth}:${principal.id}`;
}

function defaultResponsibility(lane: Lane): Responsibility {
  return lane === "waiting" ? "counterparty" : "user";
}

function eventRecord(event: EventRow): JournalEventRecord {
  let payload: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(event.payload_json) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      payload = parsed as Record<string, unknown>;
    }
  } catch {
    payload = {};
  }
  return {
    id: event.id,
    weekId: event.week_id,
    itemId: event.item_id,
    eventType: event.event_type,
    actorKind: event.actor_kind,
    actorRef: event.actor_ref,
    payload,
    label: eventLabel(event),
    occurredAt: event.occurred_at,
    createdAt: event.created_at,
  };
}

async function contentHash(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hex = [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  return `sha256:${hex}`;
}

export class JournalService {
  private readonly repository: JournalRepository;

  constructor(
    db: D1Database,
    private readonly clock: () => Date = () => new Date(),
  ) {
    this.repository = new JournalRepository(db);
  }

  async getBoard(weekId: string | null, includeResolved = false): Promise<BoardResult> {
    const now = this.clock();
    const selected = validateWeekId(weekId ?? currentWeekId(now));
    const storedWeek = await this.repository.getWeek(selected);
    const week = storedWeek ?? this.repository.virtualWeek(selected, now.toISOString());
    const allItems =
      selected === currentWeekId(now) && week.status === "open"
        ? await this.repository.listCurrentItems(selected)
        : storedWeek
          ? await this.repository.listAllItems(selected)
          : [];
    const items = includeResolved
      ? allItems
      : allItems.filter((item) => ["active", "held"].includes(item.resolution));
    const summary = emptySummary();
    for (const item of allItems) {
      if (item.resolution === "active") summary[item.lane] += 1;
      if (item.resolution === "held") summary.held += 1;
      if (item.resolution === "completed") summary.completed += 1;
      if (item.resolution === "canceled") summary.canceled += 1;
    }
    const events = storedWeek ? await this.repository.listEvents(selected) : [];
    const groups = new Map<string, WeekFlowEntry["events"]>();
    for (const event of events) {
      const date = kstEventDate(event.occurred_at);
      const group = groups.get(date) ?? [];
      group.push({
        eventType: event.event_type,
        itemId: event.item_id,
        title: event.item_title ?? null,
        label: eventLabel(event),
        occurredAt: event.occurred_at,
      });
      groups.set(date, group);
    }
    const flow = [...groups.entries()].map(([date, entries]) => ({
      date,
      events: entries,
    }));
    const frozen = week.status === "closed" ? await this.repository.getClosure(selected) : null;
    const receipts = frozen ? await this.repository.listPromotionReceipts(selected) : [];
    const closure: BoardResult["closure"] = frozen ? {
      summary: frozen.summary,
      corpusCandidates: frozen.corpusCandidates.map((candidate) => {
        const attempts = receipts.filter((receipt) =>
          receipt.item_id === candidate.itemId &&
          receipt.target_space === candidate.targetSpace &&
          receipt.content_hash === candidate.contentHash
        );
        const completed = attempts.find((receipt) => receipt.status !== "failed");
        return {
          ...candidate,
          reflectionStatus: completed?.status ?? (attempts.length ? "failed" : "pending"),
        };
      }),
      corrections: events.filter((event) => event.event_type === "correction_added").map(eventRecord),
    } : null;
    return { week, summary, items, flow, closure };
  }

  private continuation(source: ItemRecord, weekId: string, now: string): ItemRecord {
    return {
      ...source,
      id: crypto.randomUUID(),
      weekId,
      version: 1,
      durableOutcome: null,
      corpusTargetSpace: null,
      createdAt: now,
      updatedAt: now,
    };
  }

  private continuationEvent(source: ItemRecord, item: ItemRecord, principal: Principal): EventRow {
    return this.event({
      weekId: item.weekId,
      itemId: item.id,
      eventType: "item_rolled_over",
      principal,
      payload: {
        fromWeekId: source.weekId,
        fromItemId: source.id,
        logicalItemId: source.logicalItemId,
        previousResolution: source.resolution,
        resolution: source.resolution,
      },
      idempotencyKey: `continue:${item.weekId}:${source.id}`,
      occurredAt: item.createdAt,
      createdAt: item.createdAt,
    });
  }

  async findItems(input: ItemSearchInput): Promise<ItemSearchResult> {
    if ((input.startsOn && !input.endsOn) || (!input.startsOn && input.endsOn)) {
      throw new JournalError("invalid_request", "startsOn and endsOn must be provided together");
    }
    if (input.weekId) validateWeekId(input.weekId);
    if (input.startsOn && input.endsOn && input.startsOn > input.endsOn) {
      throw new JournalError("invalid_request", "startsOn must not be after endsOn");
    }
    if (input.weekId === currentWeekId(this.clock())) {
      const board = await this.getBoard(input.weekId, true);
      const query = input.query?.toLocaleLowerCase();
      const matching = board.items.filter(
        (item) =>
          (!input.projectKey || item.projectKey === input.projectKey) &&
          (!input.lane || item.lane === input.lane) &&
          (!input.resolution || item.resolution === input.resolution) &&
          (!query ||
            [item.title, item.summary, item.projectKey, item.sourceRef].some((value) =>
              value?.toLocaleLowerCase().includes(query),
            )),
      );
      return { items: matching.slice(0, input.limit), count: matching.length };
    }
    return this.repository.findItems(input);
  }

  async getItemDetail(itemId: string): Promise<ItemDetailResult> {
    const item = await this.repository.getItem(itemId);
    if (!item) {
      throw new JournalError("item_not_found", "item was not found", 404);
    }
    const [relatedItems, history, corrections] = await Promise.all([
      this.repository.listItemsByLogicalId(item.logicalItemId),
      this.repository.listEventsByLogicalId(item.logicalItemId),
      this.repository.listCorrectionsForLogicalItem(item.logicalItemId),
    ]);
    return {
      item,
      relatedItems,
      history: history.map(eventRecord),
      corrections: corrections.map(eventRecord),
    };
  }

  async ingestItems(inputs: IngestItemInput[], principal: Principal): Promise<IngestResult[]> {
    const results: IngestResult[] = [];
    for (const input of inputs) {
      results.push(await this.ingestOne(input, principal));
    }
    return results;
  }

  private async ingestOne(input: IngestItemInput, principal: Principal): Promise<IngestResult> {
    const now = this.clock();
    const nowIso = now.toISOString();
    const occurredAt = normalizeTimestamp(input.occurredAt, now);
    const receipt = await this.repository.getReceipt(input.idempotencyKey);
    if (receipt) {
      const duplicateItem = await this.repository.getItem(receipt.item_id);
      if (!duplicateItem) {
        throw new JournalError("storage_error", "ingest receipt points to a missing item", 500);
      }
      return { item: duplicateItem, created: false, duplicate: true };
    }

    const derivedDate = input.dueAt ? kstEventDate(input.dueAt) : kstEventDate(occurredAt);
    const latest = await this.repository.getLatestItemBySource(input.sourceKind, input.sourceKey);
    const current = currentWeekId(now);
    let weekId = validateWeekId(
      input.weekId ?? (weekIdForDate(derivedDate) > current ? weekIdForDate(derivedDate) : current),
    );
    if (weekId < current && latest && ["active", "held"].includes(latest.resolution))
      weekId = current;
    const week = await this.repository.ensureWeek(weekId, nowIso);
    if (week.status === "closed") {
      throw new JournalError(
        "week_closed",
        "closed weeks accept correction events, not item changes",
        409,
      );
    }

    const existing = await this.repository.getItemBySource(
      weekId,
      input.sourceKind,
      input.sourceKey,
    );

    if (!existing) {
      const previous = await this.repository.getLatestItemBySource(
        input.sourceKind,
        input.sourceKey,
      );
      const itemId = crypto.randomUUID();
      const item: ItemRecord = {
        id: itemId,
        logicalItemId: previous?.logicalItemId ?? itemId,
        weekId,
        sourceKind: input.sourceKind,
        sourceKey: input.sourceKey,
        sourceRef: input.sourceRef,
        sourceVersion: input.sourceVersion,
        projectKey: input.projectKey,
        title: input.title,
        summary: input.summary,
        lane: input.lane,
        resolution: previous?.resolution ?? "active",
        responsibility:
          input.responsibility ?? previous?.responsibility ?? defaultResponsibility(input.lane),
        dueAt: input.dueAt,
        durableOutcome: input.durableOutcome,
        corpusTargetSpace: input.corpusTargetSpace,
        version: 1,
        createdAt: nowIso,
        updatedAt: nowIso,
      };
      const event = this.event({
        weekId,
        itemId: item.id,
        eventType: "item_created",
        principal,
        payload: {
          title: item.title,
          summary: item.summary,
          lane: item.lane,
          responsibility: item.responsibility,
          sourceRef: item.sourceRef,
        },
        idempotencyKey: input.idempotencyKey,
        occurredAt,
        createdAt: nowIso,
      });
      if (previous && previous.weekId < weekId) {
        await this.repository.continueItem(
          previous,
          item,
          event,
          this.continuationEvent(previous, item, principal),
          input.idempotencyKey,
        );
      } else {
        await this.repository.insertItem(item, event, {
          idempotencyKey: input.idempotencyKey,
          sourceKind: input.sourceKind,
          sourceKey: input.sourceKey,
          sourceVersion: input.sourceVersion,
          createdAt: nowIso,
        });
      }
      return { item, created: true, duplicate: false };
    }

    const updated: ItemRecord = {
      ...existing,
      weekId,
      sourceRef: input.sourceRef,
      sourceVersion: input.sourceVersion,
      projectKey: input.projectKey,
      title: input.title,
      summary: input.summary,
      lane: input.lane,
      responsibility: input.responsibility ?? existing.responsibility,
      dueAt: input.dueAt,
      durableOutcome: input.durableOutcome,
      corpusTargetSpace: input.corpusTargetSpace,
      version: existing.version + 1,
      updatedAt: nowIso,
    };
    const event = this.event({
      weekId,
      itemId: updated.id,
      eventType: "observation_updated",
      principal,
      payload: {
        title: updated.title,
        summary: updated.summary,
        lane: updated.lane,
        responsibility: updated.responsibility,
        sourceRef: updated.sourceRef,
        resolutionUnchanged: updated.resolution,
      },
      idempotencyKey: input.idempotencyKey,
      occurredAt,
      createdAt: nowIso,
    });
    await this.repository.updateItemObservation(updated, event, {
      idempotencyKey: input.idempotencyKey,
      sourceKind: input.sourceKind,
      sourceKey: input.sourceKey,
      sourceVersion: input.sourceVersion,
      createdAt: nowIso,
    });
    return { item: updated, created: false, duplicate: false };
  }

  async setResolution(
    itemId: string,
    input: ResolutionInput,
    principal: Principal,
  ): Promise<{ item: ItemRecord; duplicate: boolean }> {
    if (principal.kind !== "owner") {
      throw new JournalError(
        "owner_confirmation_required",
        "resolution changes require owner confirmation",
        403,
      );
    }
    if (await this.repository.eventExists(input.idempotencyKey)) {
      const duplicate = await this.repository.getEventItem(input.idempotencyKey);
      if (!duplicate) {
        throw new JournalError("item_not_found", "item was not found", 404);
      }
      return { item: duplicate, duplicate: true };
    }
    const existing = await this.repository.getItem(itemId);
    if (!existing) {
      throw new JournalError("item_not_found", "item was not found", 404);
    }
    const current = currentWeekId(this.clock());
    const latest = await this.repository.getLatestItemBySource(
      existing.sourceKind,
      existing.sourceKey,
    );
    if (latest && latest.weekId > existing.weekId) {
      throw new JournalError(
        "version_conflict",
        "this item has a newer weekly instance; read the current board again",
        409,
      );
    }
    const continues = existing.weekId < current && ["active", "held"].includes(existing.resolution);
    const week = await this.repository.getWeek(existing.weekId);
    if (!week || (week.status === "closed" && !continues)) {
      throw new JournalError("week_closed", "items in a closed week cannot be changed", 409);
    }
    if (input.expectedVersion !== null && input.expectedVersion !== existing.version) {
      throw new JournalError("version_conflict", "the item changed after it was shown", 409, {
        currentVersion: existing.version,
      });
    }
    if (existing.resolution === input.resolution) {
      return { item: existing, duplicate: true };
    }
    const now = this.clock();
    const nowIso = now.toISOString();
    const base = continues ? this.continuation(existing, current, nowIso) : existing;
    const updated: ItemRecord = {
      ...base,
      resolution: input.resolution,
      version: continues ? 1 : existing.version + 1,
      updatedAt: nowIso,
    };
    const event = this.event({
      weekId: updated.weekId,
      itemId: updated.id,
      eventType: "resolution_changed",
      principal,
      payload: {
        previousResolution: existing.resolution,
        resolution: input.resolution,
      },
      idempotencyKey: input.idempotencyKey,
      occurredAt: normalizeTimestamp(input.occurredAt, now),
      createdAt: nowIso,
    });
    if (continues) {
      await this.repository.ensureWeek(current, nowIso);
      await this.repository.continueItem(
        existing,
        updated,
        event,
        this.continuationEvent(existing, updated, principal),
      );
    } else {
      await this.repository.setResolution(updated, existing.resolution, event);
    }
    return { item: updated, duplicate: false };
  }

  async prepareWeekClose(
    weekId: string | null,
    principal: Principal,
  ): Promise<WeekClosePreparation> {
    const { preparation } = await this.prepareWeekCloseSnapshot(weekId, principal);
    return preparation;
  }

  private async prepareWeekCloseSnapshot(
    weekId: string | null,
    principal: Principal,
  ): Promise<{ preparation: WeekClosePreparation; items: ItemRecord[] }> {
    if (principal.kind !== "owner") {
      throw new JournalError(
        "owner_confirmation_required",
        "preparing a week close requires owner confirmation",
        403,
      );
    }
    const now = this.clock();
    const nowIso = now.toISOString();
    const selected = validateWeekId(weekId ?? currentWeekId(now));
    const storedWeek = await this.repository.getWeek(selected);
    const week = storedWeek ?? this.repository.virtualWeek(selected, nowIso);
    if (week.status === "closed") {
      throw new JournalError("week_already_closed", "the week is already closed", 409);
    }
    const items = storedWeek ? await this.repository.listAllItems(selected) : [];
    const rolloverSources: ItemRecord[] = [];
    for (const item of items) {
      if (!["active", "held"].includes(item.resolution)) continue;
      const latest = await this.repository.getLatestItemBySource(item.sourceKind, item.sourceKey);
      if (latest?.id === item.id) rolloverSources.push(item);
    }
    const nextWeekId = addDays(selected, 7);
    const nextWeek = await this.repository.getWeek(nextWeekId);
    if (rolloverSources.length > 0 && nextWeek?.status === "closed") {
      throw new JournalError(
        "rollover_week_closed",
        "the next week is already closed and cannot accept rollover items",
        409,
      );
    }
    const nextWeekPresence: Array<{ itemId: string; present: boolean }> = [];
    if (rolloverSources.length > 0) {
      for (const source of rolloverSources) {
        const alreadyPresent = await this.repository.getItemBySource(
          nextWeekId,
          source.sourceKind,
          source.sourceKey,
        );
        nextWeekPresence.push({
          itemId: source.id,
          present: Boolean(alreadyPresent),
        });
      }
    }
    const summary = this.closureSummary(selected, items, rolloverSources);
    const candidateItems = items.filter(
      (item) => item.projectKey && item.durableOutcome && item.corpusTargetSpace,
    );
    const corpusCandidates = await Promise.all(
      candidateItems.map<Promise<CorpusCandidate>>(async (item) => ({
        itemId: item.id,
        projectKey: item.projectKey ?? "",
        targetSpace: item.corpusTargetSpace ?? "",
        durableOutcome: item.durableOutcome ?? "",
        contentHash: await contentHash(item.durableOutcome ?? ""),
        sourceRef: item.sourceRef,
      })),
    );
    const receipts = storedWeek
      ? await this.repository.listPromotionReceipts(selected)
      : [];
    const reflectedCandidateIds = corpusCandidates
      .filter((candidate) =>
        receipts.some(
          (receipt) =>
            receipt.item_id === candidate.itemId &&
            receipt.target_space === candidate.targetSpace &&
            receipt.content_hash === candidate.contentHash &&
            ["applied", "skipped"].includes(receipt.status),
        ),
      )
      .map((candidate) => candidate.itemId);
    const preparationVersion = await contentHash(
      JSON.stringify({
        weekId: selected,
        weekRevision: week.revision,
        items: items
          .map((item) => ({ id: item.id, version: item.version }))
          .sort((left, right) => left.id.localeCompare(right.id)),
        nextWeek: nextWeek
          ? { id: nextWeek.id, status: nextWeek.status, revision: nextWeek.revision }
          : null,
        nextWeekPresence: nextWeekPresence.sort((left, right) =>
          left.itemId.localeCompare(right.itemId),
        ),
      }),
    );
    return {
      preparation: {
        week,
        summary,
        corpusCandidates,
        rolloverItems: rolloverSources.map((item) => ({
          itemId: item.id,
          title: item.title,
          resolution: item.resolution,
        })),
        preparationVersion,
        reflectedCandidateIds,
      },
      items,
    };
  }

  async confirmWeekClose(
    weekId: string | null,
    preparationVersion: string,
    idempotencyKey: string,
    occurredAtInput: string | null,
    principal: Principal,
  ): Promise<WeekClosure> {
    if (principal.kind !== "owner") {
      throw new JournalError(
        "owner_confirmation_required",
        "closing a week requires owner confirmation",
        403,
      );
    }
    const now = this.clock();
    const nowIso = now.toISOString();
    const occurredAt = normalizeTimestamp(occurredAtInput, now);
    const selected = validateWeekId(weekId ?? currentWeekId(now));
    const existingClosure = await this.repository.getClosure(selected);
    if (existingClosure) {
      const closedWeek = await this.repository.getWeek(selected);
      if (!closedWeek) {
        throw new JournalError("storage_error", "closed week was not found", 500);
      }
      return {
        week: closedWeek,
        summary: existingClosure.summary,
        corpusCandidates: existingClosure.corpusCandidates,
        alreadyClosed: true,
      };
    }
    const { preparation, items } = await this.prepareWeekCloseSnapshot(selected, principal);
    if (preparation.preparationVersion !== preparationVersion) {
      throw new JournalError(
        "close_preparation_stale",
        "the week changed after the close preparation",
        409,
        { preparationVersion: preparation.preparationVersion },
      );
    }
    const week = await this.repository.ensureWeek(selected, nowIso);
    if (week.status === "closed") {
      throw new JournalError("week_already_closed", "the week is already closed", 409);
    }
    const rolloverSources: ItemRecord[] = [];
    for (const item of items) {
      if (!["active", "held"].includes(item.resolution)) continue;
      const latest = await this.repository.getLatestItemBySource(item.sourceKind, item.sourceKey);
      if (latest?.id === item.id) rolloverSources.push(item);
    }
    const rolloverItems: Array<{ item: ItemRecord; event: EventRow }> = [];
    if (rolloverSources.length > 0) {
      const nextWeekId = addDays(selected, 7);
      const nextWeek = await this.repository.ensureWeek(nextWeekId, nowIso);
      if (nextWeek.status === "closed") {
        throw new JournalError(
          "rollover_week_closed",
          "the next week is already closed and cannot accept rollover items",
          409,
        );
      }
      for (const source of rolloverSources) {
        const alreadyPresent = await this.repository.getItemBySource(
          nextWeekId,
          source.sourceKind,
          source.sourceKey,
        );
        if (alreadyPresent) continue;
        const rollover: ItemRecord = {
          ...source,
          id: crypto.randomUUID(),
          weekId: nextWeekId,
          resolution: source.resolution,
          durableOutcome: null,
          corpusTargetSpace: null,
          version: 1,
          createdAt: nowIso,
          updatedAt: nowIso,
        };
        const rolloverEvent = this.event({
          weekId: nextWeekId,
          itemId: rollover.id,
          eventType: "item_rolled_over",
          principal,
          payload: {
            fromWeekId: selected,
            fromItemId: source.id,
            logicalItemId: source.logicalItemId,
            previousResolution: source.resolution,
            resolution: rollover.resolution,
          },
          idempotencyKey: `rollover:${selected}:${source.id}`,
          occurredAt,
          createdAt: nowIso,
        });
        rolloverItems.push({ item: rollover, event: rolloverEvent });
      }
    }
    const summary = preparation.summary;
    const corpusCandidates = preparation.corpusCandidates;
    const event = this.event({
      weekId: selected,
      itemId: null,
      eventType: "week_closed",
      principal,
      payload: {
        summary,
        corpusCandidateCount: corpusCandidates.length,
        rolloverCount: rolloverSources.length,
      },
      idempotencyKey,
      occurredAt,
      createdAt: nowIso,
    });
    await this.repository.closeWeek(
      selected,
      summary,
      corpusCandidates,
      actorRef(principal),
      occurredAt,
      event,
      rolloverItems,
      items.map((item) => ({ id: item.id, version: item.version })),
    );
    const closedWeek = await this.repository.getWeek(selected);
    if (!closedWeek) {
      throw new JournalError("storage_error", "closed week was not found", 500);
    }
    return {
      week: closedWeek,
      summary,
      corpusCandidates,
      alreadyClosed: false,
    };
  }

  async addCorrection(
    weekId: string,
    input: CorrectionInput,
    principal: Principal,
  ): Promise<{ eventId: string; duplicate: boolean }> {
    if (principal.kind !== "owner") {
      throw new JournalError(
        "owner_confirmation_required",
        "closed-week corrections require owner confirmation",
        403,
      );
    }
    const selected = validateWeekId(weekId);
    const week = await this.repository.getWeek(selected);
    if (!week || week.status !== "closed") {
      throw new JournalError("week_not_closed", "corrections are only for closed weeks", 409);
    }
    if (await this.repository.eventExists(input.idempotencyKey)) {
      return { eventId: input.idempotencyKey, duplicate: true };
    }
    if (input.itemId) {
      const item = await this.repository.getItem(input.itemId);
      if (!item || item.weekId !== selected) {
        throw new JournalError(
          "item_not_found",
          "the correction item was not found in this week",
          404,
        );
      }
    }
    const now = this.clock();
    const event = this.event({
      weekId: selected,
      itemId: input.itemId,
      eventType: "correction_added",
      principal,
      payload: {
        itemId: input.itemId,
        note: input.note,
        sourceRef: input.sourceRef,
      },
      idempotencyKey: input.idempotencyKey,
      occurredAt: normalizeTimestamp(input.occurredAt, now),
      createdAt: now.toISOString(),
    });
    await this.repository.addEvent(event);
    return { eventId: event.id, duplicate: false };
  }

  async getPeriod(kind: PeriodKind, anchorInput: string | null): Promise<PeriodResult> {
    const anchor = anchorInput ?? kstDate(this.clock());
    const { startsOn, endsOn } = periodRange(kind, anchor);
    const weeks = await this.repository.listWeeksOverlapping(startsOn, endsOn);
    const current = currentWeekId(this.clock());
    const currentStored = weeks.find((week) => week.id === current);
    const items =
      kind === "week" && startsOn === current && currentStored?.status !== "closed"
        ? await this.repository.listCurrentItems(current)
        : await this.repository.listItemsInWeeks(weeks.map((week) => week.id));
    const summaryVersions = await this.repository.listPeriodSummaries(kind, anchor);
    const totals = Object.fromEntries(RESOLUTIONS.map((resolution) => [resolution, 0])) as Record<
      Resolution,
      number
    >;
    const lanes = Object.fromEntries(LANES.map((lane) => [lane, 0])) as Record<Lane, number>;
    const projects = new Map<
      string,
      { projectKey: string; total: number; completed: number; active: number }
    >();
    for (const item of items) {
      totals[item.resolution] += 1;
      lanes[item.lane] += 1;
      const key = item.projectKey ?? "미분류";
      const project = projects.get(key) ?? {
        projectKey: key,
        total: 0,
        completed: 0,
        active: 0,
      };
      project.total += 1;
      if (item.resolution === "completed") project.completed += 1;
      if (["active", "held"].includes(item.resolution)) project.active += 1;
      projects.set(key, project);
    }
    const logicalItems = new Map<
      string,
      { instances: ItemRecord[]; latest: ItemRecord }
    >();
    for (const item of items) {
      const existing = logicalItems.get(item.logicalItemId);
      if (!existing) {
        logicalItems.set(item.logicalItemId, { instances: [item], latest: item });
        continue;
      }
      existing.instances.push(item);
      if (
        item.weekId > existing.latest.weekId ||
        (item.weekId === existing.latest.weekId &&
          item.updatedAt > existing.latest.updatedAt)
      ) {
        existing.latest = item;
      }
    }
    return {
      kind,
      anchor,
      startsOn,
      endsOn,
      weeks,
      totals,
      lanes,
      projects: [...projects.values()].sort((left, right) => right.total - left.total),
      highlights: items
        .filter((item) => item.resolution === "completed" || item.durableOutcome)
        .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
        .slice(0, 20)
        .map((item) => ({
          itemId: item.id,
          weekId: item.weekId,
          title: item.title,
          projectKey: item.projectKey,
          resolution: item.resolution,
          durableOutcome: item.durableOutcome,
        })),
      longRunning: [...logicalItems.entries()]
        .filter(
          ([, value]) =>
            value.instances.length >= 2 &&
            ["active", "held"].includes(value.latest.resolution),
        )
        .map(([logicalItemId, value]) => ({
          logicalItemId,
          title: value.latest.title,
          projectKey: value.latest.projectKey,
          weekCount: new Set(value.instances.map((item) => item.weekId)).size,
          latestWeekId: value.latest.weekId,
          resolution: value.latest.resolution,
        }))
        .sort((left, right) => right.weekCount - left.weekCount),
      currentSummary: summaryVersions.at(-1) ?? null,
      summaryVersions,
    };
  }

  async savePeriodSummary(
    input: SavePeriodSummaryInput,
    principal: Principal,
  ): Promise<{ summary: PeriodSummaryVersion; duplicate: boolean }> {
    if (principal.kind !== "owner") {
      throw new JournalError(
        "owner_confirmation_required",
        "saving a period summary requires owner confirmation",
        403,
      );
    }
    const duplicate = await this.repository.getPeriodSummaryByIdempotency(input.idempotencyKey);
    if (duplicate) return { summary: duplicate, duplicate: true };

    const { startsOn, endsOn } = periodRange(input.kind, input.anchor);
    const versions = await this.repository.listPeriodSummaries(input.kind, input.anchor);
    const currentVersion = versions.at(-1)?.version ?? 0;
    if (
      input.expectedVersion !== null &&
      input.expectedVersion !== currentVersion
    ) {
      throw new JournalError(
        "version_conflict",
        "the period summary changed after it was shown",
        409,
        { currentVersion },
      );
    }
    const weeks = await this.repository.listWeeksOverlapping(startsOn, endsOn);
    const events = await this.repository.listEventsInWeeks(weeks.map((week) => week.id));
    const now = this.clock().toISOString();
    const summary: PeriodSummaryVersion = {
      id: crypto.randomUUID(),
      kind: input.kind,
      anchor: input.anchor,
      startsOn,
      endsOn,
      body: input.body,
      version: currentVersion + 1,
      sourceEventIds: events
        .filter((event) => {
          const date = kstEventDate(event.occurred_at);
          return startsOn <= date && date <= endsOn;
        })
        .map((event) => event.id),
      createdBy: actorRef(principal),
      createdAt: now,
    };
    await this.repository.insertPeriodSummary(summary, input.idempotencyKey, currentVersion);
    return { summary, duplicate: false };
  }

  async recordPromotion(
    input: PromotionReceiptInput,
    principal: Principal,
  ): Promise<{ receiptId: string; duplicate: boolean }> {
    validateWeekId(input.weekId);
    const week = await this.repository.getWeek(input.weekId);
    if (!week) {
      throw new JournalError("week_not_found", "week was not found", 404);
    }
    const replayId = await this.repository.findPromotionReplay(input);
    if (replayId) return { receiptId: replayId, duplicate: true };
    const completedId = await this.repository.findCompletedPromotionReceipt(input);
    if (completedId) {
      return { receiptId: completedId, duplicate: true };
    }
    const expectedItemVersion = await this.validatePromotionCandidate(input, week.status);
    const now = this.clock();
    const receiptId = crypto.randomUUID();
    const event = this.event({
      weekId: input.weekId,
      itemId: input.itemId,
      eventType: "corpus_promoted",
      principal,
      payload: {
        receiptId,
        targetSpace: input.targetSpace,
        sourcePath: input.sourcePath,
        contentHash: input.contentHash,
        status: input.status,
        details: input.details,
      },
      idempotencyKey: input.idempotencyKey,
      occurredAt: normalizeTimestamp(input.occurredAt, now),
      createdAt: now.toISOString(),
    });
    const inserted = await this.repository.insertPromotionReceipt(
      receiptId,
      input,
      now.toISOString(),
      event,
      expectedItemVersion,
    );
    if (inserted) return { receiptId, duplicate: false };

    const concurrentReplayId = await this.repository.findPromotionReplay(input);
    if (concurrentReplayId) return { receiptId: concurrentReplayId, duplicate: true };
    const concurrentCompletedId = await this.repository.findCompletedPromotionReceipt(input);
    if (concurrentCompletedId) {
      return { receiptId: concurrentCompletedId, duplicate: true };
    }
    const currentWeek = await this.repository.getWeek(input.weekId);
    if (!currentWeek) {
      throw new JournalError("week_not_found", "week was not found", 404);
    }
    const currentVersion = await this.validatePromotionCandidate(input, currentWeek.status);
    if (currentVersion !== null && currentVersion !== expectedItemVersion) {
      throw new JournalError(
        "version_conflict",
        "the item changed before its Corpus reflection could be recorded",
        409,
        { currentVersion },
      );
    }
    throw new JournalError("storage_error", "Corpus reflection could not be recorded", 500);
  }

  private async validatePromotionCandidate(
    input: PromotionReceiptInput,
    status: "open" | "closed",
  ): Promise<number | null> {
    if (status === "closed") {
      const closure = await this.repository.getClosure(input.weekId);
      if (!closure) {
        throw new JournalError("storage_error", "the frozen week closure was not found", 500);
      }
      if (!closure.corpusCandidates.some((candidate) =>
        candidate.itemId === input.itemId &&
        candidate.targetSpace === input.targetSpace &&
        candidate.contentHash === input.contentHash
      )) {
        throw new JournalError(
          "promotion_candidate_mismatch",
          "the Corpus reflection receipt does not match a frozen week candidate",
          409,
        );
      }
      return null;
    }
    // Retain item-less receipts for open weeks; closed weeks require a frozen candidate.
    if (!input.itemId) return null;
    const item = await this.repository.getItem(input.itemId);
    if (!item || item.weekId !== input.weekId) {
      throw new JournalError("item_not_found", "item was not found", 404);
    }
    const expectedHash = item.durableOutcome ? await contentHash(item.durableOutcome) : null;
    if (item.corpusTargetSpace !== input.targetSpace || expectedHash !== input.contentHash) {
      throw new JournalError(
        "promotion_candidate_mismatch",
        "the Corpus reflection receipt does not match the current item",
        409,
      );
    }
    return item.version;
  }

  private closureSummary(
    weekId: string,
    items: ItemRecord[],
    rolloverItems: ItemRecord[],
  ): WeekClosureSummary {
    const counts = Object.fromEntries(RESOLUTIONS.map((resolution) => [resolution, 0])) as Record<
      Resolution,
      number
    >;
    const laneCounts = Object.fromEntries(LANES.map((lane) => [lane, 0])) as Record<Lane, number>;
    const projectCounts = new Map<string, number>();
    for (const item of items) {
      counts[item.resolution] += 1;
      laneCounts[item.lane] += 1;
      const key = item.projectKey ?? "미분류";
      projectCounts.set(key, (projectCounts.get(key) ?? 0) + 1);
    }
    return {
      weekId,
      counts,
      laneCounts,
      projectCounts: [...projectCounts.entries()]
        .map(([projectKey, count]) => ({ projectKey, count }))
        .sort((left, right) => right.count - left.count),
      completedTitles: items
        .filter((item) => item.resolution === "completed")
        .map((item) => item.title),
      rolloverCount: rolloverItems.length,
      rolloverTitles: rolloverItems.map((item) => item.title),
    };
  }

  private event(input: {
    weekId: string;
    itemId: string | null;
    eventType: EventRow["event_type"];
    principal: Principal;
    payload: Record<string, unknown>;
    idempotencyKey: string;
    occurredAt: string;
    createdAt: string;
  }): EventRow {
    return {
      id: crypto.randomUUID(),
      week_id: input.weekId,
      item_id: input.itemId,
      event_type: input.eventType,
      actor_kind: input.principal.kind,
      actor_ref: actorRef(input.principal),
      payload_json: JSON.stringify(input.payload),
      idempotency_key: input.idempotencyKey,
      occurred_at: input.occurredAt,
      created_at: input.createdAt,
    };
  }
}
