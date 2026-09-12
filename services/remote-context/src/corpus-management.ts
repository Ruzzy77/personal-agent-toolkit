import {
  purgeAfter,
  type PurgeBlocker,
  type TrashState,
} from "@personal-agent/remote-runtime";
import { canonicalJson, contentSha256, nowIso } from "./canonical";
import {
  CorpusDocumentsService,
  type StoredSourceRef,
} from "./corpus-documents";
import { ContextError } from "./errors";
import {
  activeSpaceGuard,
  guard,
  guardedBatch,
  managementSchemaReady,
  sourceBindingsGuard,
} from "./management-db";
import * as schemas from "./corpus-management-schemas";
import type { Env, Principal } from "./types";

type Kind = "document" | "context_item" | "context_skill" | "space";
type Target = { kind: Kind; space_id: string; id: string };
type Member = {
  kind: Kind;
  id: string;
  space_id: string;
  locator: string;
  version: string;
  previous_state: string;
};
type Group = {
  deletion_group_id: string;
  root_kind: Kind;
  root_id: string;
  space_id: string;
  state: TrashState;
  version: number;
  trashed_at: string;
  purge_after: string;
  updated_at: string;
  blockers_json: string;
};
type Space = {
  space_id: string;
  state: string;
  version: number;
  trash_group_id: string | null;
};
const groupColumns =
  "deletion_group_id,root_kind,root_id,space_id,state,version,trashed_at,purge_after,updated_at,blockers_json";

/** Corpus-owned lifecycle data. Source bytes, registrations and other products are never cascaded. */
export class CorpusManagementService {
  private readonly db: D1Database;
  private readonly owner: string;
  constructor(
    private readonly env: Env,
    private readonly principal: Principal,
    private readonly clock = nowIso,
  ) {
    this.db = env.STATE_DB;
    this.owner = principal.ownerId;
  }

  private async ready(write = false) {
    if (!this.principal.scopes.has(write ? "corpus.write" : "corpus.read"))
      throw new ContextError(
        "insufficient_scope",
        "The required Corpus scope is missing",
        403,
      );
    if (
      !(await managementSchemaReady(this.db)) ||
      (write && this.env.CORPUS_MANAGEMENT_WRITE_ENABLED !== "true")
    ) {
      throw new ContextError(
        "management_not_enabled",
        "Management writes are not enabled for this deployment",
        503,
      );
    }
  }

  private async space(id: string, allowInactive = false): Promise<Space> {
    const row = await this.db
      .prepare(
        `SELECT s.space_id,s.state,s.trash_group_id,c.version FROM corpus_spaces s JOIN corpus_contexts c
      ON c.owner_id=s.owner_id AND c.space_id=s.space_id WHERE s.owner_id=? AND s.space_id=? AND s.access_scope='remote_allowed'`,
      )
      .bind(this.owner, id)
      .first<Space>();
    if (!row)
      throw new ContextError("space_not_found", "Space does not exist", 404);
    if (!allowInactive && (row.state !== "active" || row.trash_group_id))
      throw new ContextError(
        "space_inactive",
        "The destination must be an active Space",
        409,
      );
    return row;
  }

  private async replay(key: string, operation: string, input: unknown) {
    const fingerprint = await contentSha256(input);
    const row = await this.db
      .prepare(
        "SELECT operation,fingerprint,result_json FROM corpus_operation_receipts WHERE owner_id=? AND request_key=?",
      )
      .bind(this.owner, key)
      .first<{ operation: string; fingerprint: string; result_json: string }>();
    if (row && (row.operation !== operation || row.fingerprint !== fingerprint))
      throw new ContextError(
        "request_key_conflict",
        "This request key was used for a different operation",
        409,
      );
    if (row)
      await new CorpusDocumentsService(
        this.env,
        this.principal,
      ).releaseCommittedManagementPins(key);
    return {
      fingerprint,
      result: row
        ? (JSON.parse(row.result_json) as Record<string, unknown>)
        : null,
    };
  }

  private receipt(
    key: string,
    operation: string,
    fingerprint: string,
    result: Record<string, unknown>,
  ) {
    return this.db
      .prepare(
        `INSERT INTO corpus_operation_receipts(owner_id,request_key,operation,fingerprint,result_json,committed_at) VALUES(?,?,?,?,?,?)`,
      )
      .bind(
        this.owner,
        key,
        operation,
        fingerprint,
        canonicalJson(result),
        this.clock(),
      );
  }

  private managedOwner() {
    return this.db
      .prepare(
        "INSERT OR IGNORE INTO corpus_management_owners(owner_id) VALUES(?)",
      )
      .bind(this.owner);
  }
  private bumpSpace(space: string) {
    return this.db
      .prepare(
        "UPDATE corpus_contexts SET version=version+1,updated_at=? WHERE owner_id=? AND space_id=?",
      )
      .bind(this.clock(), this.owner, space);
  }

  async operationStatus(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const input = schemas.corpusOperationStatusSchema.parse(raw);
    const row = await this.db
      .prepare(
        "SELECT operation,result_json,committed_at FROM corpus_operation_receipts WHERE owner_id=? AND request_key=?",
      )
      .bind(this.owner, input.idempotency_key)
      .first<{
        operation: string;
        result_json: string;
        committed_at: string;
      }>();
    const pins = await this.db
      .prepare(
        "SELECT reservation_id FROM corpus_operation_source_pins WHERE owner_id=? AND request_key=?",
      )
      .bind(this.owner, input.idempotency_key)
      .all<{ reservation_id: string }>();
    const warnings = pins.results.map((pin) => ({
      code: row
        ? "source_protection_release_pending"
        : "source_protection_outcome_pending",
      reservation_id: pin.reservation_id,
    }));
    return row
      ? {
          found: true,
          operation: row.operation,
          result: JSON.parse(row.result_json),
          committed_at: row.committed_at,
          warnings,
        }
      : {
          found: false,
          state: "not_confirmed",
          message:
            "No commit receipt is visible. A missing receipt is not proof that a dispatched request failed.",
        };
  }

  private async documentRefs(uid: string): Promise<StoredSourceRef[]> {
    const rows = await this.db
      .prepare(
        `SELECT s.source_refs_json FROM corpus_document_snapshots s JOIN corpus_documents d
      ON d.owner_id=s.owner_id AND d.space_id=s.space_id AND d.document_id=s.document_id WHERE d.owner_id=? AND d.uid=?`,
      )
      .bind(this.owner, uid)
      .all<{ source_refs_json: string }>();
    return [
      ...new Map(
        rows.results
          .flatMap(
            (row) => JSON.parse(row.source_refs_json) as StoredSourceRef[],
          )
          .map((ref) => [canonicalJson(ref), ref]),
      ).values(),
    ];
  }

  async documentMove(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = schemas.corpusDocumentMoveSchema.parse(raw);
    const replay = await this.replay(
      input.idempotency_key,
      "document_move",
      input,
    );
    if (replay.result) return replay.result;
    const source = await this.space(input.space_id);
    await this.space(input.destination_space_id);
    if (input.space_id === input.destination_space_id)
      throw new ContextError(
        "same_space",
        "Space moves require a different destination",
      );
    const row = await this.db
      .prepare(
        "SELECT uid,version FROM corpus_documents WHERE owner_id=? AND space_id=? AND document_id=? AND trash_group_id IS NULL",
      )
      .bind(this.owner, input.space_id, input.document_id)
      .first<{ uid: string; version: number }>();
    if (!row)
      throw new ContextError(
        "document_not_found",
        "The current document locator was not found; aliases are read-only",
        404,
      );
    const destinationId = input.destination_document_id ?? input.document_id;
    const now = this.clock();
    const result = {
      state: "completed",
      uid: row.uid,
      space_id: input.destination_space_id,
      document_id: destinationId,
      version: input.expected_version + 1,
      source_version: input.expected_source_version + 1,
      destination_version: input.expected_destination_version + 1,
      moved_from: { space_id: input.space_id, document_id: input.document_id },
    };
    const refs = await this.documentRefs(row.uid);
    return new CorpusDocumentsService(this.env, this.principal).protectedWrite(
      refs,
      async () => {
        await guardedBatch(
          this.db,
          [
            sourceBindingsGuard(this.db, this.owner, refs),
            activeSpaceGuard(
              this.db,
              this.owner,
              input.space_id,
              input.expected_source_version,
            ),
            activeSpaceGuard(
              this.db,
              this.owner,
              input.destination_space_id,
              input.expected_destination_version,
            ),
            guard(
              this.db,
              "EXISTS (SELECT 1 FROM corpus_documents WHERE owner_id=? AND uid=? AND space_id=? AND document_id=? AND version=? AND trash_group_id IS NULL)",
              [
                this.owner,
                row.uid,
                input.space_id,
                input.document_id,
                input.expected_version,
              ],
            ),
            guard(
              this.db,
              `NOT EXISTS (SELECT 1 FROM corpus_documents WHERE owner_id=? AND space_id=? AND document_id=?) AND
          NOT EXISTS (SELECT 1 FROM corpus_document_aliases WHERE owner_id=? AND space_id=? AND document_id=? AND document_uid<>?)`,
              [
                this.owner,
                input.destination_space_id,
                destinationId,
                this.owner,
                input.destination_space_id,
                destinationId,
                row.uid,
              ],
            ),
          ],
          [
            this.db
              .prepare(
                "DELETE FROM corpus_document_aliases WHERE owner_id=? AND space_id=? AND document_id=? AND document_uid=?",
              )
              .bind(
                this.owner,
                input.destination_space_id,
                destinationId,
                row.uid,
              ),
            this.db
              .prepare(
                "INSERT INTO corpus_document_aliases(owner_id,space_id,document_id,document_uid,created_at) VALUES(?,?,?,?,?)",
              )
              .bind(
                this.owner,
                input.space_id,
                input.document_id,
                row.uid,
                now,
              ),
            this.db
              .prepare(
                "UPDATE corpus_documents SET space_id=?,document_id=?,version=version+1,updated_at=? WHERE owner_id=? AND uid=?",
              )
              .bind(
                input.destination_space_id,
                destinationId,
                now,
                this.owner,
                row.uid,
              ),
            this.db
              .prepare(
                "UPDATE corpus_document_snapshots SET version=? WHERE owner_id=? AND space_id=? AND document_id=? AND snapshot='current'",
              )
              .bind(
                input.expected_version + 1,
                this.owner,
                input.destination_space_id,
                destinationId,
              ),
            this.bumpSpace(source.space_id),
            this.bumpSpace(input.destination_space_id),
            this.managedOwner(),
            this.receipt(
              input.idempotency_key,
              "document_move",
              replay.fingerprint,
              result,
            ),
          ],
        );
        return result;
      },
      input.idempotency_key,
    );
  }

  async itemMove(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = schemas.corpusContextItemMoveSchema.parse(raw);
    const replay = await this.replay(input.idempotency_key, "item_move", input);
    if (replay.result) return replay.result;
    await this.space(input.space_id);
    await this.space(input.destination_space_id);
    if (input.space_id === input.destination_space_id)
      throw new ContextError(
        "same_space",
        "Space moves require a different destination",
      );
    const links = await this.db
      .prepare(
        `SELECT source_space_id,source_connection_id,corpus_id,document_id,revision_id,projection_id,source_unit_id
      FROM corpus_context_sources WHERE owner_id=? AND item_id=? AND provider_kind IS NULL AND provider_record_id IS NULL`,
      )
      .bind(this.owner, input.item_id)
      .all<{
        source_space_id: string | null;
        source_connection_id: string | null;
        corpus_id: string | null;
        document_id: string | null;
        revision_id: string | null;
        projection_id: string | null;
        source_unit_id: string | null;
      }>();
    if (
      links.results.some(
        (ref) =>
          !ref.source_space_id ||
          !ref.source_connection_id ||
          !ref.corpus_id ||
          !ref.document_id ||
          !ref.revision_id ||
          !ref.projection_id ||
          !ref.source_unit_id,
      )
    ) {
      throw new ContextError(
        "source_origin_unresolved",
        "A Source link has no exact origin; resolve it before moving the item",
        409,
      );
    }
    const refs = links.results.map((ref) => ({
      source_space_id: ref.source_space_id!,
      connection_id: ref.source_connection_id!,
      corpus_id: ref.corpus_id!,
      document_id: ref.document_id!,
      revision_id: ref.revision_id!,
      projection_id: ref.projection_id!,
      unit_id: ref.source_unit_id!,
      link_role: "evidence",
    }));
    const result = {
      state: "completed",
      item_id: input.item_id,
      space_id: input.destination_space_id,
      source_version: input.expected_version + 1,
      destination_version: input.expected_destination_version + 1,
    };
    return new CorpusDocumentsService(this.env, this.principal).protectedWrite(
      refs,
      async () => {
        await guardedBatch(
          this.db,
          [
            sourceBindingsGuard(this.db, this.owner, refs),
            activeSpaceGuard(
              this.db,
              this.owner,
              input.space_id,
              input.expected_version,
            ),
            activeSpaceGuard(
              this.db,
              this.owner,
              input.destination_space_id,
              input.expected_destination_version,
            ),
            guard(
              this.db,
              "EXISTS (SELECT 1 FROM corpus_context_items WHERE owner_id=? AND space_id=? AND item_id=? AND lifecycle_state='active' AND trash_group_id IS NULL)",
              [this.owner, input.space_id, input.item_id],
            ),
          ],
          [
            this.db
              .prepare(
                "UPDATE corpus_context_items SET space_id=? WHERE owner_id=? AND item_id=?",
              )
              .bind(input.destination_space_id, this.owner, input.item_id),
            this.bumpSpace(input.space_id),
            this.bumpSpace(input.destination_space_id),
            this.managedOwner(),
            this.receipt(
              input.idempotency_key,
              "item_move",
              replay.fingerprint,
              result,
            ),
          ],
        );
        return result;
      },
      input.idempotency_key,
    );
  }

  private async members(target: Target): Promise<Member[]> {
    const space = await this.space(target.space_id, target.kind === "space");
    if (space.trash_group_id)
      throw new ContextError(
        "already_trashed",
        "This Space is already in the trash",
        409,
        { deletion_group_id: space.trash_group_id },
      );
    const rows: Member[] = [];
    if (target.kind === "space" && target.id !== target.space_id)
      throw new ContextError(
        "invalid_target",
        "Space target id must match space_id",
      );
    if (target.kind === "space")
      rows.push({
        kind: "space",
        id: target.id,
        locator: target.id,
        space_id: target.space_id,
        version: String(space.version),
        previous_state: space.state,
      });
    if (target.kind === "document" || target.kind === "space") {
      const docs = await this.db
        .prepare(
          `SELECT uid AS id,document_id AS locator,CAST(version AS TEXT) AS version FROM corpus_documents
        WHERE owner_id=? AND space_id=? AND trash_group_id IS NULL ${target.kind === "document" ? "AND document_id=?" : ""} ORDER BY uid`,
        )
        .bind(
          this.owner,
          target.space_id,
          ...(target.kind === "document" ? [target.id] : []),
        )
        .all<{ id: string; locator: string; version: string }>();
      rows.push(
        ...docs.results.map((d) => ({
          ...d,
          kind: "document" as const,
          space_id: target.space_id,
          previous_state: "active",
        })),
      );
    }
    if (target.kind === "context_item" || target.kind === "space") {
      const items = await this.db
        .prepare(
          `SELECT item_id FROM corpus_context_items WHERE owner_id=? AND space_id=? AND lifecycle_state='active'
        AND trash_group_id IS NULL ${target.kind === "context_item" ? "AND item_id=?" : ""} ORDER BY item_id`,
        )
        .bind(
          this.owner,
          target.space_id,
          ...(target.kind === "context_item" ? [target.id] : []),
        )
        .all<{ item_id: string }>();
      rows.push(
        ...items.results.map((i) => ({
          kind: "context_item" as const,
          id: i.item_id,
          locator: i.item_id,
          space_id: target.space_id,
          version: String(space.version),
          previous_state: "active",
        })),
      );
    }
    if (target.kind === "context_skill" || target.kind === "space") {
      const skill = await this.db
        .prepare(
          "SELECT version FROM corpus_context_skills WHERE owner_id=? AND space_id=? AND trash_group_id IS NULL",
        )
        .bind(this.owner, target.space_id)
        .first<{ version: string }>();
      if (skill)
        rows.push({
          kind: "context_skill",
          id: target.space_id,
          locator: target.space_id,
          space_id: target.space_id,
          version: skill.version,
          previous_state: "active",
        });
    }
    if (!rows.length)
      throw new ContextError(
        "target_not_found",
        "The current target was not found; it may already be in the trash",
        404,
      );
    return rows;
  }

  private async group(id: string): Promise<Group> {
    const row = await this.db
      .prepare(
        `SELECT ${groupColumns} FROM corpus_trash_groups WHERE owner_id=? AND deletion_group_id=?`,
      )
      .bind(this.owner, id)
      .first<Group>();
    if (!row)
      throw new ContextError(
        "deletion_group_not_found",
        "Deletion group does not exist",
        404,
      );
    return row;
  }
  private async groupMembers(id: string): Promise<Member[]> {
    return (
      await this.db
        .prepare(
          "SELECT kind,id,space_id,locator,version,previous_state FROM corpus_trash_members WHERE owner_id=? AND deletion_group_id=? ORDER BY kind,id",
        )
        .bind(this.owner, id)
        .all<Member>()
    ).results;
  }

  private async blockers(group: Group): Promise<PurgeBlocker[]> {
    const result: PurgeBlocker[] = [];
    if (group.root_kind === "space") {
      const registrations = await this.db
        .prepare(
          `SELECT
        (SELECT count(*) FROM corpus_workspace_bindings WHERE owner_id=? AND space_id=?) +
        (SELECT count(*) FROM corpus_connections WHERE owner_id=? AND space_id=? AND configuration_state<>'detached') AS count`,
        )
        .bind(this.owner, group.space_id, this.owner, group.space_id)
        .first<{ count: number }>();
      if (registrations?.count)
        result.push({
          code: "registrations_attached",
          count: registrations.count,
          message:
            "Detach Workspace and Source/Work registrations through Sync before permanent deletion.",
        });
      const references = await this.db
        .prepare(
          `SELECT
        (SELECT count(*) FROM corpus_document_snapshots s JOIN json_each(s.source_refs_json) r
          WHERE s.owner_id=? AND json_extract(r.value,'$.source_space_id')=? AND NOT EXISTS
            (SELECT 1 FROM corpus_documents d WHERE d.owner_id=s.owner_id AND d.space_id=s.space_id AND d.document_id=s.document_id AND d.trash_group_id=?)) +
        (SELECT count(*) FROM corpus_context_sources s JOIN corpus_context_items i ON i.owner_id=s.owner_id AND i.item_id=s.item_id
          WHERE s.owner_id=? AND s.source_space_id=? AND (i.trash_group_id IS NULL OR i.trash_group_id<>?)) AS count`,
        )
        .bind(
          this.owner,
          group.space_id,
          group.deletion_group_id,
          this.owner,
          group.space_id,
          group.deletion_group_id,
        )
        .first<{ count: number }>();
      if (references?.count)
        result.push({
          code: "source_references",
          count: references.count,
          message:
            "Other saved material still uses Source references from this Space.",
        });
      const earlier = await this.db
        .prepare(
          `SELECT count(*) AS count FROM corpus_trash_groups WHERE owner_id=? AND space_id=?
        AND deletion_group_id<>? AND state NOT IN ('purged','restored')`,
        )
        .bind(this.owner, group.space_id, group.deletion_group_id)
        .first<{ count: number }>();
      if (earlier?.count)
        result.push({
          code: "independent_deletion_groups",
          count: earlier.count,
          message:
            "Earlier deletions keep their own group and deadline; finish or restore them first.",
        });
    }
    const superseded = await this.db
      .prepare(
        `SELECT count(*) AS count FROM corpus_context_items i JOIN corpus_trash_members m
      ON m.owner_id=i.owner_id AND m.kind='context_item' AND m.id=i.supersedes_item_id
      WHERE m.owner_id=? AND m.deletion_group_id=? AND (i.trash_group_id IS NULL OR i.trash_group_id<>?)`,
      )
      .bind(this.owner, group.deletion_group_id, group.deletion_group_id)
      .first<{ count: number }>();
    if (superseded?.count)
      result.push({
        code: "item_references",
        count: superseded.count,
        message: "Another Context item still references an item in this group.",
      });
    return result;
  }

  private async impact(
    action: "trash" | "restore" | "purge",
    target?: Target,
    groupId?: string,
  ) {
    const group = groupId ? await this.group(groupId) : undefined;
    const members = target
      ? await this.members(target)
      : await this.groupMembers(groupId!);
    const parent = await this.space(target?.space_id ?? group!.space_id, true);
    const blockers =
      group && action === "purge" ? await this.blockers(group) : [];
    const material = {
      action,
      target: target ?? null,
      group: group
        ? {
            id: group.deletion_group_id,
            version: group.version,
            state: group.state,
          }
        : null,
      members,
      parent,
      blockers,
    };
    return {
      ...material,
      impact_token: `impact-v1:${await contentSha256(material)}`,
    };
  }

  async preview(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const input = schemas.corpusManagementPreviewSchema.parse(raw);
    const impact = await this.impact(
      input.action,
      input.target,
      input.deletion_group_id,
    );
    return {
      ...impact,
      member_count: impact.members.length,
      retention_days: 30,
      original_files_included: false,
      permanent_delete_requires_explicit_owner_confirmation: true,
    };
  }

  private membersGuard(
    members: Member[],
    parent: Space,
    rootKind: Kind,
    groupId?: string,
  ) {
    const data = canonicalJson(members);
    const where = groupId ? "trash_group_id=?" : "trash_group_id IS NULL";
    const scope = [
      this.owner,
      parent.space_id,
      parent.version,
      parent.state,
      parent.trash_group_id,
    ];
    const conditions = `EXISTS (SELECT 1 FROM corpus_spaces s JOIN corpus_contexts c ON c.owner_id=s.owner_id AND c.space_id=s.space_id
      WHERE s.owner_id=? AND s.space_id=? AND c.version=? AND s.state=? AND s.trash_group_id IS ?) AND NOT EXISTS (
      SELECT 1 FROM json_each(?) m WHERE
       (json_extract(m.value,'$.kind')='document' AND NOT EXISTS (SELECT 1 FROM corpus_documents d WHERE d.owner_id=?
        AND d.uid=json_extract(m.value,'$.id') AND d.space_id=json_extract(m.value,'$.space_id') AND d.document_id=json_extract(m.value,'$.locator')
        ${groupId ? "" : "AND d.version=CAST(json_extract(m.value,'$.version') AS INTEGER)"} AND d.${where})) OR
       (json_extract(m.value,'$.kind')='context_item' AND NOT EXISTS (SELECT 1 FROM corpus_context_items i WHERE i.owner_id=?
        AND i.item_id=json_extract(m.value,'$.id') AND i.space_id=json_extract(m.value,'$.space_id') AND i.${where})) OR
       (json_extract(m.value,'$.kind')='context_skill' AND NOT EXISTS (SELECT 1 FROM corpus_context_skills k WHERE k.owner_id=?
        AND k.space_id=json_extract(m.value,'$.id') AND k.version=json_extract(m.value,'$.version') AND k.${where})))`;
    const values = [
      ...scope,
      data,
      this.owner,
      ...(groupId ? [groupId] : []),
      this.owner,
      ...(groupId ? [groupId] : []),
      this.owner,
      ...(groupId ? [groupId] : []),
    ];
    const checks = [guard(this.db, conditions, values)];
    if (rootKind === "space" && !groupId)
      checks.push(
        guard(
          this.db,
          `
      (SELECT count(*) FROM corpus_documents WHERE owner_id=? AND space_id=? AND trash_group_id IS NULL) +
      (SELECT count(*) FROM corpus_context_items WHERE owner_id=? AND space_id=? AND lifecycle_state='active' AND trash_group_id IS NULL) +
      (SELECT count(*) FROM corpus_context_skills WHERE owner_id=? AND space_id=? AND trash_group_id IS NULL) + 1 = ?`,
          [
            this.owner,
            parent.space_id,
            this.owner,
            parent.space_id,
            this.owner,
            parent.space_id,
            members.length,
          ],
        ),
      );
    return checks;
  }

  async trash(kind: Kind, raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = (
      kind === "document"
        ? schemas.corpusDocumentTrashSchema
        : kind === "context_item"
          ? schemas.corpusContextItemTrashSchema
          : kind === "space"
            ? schemas.corpusSpaceTrashSchema
            : schemas.corpusContextSkillTrashSchema
    ).parse(raw);
    const operation = `${kind}_trash`;
    const replay = await this.replay(input.idempotency_key, operation, input);
    if (replay.result) return replay.result;
    const id = String(
      "document_id" in input
        ? input.document_id
        : "item_id" in input
          ? input.item_id
          : input.space_id,
    );
    const impact = await this.impact("trash", {
      kind,
      space_id: input.space_id,
      id,
    });
    if (
      impact.impact_token !== input.impact_token ||
      impact.members.find((m) => m.kind === kind)?.version !==
        String(input.expected_version)
    ) {
      throw new ContextError(
        "impact_conflict",
        "The reviewed target or its contents changed; review the impact again",
        409,
      );
    }
    const now = this.clock();
    const groupId = `trash_${crypto.randomUUID().replaceAll("-", "")}`;
    const result = {
      state: "trashed",
      deletion_group_id: groupId,
      version: 1,
      trashed_at: now,
      purge_after: purgeAfter(now),
      member_count: impact.members.length,
    };
    const data = canonicalJson(impact.members);
    const writes = [
      this.db
        .prepare(
          `INSERT INTO corpus_trash_groups(owner_id,deletion_group_id,root_kind,root_id,space_id,state,trashed_at,purge_after,updated_at)
        VALUES(?,?,?,?,?,'trashed',?,?,?)`,
        )
        .bind(
          this.owner,
          groupId,
          kind,
          id,
          input.space_id,
          now,
          result.purge_after,
          now,
        ),
      this.db
        .prepare(
          `INSERT INTO corpus_trash_members(owner_id,deletion_group_id,kind,id,space_id,locator,version,previous_state)
        SELECT ?,?,json_extract(value,'$.kind'),json_extract(value,'$.id'),json_extract(value,'$.space_id'),json_extract(value,'$.locator'),
          json_extract(value,'$.version'),json_extract(value,'$.previous_state') FROM json_each(?)`,
        )
        .bind(this.owner, groupId, data),
      this.db
        .prepare(
          `UPDATE corpus_documents SET trash_group_id=?,version=version+1,updated_at=? WHERE owner_id=? AND uid IN
        (SELECT json_extract(value,'$.id') FROM json_each(?) WHERE json_extract(value,'$.kind')='document')`,
        )
        .bind(groupId, now, this.owner, data),
      this.db
        .prepare(
          `UPDATE corpus_document_snapshots SET version=version+1 WHERE owner_id=? AND snapshot='current' AND EXISTS
        (SELECT 1 FROM corpus_documents d WHERE d.owner_id=corpus_document_snapshots.owner_id AND d.space_id=corpus_document_snapshots.space_id
        AND d.document_id=corpus_document_snapshots.document_id AND d.trash_group_id=?)`,
        )
        .bind(this.owner, groupId),
      this.db
        .prepare(
          `UPDATE corpus_context_items SET trash_group_id=?,lifecycle_state='trash' WHERE owner_id=? AND item_id IN
        (SELECT json_extract(value,'$.id') FROM json_each(?) WHERE json_extract(value,'$.kind')='context_item')`,
        )
        .bind(groupId, this.owner, data),
      this.db
        .prepare(
          `UPDATE corpus_context_skills SET trash_group_id=? WHERE owner_id=? AND space_id IN
        (SELECT json_extract(value,'$.id') FROM json_each(?) WHERE json_extract(value,'$.kind')='context_skill')`,
        )
        .bind(groupId, this.owner, data),
      ...(kind === "space"
        ? [
            this.db
              .prepare(
                "UPDATE corpus_spaces SET trash_group_id=? WHERE owner_id=? AND space_id=?",
              )
              .bind(groupId, this.owner, input.space_id),
          ]
        : []),
      this.bumpSpace(input.space_id),
      this.managedOwner(),
      this.receipt(
        input.idempotency_key,
        operation,
        replay.fingerprint,
        result,
      ),
    ];
    await guardedBatch(
      this.db,
      this.membersGuard(impact.members, impact.parent, kind),
      writes,
    );
    return result;
  }

  async trashList(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const input = schemas.corpusTrashListSchema.parse(raw);
    const rows = await this.db
      .prepare(
        `SELECT ${groupColumns} FROM corpus_trash_groups WHERE owner_id=? ${input.space_id ? "AND space_id=?" : ""}
      ${input.state === "all" ? "" : input.state === "trashed" ? "AND state IN ('trashed','blocked','purging')" : "AND state=?"}
      ORDER BY trashed_at,deletion_group_id LIMIT ? OFFSET ?`,
      )
      .bind(
        this.owner,
        ...(input.space_id ? [input.space_id] : []),
        ...(input.state === "all" || input.state === "trashed"
          ? []
          : [input.state]),
        input.limit + 1,
        input.offset,
      )
      .all<Group>();
    const groups = await Promise.all(
      rows.results.slice(0, input.limit).map(async (row) => {
        const { blockers_json: _stored, ...group } = row;
        return {
          ...group,
          restorable: group.state === "trashed" || group.state === "blocked",
          members: await this.groupMembers(group.deletion_group_id),
          blockers:
            group.state === "purged" || group.state === "restored"
              ? []
              : await this.blockers(row),
        };
      }),
    );
    const lastRun = await this.db
      .prepare(
        "SELECT started_at,finished_at,state,result_json FROM corpus_maintenance_runs ORDER BY started_at DESC LIMIT 1",
      )
      .first();
    return {
      groups,
      has_more: rows.results.length > input.limit,
      next_offset:
        rows.results.length > input.limit ? input.offset + input.limit : null,
      maintenance: {
        enabled: this.env.TRASH_SWEEP_ENABLED === "true",
        last_run: lastRun,
      },
    };
  }

  async restore(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = schemas.corpusTrashRestoreSchema.parse(raw);
    const replay = await this.replay(
      input.idempotency_key,
      "trash_restore",
      input,
    );
    if (replay.result) return replay.result;
    const impact = await this.impact(
      "restore",
      undefined,
      input.deletion_group_id,
    );
    const group = await this.group(input.deletion_group_id);
    if (!impact.group || !["trashed", "blocked"].includes(group.state))
      throw new ContextError(
        "not_restorable",
        "Permanent deletion has started or this group has already been restored",
        409,
      );
    if (
      group.version !== input.expected_version ||
      impact.impact_token !== input.impact_token
    )
      throw new ContextError(
        "impact_conflict",
        "The deletion group or restore destination changed",
        409,
      );
    if (
      group.root_kind !== "space" &&
      (impact.parent.state !== "active" || impact.parent.trash_group_id)
    )
      throw new ContextError(
        "restore_destination_inactive",
        "Restore the parent Space first",
        409,
      );
    const now = this.clock();
    const result = {
      state: "restored",
      deletion_group_id: group.deletion_group_id,
      version: group.version + 1,
    };
    const groupGuard = guard(
      this.db,
      "EXISTS (SELECT 1 FROM corpus_trash_groups WHERE owner_id=? AND deletion_group_id=? AND version=? AND state IN ('trashed','blocked'))",
      [this.owner, group.deletion_group_id, group.version],
    );
    await guardedBatch(
      this.db,
      [
        groupGuard,
        ...this.membersGuard(
          impact.members,
          impact.parent,
          group.root_kind,
          group.deletion_group_id,
        ),
      ],
      [
        this.db
          .prepare(
            "UPDATE corpus_documents SET trash_group_id=NULL,version=version+1,updated_at=? WHERE owner_id=? AND trash_group_id=?",
          )
          .bind(now, this.owner, group.deletion_group_id),
        this.db
          .prepare(
            `UPDATE corpus_document_snapshots SET version=version+1 WHERE owner_id=? AND snapshot='current' AND EXISTS
        (SELECT 1 FROM corpus_trash_members m WHERE m.owner_id=corpus_document_snapshots.owner_id AND m.deletion_group_id=? AND m.kind='document'
         AND m.space_id=corpus_document_snapshots.space_id AND m.locator=corpus_document_snapshots.document_id)`,
          )
          .bind(this.owner, group.deletion_group_id),
        this.db
          .prepare(
            "UPDATE corpus_context_items SET trash_group_id=NULL,lifecycle_state='active' WHERE owner_id=? AND trash_group_id=?",
          )
          .bind(this.owner, group.deletion_group_id),
        this.db
          .prepare(
            "UPDATE corpus_context_skills SET trash_group_id=NULL WHERE owner_id=? AND trash_group_id=?",
          )
          .bind(this.owner, group.deletion_group_id),
        this.db
          .prepare(
            "UPDATE corpus_spaces SET trash_group_id=NULL WHERE owner_id=? AND trash_group_id=?",
          )
          .bind(this.owner, group.deletion_group_id),
        this.bumpSpace(group.space_id),
        this.db
          .prepare(
            "UPDATE corpus_trash_groups SET state='restored',version=version+1,updated_at=?,blockers_json='[]' WHERE owner_id=? AND deletion_group_id=?",
          )
          .bind(now, this.owner, group.deletion_group_id),
        this.receipt(
          input.idempotency_key,
          "trash_restore",
          replay.fingerprint,
          result,
        ),
      ],
    );
    return result;
  }

  async purge(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = schemas.corpusTrashPurgeSchema.parse(raw);
    if (this.principal.auth === "sync-device")
      throw new ContextError(
        "owner_confirmation_required",
        "Only an authenticated owner can request permanent deletion",
        403,
      );
    const replay = await this.replay(
      input.idempotency_key,
      "trash_purge",
      input,
    );
    if (replay.result) return this.purgeBatch(input.deletion_group_id);
    const impact = await this.impact(
      "purge",
      undefined,
      input.deletion_group_id,
    );
    const group = await this.group(input.deletion_group_id);
    if (
      group.version !== input.expected_version ||
      impact.impact_token !== input.impact_token
    )
      throw new ContextError(
        "impact_conflict",
        "The reviewed deletion impact changed",
        409,
      );
    if (group.state === "purging") {
      await guardedBatch(
        this.db,
        [
          guard(
            this.db,
            "EXISTS(SELECT 1 FROM corpus_trash_groups WHERE owner_id=? AND deletion_group_id=? AND state='purging')",
            [this.owner, group.deletion_group_id],
          ),
        ],
        [
          this.receipt(
            input.idempotency_key,
            "trash_purge",
            replay.fingerprint,
            { state: "purging", deletion_group_id: group.deletion_group_id },
          ),
        ],
      );
      return this.purgeBatch(group.deletion_group_id);
    }
    return this.beginPurge(group, input.idempotency_key, replay.fingerprint);
  }

  private async beginPurge(
    group: Group,
    key?: string,
    fingerprint?: string,
  ): Promise<Record<string, unknown>> {
    if (!["trashed", "blocked"].includes(group.state))
      throw new ContextError(
        "purge_conflict",
        "Deletion group is not waiting for permanent deletion",
        409,
      );
    const blockers = await this.blockers(group);
    if (blockers.length) {
      await this.db
        .prepare(
          "UPDATE corpus_trash_groups SET state='blocked',blockers_json=?,updated_at=? WHERE owner_id=? AND deletion_group_id=? AND version=? AND state IN ('trashed','blocked')",
        )
        .bind(
          canonicalJson(blockers),
          this.clock(),
          this.owner,
          group.deletion_group_id,
          group.version,
        )
        .run();
      return {
        state: "blocked",
        deletion_group_id: group.deletion_group_id,
        version: group.version,
        blockers,
      };
    }
    const impact = await this.impact(
      "purge",
      undefined,
      group.deletion_group_id,
    );
    const checks = [
      guard(
        this.db,
        "EXISTS (SELECT 1 FROM corpus_trash_groups WHERE owner_id=? AND deletion_group_id=? AND version=? AND state IN ('trashed','blocked'))",
        [this.owner, group.deletion_group_id, group.version],
      ),
      ...this.membersGuard(
        impact.members,
        impact.parent,
        group.root_kind,
        group.deletion_group_id,
      ),
    ];
    if (group.root_kind === "space")
      checks.push(
        guard(
          this.db,
          `NOT EXISTS (SELECT 1 FROM corpus_connections WHERE owner_id=? AND space_id=? AND configuration_state<>'detached')
      AND NOT EXISTS (SELECT 1 FROM corpus_workspace_bindings WHERE owner_id=? AND space_id=?)
      AND NOT EXISTS (SELECT 1 FROM corpus_trash_groups WHERE owner_id=? AND space_id=? AND deletion_group_id<>? AND state NOT IN ('purged','restored'))`,
          [
            this.owner,
            group.space_id,
            this.owner,
            group.space_id,
            this.owner,
            group.space_id,
            group.deletion_group_id,
          ],
        ),
      );
    if (group.root_kind === "space")
      checks.push(
        guard(
          this.db,
          `
      NOT EXISTS (SELECT 1 FROM corpus_document_snapshots s JOIN json_each(s.source_refs_json) r
        WHERE s.owner_id=? AND json_extract(r.value,'$.source_space_id')=? AND NOT EXISTS
          (SELECT 1 FROM corpus_documents d WHERE d.owner_id=s.owner_id AND d.space_id=s.space_id AND d.document_id=s.document_id AND d.trash_group_id=?))
      AND NOT EXISTS (SELECT 1 FROM corpus_context_sources s JOIN corpus_context_items i ON i.owner_id=s.owner_id AND i.item_id=s.item_id
        WHERE s.owner_id=? AND s.source_space_id=? AND (i.trash_group_id IS NULL OR i.trash_group_id<>?))`,
          [
            this.owner,
            group.space_id,
            group.deletion_group_id,
            this.owner,
            group.space_id,
            group.deletion_group_id,
          ],
        ),
      );
    checks.push(
      guard(
        this.db,
        `NOT EXISTS (SELECT 1 FROM corpus_context_items i JOIN corpus_trash_members m ON m.owner_id=i.owner_id
      AND m.kind='context_item' AND m.id=i.supersedes_item_id WHERE m.owner_id=? AND m.deletion_group_id=? AND (i.trash_group_id IS NULL OR i.trash_group_id<>?))`,
        [this.owner, group.deletion_group_id, group.deletion_group_id],
      ),
    );
    const pending = {
      state: "purging",
      deletion_group_id: group.deletion_group_id,
      version: group.version + 1,
      restorable: false,
    };
    await guardedBatch(this.db, checks, [
      this.db
        .prepare(
          "UPDATE corpus_trash_groups SET state='purging',version=version+1,blockers_json='[]',updated_at=? WHERE owner_id=? AND deletion_group_id=?",
        )
        .bind(this.clock(), this.owner, group.deletion_group_id),
      ...(key ? [this.receipt(key, "trash_purge", fingerprint!, pending)] : []),
    ]);
    return this.purgeBatch(group.deletion_group_id);
  }

  private async purgeBatch(id: string): Promise<Record<string, unknown>> {
    const group = await this.group(id);
    if (group.state === "purged")
      return { state: "purged", deletion_group_id: id, restorable: false };
    if (group.state !== "purging")
      throw new ContextError(
        "purge_conflict",
        "This deletion group is not being purged",
        409,
      );
    await this.db
      .prepare(
        "UPDATE corpus_trash_groups SET updated_at=? WHERE owner_id=? AND deletion_group_id=? AND state='purging'",
      )
      .bind(this.clock(), this.owner, id)
      .run();
    const members = (
      await this.db
        .prepare(
          "SELECT kind,id FROM corpus_trash_members WHERE owner_id=? AND deletion_group_id=? AND kind<>'space' ORDER BY kind,id LIMIT 100",
        )
        .bind(this.owner, id)
        .all<Member>()
    ).results;
    const selected = canonicalJson(members);
    const statements = (
      [
        ["document", "corpus_documents", "uid"],
        ["context_item", "corpus_context_items", "item_id"],
        ["context_skill", "corpus_context_skills", "space_id"],
      ] as const
    ).map(([kind, table, column]) =>
      this.db
        .prepare(
          `DELETE FROM ${table} WHERE owner_id=? AND trash_group_id=? AND ${column} IN
       (SELECT json_extract(value,'$.id') FROM json_each(?) WHERE json_extract(value,'$.kind')=?)`,
        )
        .bind(this.owner, id, selected, kind),
    );
    statements.push(
      this.db
        .prepare(
          `DELETE FROM corpus_trash_members WHERE owner_id=? AND deletion_group_id=? AND EXISTS
      (SELECT 1 FROM json_each(?) m WHERE json_extract(m.value,'$.id')=corpus_trash_members.id AND json_extract(m.value,'$.kind')=corpus_trash_members.kind)`,
        )
        .bind(this.owner, id, selected),
    );
    await guardedBatch(
      this.db,
      [
        guard(
          this.db,
          "EXISTS (SELECT 1 FROM corpus_trash_groups WHERE owner_id=? AND deletion_group_id=? AND state='purging')",
          [this.owner, id],
        ),
      ],
      statements,
    );
    const remaining = (await this.db
      .prepare(
        "SELECT count(*) AS count FROM corpus_trash_members WHERE owner_id=? AND deletion_group_id=? AND kind<>'space'",
      )
      .bind(this.owner, id)
      .first<{ count: number }>())!.count;
    if (remaining)
      return {
        state: "purging",
        deletion_group_id: id,
        restorable: false,
        remaining_members: remaining,
      };
    const result = {
      state: "purged",
      deletion_group_id: id,
      restorable: false,
    };
    await guardedBatch(
      this.db,
      [
        guard(
          this.db,
          "EXISTS (SELECT 1 FROM corpus_trash_groups WHERE owner_id=? AND deletion_group_id=? AND state='purging')",
          [this.owner, id],
        ),
      ],
      [
        ...(group.root_kind === "space"
          ? [
              this.db
                .prepare(
                  "DELETE FROM corpus_contexts WHERE owner_id=? AND space_id=?",
                )
                .bind(this.owner, group.space_id),
              this.db
                .prepare(
                  "DELETE FROM corpus_spaces WHERE owner_id=? AND space_id=? AND trash_group_id=?",
                )
                .bind(this.owner, group.space_id, id),
            ]
          : []),
        this.db
          .prepare(
            "DELETE FROM corpus_trash_members WHERE owner_id=? AND deletion_group_id=?",
          )
          .bind(this.owner, id),
        this.db
          .prepare(
            "UPDATE corpus_trash_groups SET state='purged',updated_at=? WHERE owner_id=? AND deletion_group_id=?",
          )
          .bind(this.clock(), this.owner, id),
        this.db
          .prepare(
            "UPDATE corpus_operation_receipts SET result_json=? WHERE owner_id=? AND operation='trash_purge' AND json_extract(result_json,'$.deletion_group_id')=?",
          )
          .bind(canonicalJson(result), this.owner, id),
      ],
    );
    return result;
  }

  /** Internal maintenance entry: only due groups, never general body or permission writes. */
  async auditDue(id: string): Promise<Record<string, unknown>> {
    const group = await this.group(id);
    return {
      state: "dry_run",
      deletion_group_id: id,
      purge_after: group.purge_after,
      blockers: await this.blockers(group),
    };
  }
  async purgeDue(id: string): Promise<Record<string, unknown>> {
    const group = await this.group(id);
    if (Date.parse(group.purge_after) > Date.parse(this.clock()))
      return { state: "not_due" };
    if (group.state === "purging") return this.purgeBatch(id);
    return this.beginPurge(group);
  }

  async spaceRevise(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = schemas.corpusSpaceReviseSchema.parse(raw);
    const parent = await this.space(input.space_id, true);
    if (parent.trash_group_id)
      throw new ContextError(
        "space_trashed",
        "Restore this Space before revising it",
        409,
      );
    await guardedBatch(
      this.db,
      [
        guard(
          this.db,
          `EXISTS (SELECT 1 FROM corpus_contexts c JOIN corpus_spaces s ON s.owner_id=c.owner_id AND s.space_id=c.space_id
      WHERE c.owner_id=? AND c.space_id=? AND c.version=? AND s.trash_group_id IS NULL)`,
          [this.owner, input.space_id, input.expected_version],
        ),
      ],
      [
        this.db
          .prepare(
            "UPDATE corpus_spaces SET display_name=?,state=?,updated_at=? WHERE owner_id=? AND space_id=?",
          )
          .bind(
            input.display_name,
            input.state,
            this.clock(),
            this.owner,
            input.space_id,
          ),
        this.db
          .prepare(
            "UPDATE corpus_contexts SET title=?,purpose=?,scope_json=?,version=version+1,updated_at=? WHERE owner_id=? AND space_id=?",
          )
          .bind(
            input.display_name,
            input.purpose,
            canonicalJson(input.scope),
            this.clock(),
            this.owner,
            input.space_id,
          ),
        this.managedOwner(),
      ],
    );
    return {
      space_id: input.space_id,
      version: input.expected_version + 1,
      state: input.state,
      changed: true,
    };
  }

  async itemCreate(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = schemas.corpusContextItemCreateSchema.parse(raw);
    await guardedBatch(
      this.db,
      [
        activeSpaceGuard(
          this.db,
          this.owner,
          input.space_id,
          input.expected_version,
        ),
      ],
      [
        this.db
          .prepare(
            `INSERT INTO corpus_context_items(owner_id,space_id,item_id,kind,body_text,attributes_json,created_at) VALUES(?,?,?,?,?,?,?)`,
          )
          .bind(
            this.owner,
            input.space_id,
            input.item_id,
            input.kind,
            input.body_text,
            canonicalJson(
              input.status === undefined ? {} : { status: input.status },
            ),
            this.clock(),
          ),
        this.bumpSpace(input.space_id),
        this.managedOwner(),
      ],
    );
    return {
      space_id: input.space_id,
      item_id: input.item_id,
      version: input.expected_version + 1,
      created: true,
    };
  }
}
