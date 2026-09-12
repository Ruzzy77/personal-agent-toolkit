import { canonicalJson, nowIso, sha256Hex } from "./canonical";
import { ContextError } from "./errors";
import {
  corpusContextSearchSchema,
  corpusDocumentCreateSchema,
  corpusDocumentListSchema,
  corpusDocumentReadSchema,
  corpusDocumentRestoreSchema,
  corpusDocumentReviseSchema,
  corpusSpaceCreateSchema,
  corpusWorkspaceBindSchema,
  corpusWorkspaceResolveSchema,
  type CorpusDocumentInput,
  type CorpusDocumentSourceRef,
} from "./corpus-document-schemas";
import type { Env, Principal } from "./types";
import {
  activeSpaceGuard,
  guard,
  guardedBatch,
  managementSchemaReady,
  sourceBindingsGuard,
} from "./management-db";

export interface StoredSourceRef extends CorpusDocumentSourceRef {
  corpus_id: string;
}
interface SnapshotRow {
  uid?: string;
  space_id: string;
  trash_group_id?: string | null;
  document_id: string;
  snapshot: "current" | "previous";
  version: number;
  title: string;
  body_markdown: string;
  kind: "context" | "guidance";
  applicability_json: string;
  guidance_approval_json: string | null;
  source_refs_json: string;
  migration_provenance_json: string | null;
  saved_at: string;
  document_version: number;
  created_at: string;
  updated_at: string;
}
interface BindingRow {
  host_id: string;
  workspace_id: string;
  space_id: string;
  environment_kind: "local" | "remote";
  project_id: string | null;
  version: number;
  updated_at: string;
}
interface ConnectionRow {
  connection_id: string;
  corpus_id: string;
  roles_json: string;
}
interface ContextSourceRow {
  source_space_id?: string | null;
  source_connection_id?: string | null;
  item_id: string;
  source_ref_id: string;
  corpus_id: string | null;
  document_id: string | null;
  revision_id: string | null;
  projection_id: string | null;
  source_unit_id: string | null;
  link_role: string;
  is_provider: number;
  ref_count: number;
}
type SnapshotValue = Pick<
  SnapshotRow,
  | "title"
  | "body_markdown"
  | "kind"
  | "applicability_json"
  | "guidance_approval_json"
  | "source_refs_json"
  | "migration_provenance_json"
>;

const snapshotColumns =
  "title, body_markdown, kind, applicability_json, guidance_approval_json, source_refs_json, migration_provenance_json";
const snapshotFields = snapshotColumns.split(", ") as Array<
  keyof SnapshotValue
>;
const jsonOrNull = (value: string | null): unknown =>
  value === null ? null : JSON.parse(value);

function readReference(
  spaceId: string,
  connectionId: string,
  corpusId: string,
  unitId: string,
): string {
  const bytes = new TextEncoder().encode(
    canonicalJson({ version: 1, spaceId, connectionId, corpusId, unitId }),
  );
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return `read1.${btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "")}`;
}

function boundedOutput(
  value: Record<string, unknown>,
): Record<string, unknown> {
  if (
    new TextEncoder().encode(JSON.stringify(value)).byteLength >
    2 * 1024 * 1024
  ) {
    throw new ContextError(
      "budget_exceeded",
      "Native Context response exceeds 2 MiB; reduce the requested page size",
    );
  }
  return value;
}

function checkDocumentBytes(raw: unknown): void {
  const text =
    raw && typeof raw === "object" && !Array.isArray(raw)
      ? (raw as Record<string, unknown>).body_markdown
      : undefined;
  if (
    typeof text === "string" &&
    new TextEncoder().encode(text).byteLength > 524_288
  ) {
    throw new ContextError(
      "document_too_large",
      "Document Markdown exceeds the 512 KiB UTF-8 body budget",
    );
  }
}

function page<T>(items: T[], limit: number, offset: number) {
  const selected = items.slice(0, limit);
  const hasMore = items.length > limit;
  return {
    items: selected,
    limit,
    offset,
    returned_count: selected.length,
    has_more: hasMore,
    next_offset: hasMore ? offset + selected.length : null,
  };
}

/** Native project text and explicit environment bindings, not a filesystem authority. */
export class CorpusDocumentsService {
  constructor(
    private readonly env: Env,
    private readonly principal: Principal,
  ) {}

  private requireScope(write = false): void {
    if (!this.principal.scopes.has(write ? "corpus.write" : "corpus.read")) {
      throw new ContextError(
        "insufficient_scope",
        "the required Corpus scope is missing",
        403,
      );
    }
  }

  private async space(spaceId: string): Promise<void> {
    const managed = await managementSchemaReady(this.env.STATE_DB);
    const row = await this.env.STATE_DB.prepare(
      `SELECT space.space_id FROM corpus_spaces AS space
       JOIN corpus_contexts AS context ON context.owner_id = space.owner_id AND context.space_id = space.space_id
       WHERE space.owner_id = ? AND space.space_id = ? AND space.state = 'active' AND space.access_scope = 'remote_allowed'
       ${managed ? "AND space.trash_group_id IS NULL" : ""}`,
    )
      .bind(this.principal.ownerId, spaceId)
      .first();
    if (!row)
      throw new ContextError(
        "space_not_found",
        "Context Space does not exist",
        404,
      );
  }

  async spaceCreate(raw: unknown): Promise<Record<string, unknown>> {
    this.requireScope(true);
    const input = corpusSpaceCreateSchema.parse(raw);
    const now = nowIso();
    try {
      await this.env.STATE_DB.batch([
        this.env.STATE_DB.prepare(
          `INSERT INTO corpus_spaces(owner_id, space_id, display_name, state, access_scope, updated_at)
          VALUES (?, ?, ?, 'active', 'remote_allowed', ?)`,
        ).bind(this.principal.ownerId, input.space_id, input.display_name, now),
        this.env.STATE_DB.prepare(
          `INSERT INTO corpus_contexts(owner_id, space_id, title, purpose, scope_json, version, updated_at)
          VALUES (?, ?, ?, ?, ?, 1, ?)`,
        ).bind(
          this.principal.ownerId,
          input.space_id,
          input.display_name,
          input.purpose,
          canonicalJson(input.scope),
          now,
        ),
      ]);
    } catch (error) {
      const existing = await this.env.STATE_DB.prepare(
        "SELECT 1 FROM corpus_spaces WHERE owner_id = ? AND space_id = ?",
      )
        .bind(this.principal.ownerId, input.space_id)
        .first();
      if (existing)
        throw new ContextError(
          "space_conflict",
          "Space already exists; existing state was not replaced",
          409,
        );
      throw error;
    }
    return {
      space_id: input.space_id,
      display_name: input.display_name,
      version: 1,
      created: true,
    };
  }

  async workspaceBind(raw: unknown): Promise<Record<string, unknown>> {
    this.requireScope(true);
    const input = corpusWorkspaceBindSchema.parse(raw);
    await this.space(input.space_id);
    const now = nowIso();
    const next =
      input.expected_version === "absent" ? 1 : input.expected_version + 1;
    const statement =
      input.expected_version === "absent"
        ? this.env.STATE_DB.prepare(
            `INSERT INTO corpus_workspace_bindings(owner_id,host_id,workspace_id,space_id,environment_kind,project_id,version,updated_at)
          VALUES (?,?,?,?,?,?,1,?) ON CONFLICT(owner_id,host_id,workspace_id) DO NOTHING`,
          ).bind(
            this.principal.ownerId,
            input.host_id,
            input.workspace_id,
            input.space_id,
            input.environment_kind,
            input.project_id ?? null,
            now,
          )
        : this.env.STATE_DB.prepare(
            `UPDATE corpus_workspace_bindings SET space_id=?,environment_kind=?,project_id=?,version=?,updated_at=?
          WHERE owner_id=? AND host_id=? AND workspace_id=? AND version=?`,
          ).bind(
            input.space_id,
            input.environment_kind,
            input.project_id ?? null,
            next,
            now,
            this.principal.ownerId,
            input.host_id,
            input.workspace_id,
            input.expected_version,
          );
    const results = await guardedBatch(
      this.env.STATE_DB,
      (await managementSchemaReady(this.env.STATE_DB))
        ? [
            activeSpaceGuard(
              this.env.STATE_DB,
              this.principal.ownerId,
              input.space_id,
            ),
          ]
        : [],
      [statement],
      "workspace_conflict",
    );
    const result = results[results.length - 1]!;
    if (result.meta.changes !== 1)
      throw new ContextError(
        "workspace_conflict",
        "Workspace binding changed or already exists",
        409,
      );
    return {
      workspace_id: input.workspace_id,
      host_id: input.host_id,
      space_id: input.space_id,
      environment_kind: input.environment_kind,
      project_id: input.project_id ?? null,
      version: next,
      updated_at: now,
    };
  }

  async workspaceResolve(raw: unknown): Promise<Record<string, unknown>> {
    this.requireScope();
    const input = corpusWorkspaceResolveSchema.parse(raw);
    const row = await this.env.STATE_DB.prepare(
      `SELECT host_id, workspace_id, space_id, environment_kind, project_id, version, updated_at
      FROM corpus_workspace_bindings WHERE owner_id=? AND host_id=? AND workspace_id=?`,
    )
      .bind(this.principal.ownerId, input.host_id, input.workspace_id)
      .first<BindingRow>();
    if (!row)
      throw new ContextError(
        "workspace_not_found",
        "Workspace binding does not exist",
        404,
      );
    await this.space(row.space_id);
    return { workspace: row, filesystem_authority: false };
  }

  private async connections(
    spaceId: string,
  ): Promise<Map<string, ConnectionRow>> {
    const rows = await this.env.STATE_DB.prepare(
      `SELECT c.connection_id, c.corpus_id, c.roles_json FROM corpus_connections c JOIN corpus_spaces s ON s.owner_id=c.owner_id AND s.space_id=c.space_id
      WHERE c.owner_id=? AND c.space_id=? AND c.access_scope='remote_allowed' AND s.access_scope='remote_allowed' AND c.index_mode='indexed' AND c.corpus_id IS NOT NULL ORDER BY c.connection_id`,
    )
      .bind(this.principal.ownerId, spaceId)
      .all<ConnectionRow>();
    return new Map(
      rows.results
        .filter((row) =>
          (JSON.parse(row.roles_json) as string[]).includes("source"),
        )
        .map((row) => [row.connection_id, row]),
    );
  }

  private async sourceRefs(
    spaceId: string,
    refs: CorpusDocumentSourceRef[],
  ): Promise<StoredSourceRef[]> {
    if (
      refs.some(
        (ref) => ref.source_space_id && ref.source_space_id !== spaceId,
      ) &&
      !(await managementSchemaReady(this.env.STATE_DB))
    ) {
      throw new ContextError(
        "source_scope_migration_pending",
        "Source origin migration must finish before cross-Space evidence can be saved",
        503,
      );
    }
    const bySpace = new Map<string, Map<string, ConnectionRow>>();
    const result: StoredSourceRef[] = [];
    const seen = new Set<string>();
    for (const ref of refs) {
      const origin = ref.source_space_id ?? spaceId;
      if (!bySpace.has(origin))
        bySpace.set(origin, await this.connections(origin));
      const connections = bySpace.get(origin)!;
      const connection = connections.get(ref.connection_id);
      if (!connection)
        throw new ContextError(
          "source_connection_not_found",
          "Source reference requires a readable Connection in this Space",
          404,
        );
      const value = {
        ...ref,
        source_space_id: origin,
        corpus_id: connection.corpus_id,
      };
      const key = canonicalJson(value);
      if (seen.has(key))
        throw new ContextError(
          "duplicate_source_ref",
          "Source references must be unique",
        );
      seen.add(key);
      result.push(value);
    }
    return result;
  }

  private async sourceCall(
    corpusId: string,
    path: string,
    value: Record<string, unknown>,
  ): Promise<void> {
    const stub = this.env.CORPUS_SHARDS.get(
      this.env.CORPUS_SHARDS.idFromName(
        `${this.principal.ownerId}:${corpusId}`,
      ),
    );
    const response = await stub.fetch(
      `https://corpus.internal/context-document/${path}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Owner-Id": this.principal.ownerId,
        },
        body: JSON.stringify({
          ...value,
          ownerId: this.principal.ownerId,
          corpusId,
        }),
      },
    );
    const result = (await response.json()) as {
      ok?: boolean;
      error?: { code?: string; message?: string };
    };
    if (!response.ok || !result.ok)
      throw new ContextError(
        result.error?.code ?? "source_protection_failed",
        result.error?.message ??
          "Exact Source protection could not be established",
        response.status >= 400 ? response.status : 409,
        {
          protection_request_rejected:
            response.status >= 400 && response.status < 500,
        },
      );
  }

  async protectedWrite(
    refs: StoredSourceRef[],
    commit: () => Promise<Record<string, unknown>>,
    requestKey?: string,
  ): Promise<Record<string, unknown>> {
    const groups = new Map<string, StoredSourceRef[]>();
    for (const ref of refs)
      groups.set(ref.corpus_id, [...(groups.get(ref.corpus_id) ?? []), ref]);
    const reservations: Array<{ corpus_id: string; reservation_id: string }> =
      [];
    const warnings: Array<Record<string, string>> = [];
    let result: Record<string, unknown> = {};
    let failure: unknown;
    let safeToRelease = false;
    try {
      for (const [corpusId, values] of groups) {
        const reservation = {
          corpus_id: corpusId,
          reservation_id: requestKey
            ? `cdoc_${await sha256Hex(canonicalJson([this.principal.ownerId, requestKey, corpusId]))}`
            : `cdoc_${crypto.randomUUID().replaceAll("-", "")}`,
        };
        // Record before sending: a lost response may still have committed a reservation.
        reservations.push(reservation);
        if (requestKey)
          await this.env.STATE_DB.prepare(
            "INSERT OR IGNORE INTO corpus_operation_source_pins VALUES(?,?,?,?)",
          )
            .bind(
              this.principal.ownerId,
              requestKey,
              corpusId,
              reservation.reservation_id,
            )
            .run();
        await this.sourceCall(corpusId, "protect", {
          reservation_id: reservation.reservation_id,
          refs: values.map(
            ({ document_id, revision_id, projection_id, unit_id }) => ({
              document_id,
              revision_id,
              projection_id,
              unit_id,
            }),
          ),
        });
      }
      result = await commit();
      safeToRelease = true;
    } catch (error) {
      failure = error;
      safeToRelease =
        error instanceof ContextError &&
        (error.details.write_committed === false ||
          error.details.protection_request_rejected === true);
    }
    if (safeToRelease) {
      // Successful D1 snapshots now provide durable protection. On an atomic
      // commit failure only these temporary pins are released. Never expire a
      // pin on a timer while its D1 write may still be in flight.
      for (const reservation of reservations) {
        try {
          await this.sourceCall(reservation.corpus_id, "release", {
            reservation_id: reservation.reservation_id,
          });
          if (requestKey)
            await this.env.STATE_DB.prepare(
              "DELETE FROM corpus_operation_source_pins WHERE owner_id=? AND request_key=? AND corpus_id=?",
            )
              .bind(this.principal.ownerId, requestKey, reservation.corpus_id)
              .run();
        } catch {
          warnings.push({
            code: "source_protection_release_pending",
            reservation_id: reservation.reservation_id,
          });
        }
      }
    } else {
      // A failed network/D1 response is not evidence that a dispatched commit
      // did not happen. Keep pins until an operator establishes its outcome.
      for (const reservation of reservations)
        warnings.push({
          code: "source_protection_outcome_pending",
          reservation_id: reservation.reservation_id,
        });
    }
    if (failure !== undefined) {
      if (failure instanceof ContextError)
        throw new ContextError(failure.code, failure.message, failure.status, {
          ...failure.details,
          ...(warnings.length ? { warnings } : {}),
        });
      if (warnings.length)
        throw new ContextError(
          "document_write_outcome_unknown",
          "Document write outcome is unknown; Source protection was retained",
          503,
          { warnings },
        );
      throw failure;
    }
    return warnings.length ? { ...result, warnings } : result;
  }

  async releaseCommittedManagementPins(
    requestKey: string,
  ): Promise<Array<Record<string, string>>> {
    const receipt = await this.env.STATE_DB.prepare(
      "SELECT 1 FROM corpus_operation_receipts WHERE owner_id=? AND request_key=?",
    )
      .bind(this.principal.ownerId, requestKey)
      .first();
    if (!receipt) return [];
    const pins = await this.env.STATE_DB.prepare(
      "SELECT corpus_id,reservation_id FROM corpus_operation_source_pins WHERE owner_id=? AND request_key=?",
    )
      .bind(this.principal.ownerId, requestKey)
      .all<{ corpus_id: string; reservation_id: string }>();
    const pending: Array<Record<string, string>> = [];
    for (const pin of pins.results) {
      try {
        await this.sourceCall(pin.corpus_id, "release", {
          reservation_id: pin.reservation_id,
        });
        await this.env.STATE_DB.prepare(
          "DELETE FROM corpus_operation_source_pins WHERE owner_id=? AND request_key=? AND corpus_id=?",
        )
          .bind(this.principal.ownerId, requestKey, pin.corpus_id)
          .run();
      } catch {
        pending.push({
          code: "source_protection_release_pending",
          reservation_id: pin.reservation_id,
        });
      }
    }
    return pending;
  }

  private value(
    input: CorpusDocumentInput,
    refs: StoredSourceRef[],
  ): SnapshotValue {
    const value = {
      title: input.title,
      body_markdown: input.body_markdown,
      kind: input.kind,
      applicability_json: canonicalJson(input.applicability),
      guidance_approval_json: input.guidance_approval
        ? canonicalJson({
            ...input.guidance_approval,
            provenance: "user_approved_guidance",
            approved_at: nowIso(),
          })
        : null,
      source_refs_json: canonicalJson(refs),
      migration_provenance_json: input.migration_provenance
        ? canonicalJson({
            ...input.migration_provenance,
            ...(input.migration_provenance.source_id
              ? {
                  source_space_id:
                    input.migration_provenance.source_space_id ??
                    input.space_id,
                }
              : {}),
          })
        : null,
    };
    if (new TextEncoder().encode(canonicalJson(value)).byteLength > 700_000) {
      throw new ContextError(
        "budget_exceeded",
        "Document content and metadata exceed the snapshot budget",
      );
    }
    return value;
  }

  async documentCreate(raw: unknown): Promise<Record<string, unknown>> {
    this.requireScope(true);
    checkDocumentBytes(raw);
    const input = corpusDocumentCreateSchema.parse(raw);
    await this.space(input.space_id);
    const refs = await this.sourceRefs(input.space_id, input.source_refs);
    const value = this.value(input, refs);
    const now = nowIso();
    const managed = await managementSchemaReady(this.env.STATE_DB);
    return this.protectedWrite(refs, async () => {
      try {
        await this.env.STATE_DB.batch([
          ...(managed
            ? [
                activeSpaceGuard(
                  this.env.STATE_DB,
                  this.principal.ownerId,
                  input.space_id,
                ),
                sourceBindingsGuard(
                  this.env.STATE_DB,
                  this.principal.ownerId,
                  refs,
                ),
              ]
            : []),
          this.env.STATE_DB.prepare(
            `INSERT INTO corpus_documents(owner_id,space_id,document_id,version,created_at,updated_at) VALUES (?,?,?,1,?,?)`,
          ).bind(
            this.principal.ownerId,
            input.space_id,
            input.document_id,
            now,
            now,
          ),
          this.env.STATE_DB.prepare(
            `INSERT INTO corpus_document_snapshots(owner_id,space_id,document_id,snapshot,version,${snapshotColumns},saved_at)
            VALUES (?,?,?,'current',1,?,?,?,?,?,?,?,?)`,
          ).bind(
            this.principal.ownerId,
            input.space_id,
            input.document_id,
            ...snapshotFields.map((key) => value[key]),
            now,
          ),
        ]);
      } catch (error) {
        if (
          error instanceof Error &&
          /UNIQUE constraint failed: corpus_documents\.|document_alias_conflict|malformed JSON/.test(
            error.message,
          )
        ) {
          throw new ContextError(
            "document_conflict",
            "Document already exists; existing content was not replaced",
            409,
            { write_committed: false },
          );
        }
        throw error;
      }
      return {
        space_id: input.space_id,
        document_id: input.document_id,
        version: 1,
        created: true,
        updated_at: now,
      };
    });
  }

  private async snapshot(
    spaceId: string,
    documentId: string,
    snapshot: "current" | "previous",
  ): Promise<SnapshotRow> {
    const row = await this.env.STATE_DB.prepare(
      `SELECT document.*, snapshot.*, document.version AS document_version, document.created_at, document.updated_at
      FROM corpus_document_snapshots AS snapshot JOIN corpus_documents AS document
        ON document.owner_id=snapshot.owner_id AND document.space_id=snapshot.space_id AND document.document_id=snapshot.document_id
      WHERE snapshot.owner_id=? AND snapshot.space_id=? AND snapshot.document_id=? AND snapshot.snapshot=?`,
    )
      .bind(this.principal.ownerId, spaceId, documentId, snapshot)
      .first<SnapshotRow>();
    if (!row)
      throw new ContextError(
        snapshot === "previous"
          ? "previous_snapshot_not_found"
          : "document_not_found",
        snapshot === "previous"
          ? "Document has no previous snapshot"
          : "Document does not exist",
        404,
      );
    if (row.trash_group_id)
      throw new ContextError(
        "document_trashed",
        "Document is in the trash; restore its deletion group before editing or reading it",
        409,
        { deletion_group_id: row.trash_group_id },
      );
    return row;
  }

  private async revise(
    spaceId: string,
    documentId: string,
    expected: number,
    value: SnapshotValue,
    restoredFrom?: number,
  ): Promise<Record<string, unknown>> {
    const now = nowIso();
    const next = expected + 1;
    const versionGuard = `EXISTS (SELECT 1 FROM corpus_documents AS document WHERE document.owner_id=? AND document.space_id=? AND document.document_id=? AND document.version=?)`;
    const identity = [this.principal.ownerId, spaceId, documentId];
    const refs = JSON.parse(value.source_refs_json) as StoredSourceRef[];
    const managed = await managementSchemaReady(this.env.STATE_DB);
    return this.protectedWrite(refs, async () => {
      const results = await guardedBatch(
        this.env.STATE_DB,
        managed
          ? [
              activeSpaceGuard(
                this.env.STATE_DB,
                this.principal.ownerId,
                spaceId,
              ),
              sourceBindingsGuard(
                this.env.STATE_DB,
                this.principal.ownerId,
                refs,
              ),
              guard(
                this.env.STATE_DB,
                `${versionGuard} AND EXISTS (SELECT 1 FROM corpus_documents WHERE owner_id=? AND space_id=? AND document_id=? AND trash_group_id IS NULL)`,
                [...identity, expected, ...identity],
              ),
            ]
          : [],
        [
          this.env.STATE_DB.prepare(
            `INSERT INTO corpus_document_snapshots(owner_id,space_id,document_id,snapshot,version,${snapshotColumns},saved_at)
          SELECT owner_id,space_id,document_id,'previous',version,${snapshotColumns},saved_at FROM corpus_document_snapshots
          WHERE owner_id=? AND space_id=? AND document_id=? AND snapshot='current' AND ${versionGuard}
          ON CONFLICT(owner_id,space_id,document_id,snapshot) DO UPDATE SET version=excluded.version,
          ${snapshotFields.map((key) => `${key}=excluded.${key}`).join(",")},saved_at=excluded.saved_at`,
          ).bind(...identity, ...identity, expected),
          this.env.STATE_DB.prepare(
            `UPDATE corpus_document_snapshots SET version=?,${snapshotFields.map((key) => `${key}=?`).join(",")},saved_at=?
          WHERE owner_id=? AND space_id=? AND document_id=? AND snapshot='current' AND ${versionGuard}`,
          ).bind(
            next,
            ...snapshotFields.map((key) => value[key]),
            now,
            ...identity,
            ...identity,
            expected,
          ),
          this.env.STATE_DB.prepare(
            `UPDATE corpus_documents SET version=?,updated_at=? WHERE owner_id=? AND space_id=? AND document_id=? AND version=?`,
          ).bind(next, now, ...identity, expected),
        ],
        "document_conflict",
      );
      if (results.at(-1)?.meta.changes !== 1)
        throw new ContextError(
          "document_conflict",
          "Document changed after it was read",
          409,
          { expected_version: expected, write_committed: false },
        );
      return {
        space_id: spaceId,
        document_id: documentId,
        version: next,
        updated_at: now,
        ...(restoredFrom === undefined
          ? {}
          : { restored_from_version: restoredFrom }),
      };
    });
  }

  async documentRevise(raw: unknown): Promise<Record<string, unknown>> {
    this.requireScope(true);
    checkDocumentBytes(raw);
    const input = corpusDocumentReviseSchema.parse(raw);
    await this.space(input.space_id);
    const current = await this.snapshot(
      input.space_id,
      input.document_id,
      "current",
    );
    const scoped =
      (JSON.parse(current.source_refs_json) as StoredSourceRef[]).some(
        (ref) => ref.source_space_id && ref.source_space_id !== input.space_id,
      ) ||
      ((
        jsonOrNull(current.migration_provenance_json) as {
          source_space_id?: string;
        } | null
      )?.source_space_id !== undefined &&
        (
          jsonOrNull(current.migration_provenance_json) as {
            source_space_id?: string;
          }
        ).source_space_id !== input.space_id);
    if (scoped && input.source_scope_version !== 2)
      throw new ContextError(
        "source_scope_required",
        "This document carries evidence from another Space. Use source_scope_version=2 and preserve its Source origins",
        409,
      );
    if (current.document_version !== input.expected_version)
      throw new ContextError(
        "document_conflict",
        "Document changed after it was read",
        409,
        {
          current_version: current.document_version,
          expected_version: input.expected_version,
        },
      );
    const refs = await this.sourceRefs(input.space_id, input.source_refs);
    return this.revise(
      input.space_id,
      input.document_id,
      input.expected_version,
      this.value(input, refs),
    );
  }

  async documentRestore(raw: unknown): Promise<Record<string, unknown>> {
    this.requireScope(true);
    const input = corpusDocumentRestoreSchema.parse(raw);
    await this.space(input.space_id);
    const previous = await this.snapshot(
      input.space_id,
      input.document_id,
      "previous",
    );
    if (previous.document_version !== input.expected_version)
      throw new ContextError(
        "document_conflict",
        "Document changed after it was read",
        409,
      );
    const storedRefs = JSON.parse(
      previous.source_refs_json,
    ) as StoredSourceRef[];
    const resolvedRefs = await this.sourceRefs(
      input.space_id,
      storedRefs.map(({ corpus_id: _corpus, ...ref }) => ref),
    );
    if (
      canonicalJson(resolvedRefs) !==
      canonicalJson(
        storedRefs.map((ref) => ({
          ...ref,
          source_space_id: ref.source_space_id ?? input.space_id,
        })),
      )
    )
      throw new ContextError(
        "source_connection_changed",
        "Historical Source binding changed; the previous snapshot was not reassigned",
        409,
      );
    return this.revise(
      input.space_id,
      input.document_id,
      input.expected_version,
      previous,
      previous.version,
    );
  }

  private async publicRefs(
    spaceId: string,
    refs: StoredSourceRef[],
    knownConnections?: Map<string, ConnectionRow>,
    includeUnavailableIdentity = false,
  ): Promise<Record<string, unknown>[]> {
    const bySpace = new Map<string, Map<string, ConnectionRow>>([
      [spaceId, knownConnections ?? (await this.connections(spaceId))],
    ]);
    for (const ref of refs)
      if (ref.source_space_id && !bySpace.has(ref.source_space_id))
        bySpace.set(
          ref.source_space_id,
          await this.connections(ref.source_space_id),
        );
    return refs.map((ref) => {
      const origin = ref.source_space_id ?? spaceId;
      const { corpus_id: _corpus, ...publicRef } = ref;
      if (
        bySpace.get(origin)?.get(ref.connection_id)?.corpus_id !== ref.corpus_id
      )
        return {
          ...(includeUnavailableIdentity
            ? { ...publicRef, source_space_id: origin }
            : { link_role: ref.link_role }),
          read_ref: null,
          availability: "unavailable",
          existence_checked: false,
          unavailable_reason: "source_connection_unavailable",
        };
      return {
        ...publicRef,
        source_space_id: origin,
        read_ref: readReference(
          origin,
          ref.connection_id,
          ref.corpus_id,
          ref.unit_id,
        ),
        availability: "not_checked",
        existence_checked: false,
      };
    });
  }

  async documentRead(raw: unknown): Promise<Record<string, unknown>> {
    this.requireScope();
    const input = corpusDocumentReadSchema.parse(raw);
    let location = { space_id: input.space_id, document_id: input.document_id };
    if (await managementSchemaReady(this.env.STATE_DB)) {
      const alias = await this.env.STATE_DB.prepare(
        `SELECT d.space_id,d.document_id FROM corpus_document_aliases a JOIN corpus_documents d
        ON d.owner_id=a.owner_id AND d.uid=a.document_uid WHERE a.owner_id=? AND a.space_id=? AND a.document_id=?`,
      )
        .bind(this.principal.ownerId, input.space_id, input.document_id)
        .first<typeof location>();
      if (alias) location = alias;
    }
    await this.space(location.space_id);
    const row = await this.snapshot(
      location.space_id,
      location.document_id,
      input.snapshot,
    );
    if (
      input.expected_version !== undefined &&
      row.version !== input.expected_version
    )
      throw new ContextError(
        "document_conflict",
        "Selected snapshot changed between pages",
        409,
        {
          current_version: row.version,
          expected_version: input.expected_version,
        },
      );
    const characters = Array.from(row.body_markdown);
    if (input.start_char > characters.length)
      throw new ContextError(
        "invalid_page",
        "start_char is beyond the selected document",
      );
    const body = characters
      .slice(input.start_char, input.start_char + input.max_chars)
      .join("");
    const returned = Array.from(body).length;
    const more = input.start_char + returned < characters.length;
    return boundedOutput({
      document: {
        ...location,
        uid: row.uid,
        source_scope_version: 2,
        snapshot: row.snapshot,
        version: row.version,
        ...(location.space_id !== input.space_id ||
        location.document_id !== input.document_id
          ? {
              relocated_from: {
                space_id: input.space_id,
                document_id: input.document_id,
              },
            }
          : {}),
        document_version: row.document_version,
        title: row.title,
        kind: row.kind,
        applicability: JSON.parse(row.applicability_json),
        guidance_approval: jsonOrNull(row.guidance_approval_json),
        provenance:
          row.kind === "guidance" ? "user_approved_guidance" : "native_context",
        source_refs: await this.publicRefs(
          location.space_id,
          JSON.parse(row.source_refs_json) as StoredSourceRef[],
        ),
        migration_provenance: jsonOrNull(row.migration_provenance_json),
        saved_at: row.saved_at,
        body_markdown: body,
        body_chars: characters.length,
      },
      start_char: input.start_char,
      returned_chars: returned,
      has_more: more,
      next_start_char: more ? input.start_char + returned : null,
      offset_unit: "unicode_code_point",
    });
  }

  async documentList(raw: unknown): Promise<Record<string, unknown>> {
    this.requireScope();
    const input = corpusDocumentListSchema.parse(raw);
    await this.space(input.space_id);
    const managed = await managementSchemaReady(this.env.STATE_DB);
    const filter =
      managed && input.lifecycle !== "all"
        ? `AND EXISTS (SELECT 1 FROM corpus_documents d
      WHERE d.owner_id=corpus_document_snapshots.owner_id AND d.space_id=corpus_document_snapshots.space_id
      AND d.document_id=corpus_document_snapshots.document_id AND d.trash_group_id IS ${input.lifecycle === "trash" ? "NOT " : ""}NULL)`
        : "";
    const rows = await this.env.STATE_DB.prepare(
      `SELECT document_id, version, title, kind, applicability_json, saved_at,
      length(CAST(body_markdown AS BLOB)) AS body_bytes
      ${
        managed
          ? `,(SELECT uid FROM corpus_documents d WHERE d.owner_id=corpus_document_snapshots.owner_id AND d.space_id=corpus_document_snapshots.space_id
        AND d.document_id=corpus_document_snapshots.document_id) AS uid,
        (SELECT trash_group_id FROM corpus_documents d WHERE d.owner_id=corpus_document_snapshots.owner_id AND d.space_id=corpus_document_snapshots.space_id
        AND d.document_id=corpus_document_snapshots.document_id) AS deletion_group_id`
          : ""
      } FROM corpus_document_snapshots
      WHERE owner_id=? AND space_id=? AND snapshot='current' ${input.kind ? "AND kind=?" : ""} ${filter}
      ORDER BY document_id LIMIT ? OFFSET ?`,
    )
      .bind(
        this.principal.ownerId,
        input.space_id,
        ...(input.kind ? [input.kind] : []),
        input.limit + 1,
        input.offset,
      )
      .all<{
        document_id: string;
        version: number;
        title: string;
        kind: string;
        applicability_json: string;
        saved_at: string;
        body_bytes: number;
      }>();
    return boundedOutput({
      space_id: input.space_id,
      ...page(
        rows.results.map(({ applicability_json, ...row }) => ({
          ...row,
          applicability: JSON.parse(applicability_json),
          document_ref: {
            space_id: input.space_id,
            document_id: row.document_id,
            snapshot: "current",
            expected_version: row.version,
          },
          provenance:
            row.kind === "guidance"
              ? "user_approved_guidance"
              : "native_context",
        })),
        input.limit,
        input.offset,
      ),
    });
  }

  async contextSearch(raw: unknown): Promise<Record<string, unknown>> {
    this.requireScope();
    const input = corpusContextSearchSchema.parse(raw);
    await this.space(input.space_id);
    const managed = await managementSchemaReady(this.env.STATE_DB);
    // Literal substring matching also supports Korean text without pretending
    // this small native-text query is semantic Source search or an FTS rank.
    const rows = await this.env.STATE_DB.prepare(
      `WITH matches AS (
      SELECT 'document' AS result_type, document_id AS id, version, title, kind,
        substr(body_markdown, max(1, instr(lower(body_markdown), lower(?)) - 80), 400) AS snippet,
        CASE WHEN instr(lower(title),lower(?))>0 THEN 0 ELSE 1 END AS title_match,
        (SELECT COALESCE(json_group_array(json(value)),'[]') FROM json_each(source_refs_json) WHERE key<5) AS source_refs_json,
        json_array_length(source_refs_json) AS ref_count
      FROM corpus_document_snapshots WHERE owner_id=? AND space_id=? AND snapshot='current'
        ${
          managed
            ? `AND EXISTS (SELECT 1 FROM corpus_documents d WHERE d.owner_id=corpus_document_snapshots.owner_id
          AND d.space_id=corpus_document_snapshots.space_id AND d.document_id=corpus_document_snapshots.document_id AND d.trash_group_id IS NULL)`
            : ""
        }
        AND (instr(lower(title),lower(?))>0 OR instr(lower(body_markdown),lower(?))>0)
      UNION ALL
      SELECT 'context_item', item.item_id, context.version, '', item.kind,
        substr(item.body_text,max(1,instr(lower(item.body_text),lower(?))-80),400), 1, '[]', 0
      FROM corpus_context_items AS item JOIN corpus_contexts AS context
        ON context.owner_id=item.owner_id AND context.space_id=item.space_id
      WHERE item.owner_id=? AND item.space_id=? AND item.lifecycle_state='active'
        AND instr(lower(item.body_text),lower(?))>0
    ) SELECT result_type,id,version,title,kind,snippet,source_refs_json,ref_count FROM matches ORDER BY title_match,result_type,id LIMIT ? OFFSET ?`,
    )
      .bind(
        input.query,
        input.query,
        this.principal.ownerId,
        input.space_id,
        input.query,
        input.query,
        input.query,
        this.principal.ownerId,
        input.space_id,
        input.query,
        input.limit + 1,
        input.offset,
      )
      .all<{
        result_type: string;
        id: string;
        version: number;
        title: string;
        kind: string;
        snippet: string;
        source_refs_json: string;
        ref_count: number;
      }>();
    const selected = rows.results.slice(0, input.limit);
    const itemIds = selected
      .filter((row) => row.result_type === "context_item")
      .map((row) => row.id);
    const linksByItem = new Map<string, ContextSourceRow[]>();
    if (itemIds.length) {
      const links = await this.env.STATE_DB.prepare(
        `WITH ranked AS (
        SELECT source.item_id,source.source_ref_id,source.corpus_id,source.document_id,source.revision_id,source.projection_id,
          ${managed ? "source.source_space_id,source.source_connection_id," : ""}
          source.source_unit_id,source.link_role, CASE WHEN source.provider_kind IS NOT NULL OR source.provider_record_id IS NOT NULL THEN 1 ELSE 0 END AS is_provider,
          row_number() OVER (PARTITION BY source.item_id ORDER BY source.source_ref_id) AS position,
          count(*) OVER (PARTITION BY source.item_id) AS ref_count
        FROM corpus_context_sources AS source JOIN json_each(?) AS wanted ON wanted.value=source.item_id
        WHERE source.owner_id=?
      ) SELECT * FROM ranked WHERE position<=5 ORDER BY item_id,source_ref_id`,
      )
        .bind(canonicalJson(itemIds), this.principal.ownerId)
        .all<ContextSourceRow>();
      for (const link of links.results)
        linksByItem.set(link.item_id, [
          ...(linksByItem.get(link.item_id) ?? []),
          link,
        ]);
    }
    const connections = await this.connections(input.space_id);
    const items = await Promise.all(
      selected.map(async ({ source_refs_json, ref_count, ...row }) => {
        let sourceRefs: Record<string, unknown>[];
        let count = ref_count;
        if (row.result_type === "document") {
          sourceRefs = await this.publicRefs(
            input.space_id,
            JSON.parse(source_refs_json) as StoredSourceRef[],
            connections,
          );
        } else {
          const links = linksByItem.get(row.id) ?? [];
          count = links[0]?.ref_count ?? 0;
          sourceRefs = await Promise.all(
            links.map(async (link) => {
              const origin = link.source_space_id ?? input.space_id;
              const originConnections =
                origin === input.space_id
                  ? connections
                  : await this.connections(origin);
              const connection = link.source_connection_id
                ? originConnections.get(link.source_connection_id)
                : [...originConnections.values()].find(
                    (candidate) => candidate.corpus_id === link.corpus_id,
                  );
              const identity = {
                source_ref_id: link.source_ref_id,
                link_role: link.link_role,
                existence_checked: false,
              };
              if (!connection || link.is_provider)
                return {
                  ...identity,
                  read_ref: null,
                  availability: "unavailable",
                  unavailable_reason: link.is_provider
                    ? "provider_read_not_supported"
                    : "source_connection_unavailable",
                };
              return {
                ...identity,
                source_space_id: origin,
                connection_id: connection.connection_id,
                document_id: link.document_id,
                revision_id: link.revision_id,
                projection_id: link.projection_id,
                unit_id: link.source_unit_id,
                read_ref: link.source_unit_id
                  ? readReference(
                      origin,
                      connection.connection_id,
                      connection.corpus_id,
                      link.source_unit_id,
                    )
                  : null,
                availability: link.source_unit_id
                  ? "not_checked"
                  : "unavailable",
                ...(link.source_unit_id
                  ? {}
                  : { unavailable_reason: "source_unit_id_missing" }),
              };
            }),
          );
        }
        return {
          ...row,
          canonical: true,
          snippet_is_excerpt: true,
          provenance:
            row.result_type === "document"
              ? row.kind === "guidance"
                ? "user_approved_guidance"
                : "native_context"
              : "stored_context_item",
          source_refs: sourceRefs,
          source_refs_count: count,
          source_refs_limit: 5,
          source_refs_has_more: count > sourceRefs.length,
          ...(row.result_type === "document"
            ? {
                document_ref: {
                  space_id: input.space_id,
                  document_id: row.id,
                  snapshot: "current",
                  expected_version: row.version,
                },
              }
            : {
                context_version: row.version,
                context_item_ref: {
                  space_id: input.space_id,
                  item_id: row.id,
                  expected_version: row.version,
                },
              }),
        };
      }),
    );
    // This is a bounded relationship map over metadata already on this page,
    // not a semantic recommendation or an excuse to fetch Source/Skill bodies.
    const sourceKeys = items.map((item) =>
      item.source_refs.flatMap((ref) => {
        const fields = [
          ref.source_space_id,
          ref.connection_id,
          ref.document_id,
          ref.revision_id,
          ref.projection_id,
        ];
        return ref.availability !== "unavailable" &&
          fields.every((value) => typeof value === "string" && value)
          ? [
              {
                key: canonicalJson(fields),
                ref: {
                  connection_id: ref.connection_id,
                  document_id: ref.document_id,
                  revision_id: ref.revision_id,
                  projection_id: ref.projection_id,
                },
              },
            ]
          : [];
      }),
    );
    const relatedItems = items.map((item, index) => {
      const matches = items.flatMap((candidate, candidateIndex) => {
        if (index === candidateIndex) return [];
        const shared = sourceKeys[index]!.find((source) =>
          sourceKeys[candidateIndex]!.some((other) => source.key === other.key),
        );
        if (!shared) return [];
        return [
          {
            result_type: candidate.result_type,
            id: candidate.id,
            version: candidate.version,
            relation: "shared_source_projection",
            shared_source_ref: shared.ref,
            ...("document_ref" in candidate
              ? { document_ref: candidate.document_ref }
              : { context_item_ref: candidate.context_item_ref }),
          },
        ];
      });
      return {
        ...item,
        related_candidates: matches.slice(0, 3),
        related_candidates_count: matches.length,
        related_candidates_limit: 3,
        related_candidates_has_more: matches.length > 3,
        related_candidates_scope: "returned_page_visible_source_refs",
      };
    });
    const hasMore = rows.results.length > input.limit;
    return boundedOutput({
      space_id: input.space_id,
      query: input.query,
      match_mode: "literal_substring",
      items: relatedItems,
      limit: input.limit,
      offset: input.offset,
      returned_count: items.length,
      has_more: hasMore,
      next_offset: hasMore ? input.offset + items.length : null,
    });
  }
}
