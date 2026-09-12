import { z } from "zod/v4";
import {
  defineOperation,
  executeOperation,
  operationCapabilities,
  purgeAfter,
  type OperationActor,
  type OperationDefinition,
} from "@personal-agent/remote-runtime";
import { d1Batch, d1Guard } from "@personal-agent/remote-runtime/d1";
import { sha256 } from "@personal-agent/immutable-assets";
import { indexExistingIssues, libraryManaged } from "./assets";
import { LibraryError } from "./errors";
const requestKey = z.string().min(16).max(160),
  id = z.string().min(1).max(200);
const groupInput = {
  deletion_group_id: id,
  expected_version: z.number().int().positive(),
  impact_token: z.string().length(64),
  idempotency_key: requestKey,
};
export const libraryPreviewSchema = z
  .object({
    action: z.enum(["trash", "restore", "purge"]),
    issue_id: id.optional(),
    deletion_group_id: id.optional(),
  })
  .strict();
export const libraryTrashSchema = z
  .object({
    issue_id: id,
    expected_version: z.number().int().positive(),
    impact_token: z.string().length(64),
    idempotency_key: requestKey,
  })
  .strict();
export const libraryRestoreSchema = z.object(groupInput).strict();
export const libraryPurgeSchema = z
  .object({ ...groupInput, confirm_permanent_delete: z.literal(true) })
  .strict();
export const libraryTrashListSchema = z
  .object({
    state: z.enum(["pending", "all"]).default("pending"),
    limit: z.number().int().min(1).max(100).default(25),
    offset: z.number().int().nonnegative().default(0),
  })
  .strict();
type Group = {
  deletion_group_id: string;
  issue_id: string;
  version: number;
  state: string;
  trashed_at: string;
  purge_after: string;
  updated_at: string;
  blockers_json: string;
};
type Issue = {
  id: string;
  version: number;
  trash_group_id: string | null;
  canonical_path: string;
};
type Asset = {
  object_key: string;
  state: string;
  owned: number;
  uploads: number;
  shared: number;
};
const hash = (value: unknown) =>
  sha256(new TextEncoder().encode(JSON.stringify(value)));
export class LibraryManagementService {
  constructor(
    private db: D1Database,
    private media: R2Bucket,
    readonly enabled = false,
    private clock = () => new Date().toISOString(),
  ) {}
  private async ready(write = false) {
    if (!(await libraryManaged(this.db)) || (write && !this.enabled))
      throw new LibraryError(
        "management_not_enabled",
        "Library management is not enabled",
        503,
      );
  }
  private async group(id: string) {
    const row = await this.db
      .prepare("SELECT * FROM library_trash_groups WHERE deletion_group_id=?")
      .bind(id)
      .first<Group>();
    if (!row)
      throw new LibraryError(
        "group_not_found",
        "Deletion group was not found",
        404,
      );
    return row;
  }
  private async issue(id: string) {
    const row = await this.db
      .prepare(
        "SELECT id,version,trash_group_id,canonical_path FROM documents WHERE id=?",
      )
      .bind(id)
      .first<Issue>();
    if (!row) throw new LibraryError("not_found", "Issue was not found", 404);
    return row;
  }
  private async replay(input: { idempotency_key: string }, operation: string) {
    const fingerprint = await hash({ operation, input });
    const row = await this.db
      .prepare("SELECT * FROM library_operation_receipts WHERE request_key=?")
      .bind(input.idempotency_key)
      .first<{ fingerprint: string; result_json: string }>();
    if (row && row.fingerprint !== fingerprint)
      throw new LibraryError(
        "request_key_conflict",
        "Request key was already used",
        409,
      );
    return { fingerprint, result: row ? JSON.parse(row.result_json) : null };
  }
  private receipt(key: string, fingerprint: string, result: unknown) {
    return this.db
      .prepare("INSERT INTO library_operation_receipts VALUES(?,?,?)")
      .bind(key, fingerprint, JSON.stringify(result));
  }
  private async assets(issueId: string) {
    return (
      await this.db
        .prepare(
          `SELECT a.object_key,a.state,a.owned,
 (SELECT count(*) FROM library_asset_uploads u WHERE u.object_key=a.object_key) AS uploads,
 (SELECT count(*) FROM library_asset_refs x WHERE x.object_key=a.object_key AND x.issue_id<>?) AS shared
 FROM library_assets a JOIN library_asset_refs r ON r.object_key=a.object_key WHERE r.issue_id=? ORDER BY a.object_key`,
        )
        .bind(issueId, issueId)
        .all<Asset>()
    ).results;
  }
  private async facts(issue: Issue) {
    const assets = await this.assets(issue.id),
      owned = assets.filter((a) => !a.shared),
      blockers: Array<{ code: string; message: string }> = [];
    const incoming = await this.db
      .prepare(
        "SELECT count(*) AS n FROM documents d JOIN json_each(d.references_json) r WHERE d.id<>? AND r.value=?",
      )
      .bind(issue.id, issue.canonical_path)
      .first<{ n: number }>();
    if (incoming?.n)
      blockers.push({
        code: "external_reference",
        message:
          "Another edition retains a structured reference to this edition.",
      });
    if (owned.length) {
      const incomplete = await this.db
        .prepare(
          "SELECT count(*) AS n FROM documents d WHERE NOT EXISTS(SELECT 1 FROM library_asset_inventory i WHERE i.issue_id=d.id AND i.version=d.version AND i.complete=1)",
        )
        .first<{ n: number }>();
      if (incomplete?.n)
        blockers.push({
          code: "asset_inventory_incomplete",
          message:
            "Supported local asset references have not been completely inventoried.",
        });
    }
    if (owned.some((a) => !a.owned))
      blockers.push({
        code: "asset_ownership_unconfirmed",
        message:
          "A legacy object has no confirmed product ownership; it is retained.",
      });
    if (owned.some((a) => a.uploads))
      blockers.push({
        code: "asset_upload_pending",
        message: "An asset upload outcome has not been confirmed.",
      });
    return { assets, owned, blockers };
  }
  async preview(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const input = libraryPreviewSchema.parse(raw);
    await indexExistingIssues(this.db);
    const group = input.deletion_group_id
      ? await this.group(input.deletion_group_id)
      : null;
    if (
      (input.action !== "trash" && !group) ||
      (input.action === "trash" && !input.issue_id)
    )
      throw new LibraryError(
        "invalid_target",
        "Select the issue or deletion group",
      );
    const issue = await this.issue(group?.issue_id ?? input.issue_id!);
    const facts = await this.facts(issue);
    const value = {
      action: input.action,
      issue,
      group: group
        ? {
            id: group.deletion_group_id,
            version: group.version,
            state: group.state,
          }
        : null,
      ...facts,
    };
    return {
      ...value,
      impact_token: await hash(value),
      members: [{ kind: "issue", id: issue.id, version: issue.version }],
      deletion_group_id: group?.deletion_group_id,
      expected_version: group?.version ?? issue.version,
    };
  }
  async trash(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = libraryTrashSchema.parse(raw),
      replay = await this.replay(input, "trash");
    if (replay.result) return replay.result;
    const preview = await this.preview({
        action: "trash",
        issue_id: input.issue_id,
      }),
      issue = await this.issue(input.issue_id);
    if (
      preview.impact_token !== input.impact_token ||
      issue.version !== input.expected_version ||
      issue.trash_group_id
    )
      throw new LibraryError(
        "impact_conflict",
        "The reviewed issue changed",
        409,
      );
    const now = this.clock(),
      group = crypto.randomUUID(),
      result = {
        state: "trashed",
        deletion_group_id: group,
        version: 1,
        trashed_at: now,
        purge_after: purgeAfter(now),
      };
    await d1Batch(
      this.db,
      [
        d1Guard(
          this.db,
          "EXISTS(SELECT 1 FROM documents WHERE id=? AND version=? AND trash_group_id IS NULL)",
          [issue.id, issue.version],
        ),
      ],
      [
        this.db
          .prepare(
            "INSERT INTO library_trash_groups VALUES(?,?,1,'trashed',?,?,?,'[]')",
          )
          .bind(group, issue.id, now, result.purge_after, now),
        this.db
          .prepare(
            "UPDATE documents SET trash_group_id=?,version=version+1 WHERE id=?",
          )
          .bind(group, issue.id),
        this.db
          .prepare(
            "UPDATE library_asset_inventory SET version=version+1 WHERE issue_id=?",
          )
          .bind(issue.id),
        this.receipt(input.idempotency_key, replay.fingerprint, result),
      ],
    );
    return result;
  }
  async restore(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = libraryRestoreSchema.parse(raw),
      replay = await this.replay(input, "restore");
    if (replay.result) return replay.result;
    const preview = await this.preview({
        action: "restore",
        deletion_group_id: input.deletion_group_id,
      }),
      group = await this.group(input.deletion_group_id);
    if (
      preview.impact_token !== input.impact_token ||
      group.version !== input.expected_version
    )
      throw new LibraryError("impact_conflict", "Restore impact changed", 409);
    const result = {
      state: "restored",
      deletion_group_id: group.deletion_group_id,
      version: group.version + 1,
    };
    await d1Batch(
      this.db,
      [
        d1Guard(
          this.db,
          "EXISTS(SELECT 1 FROM library_trash_groups WHERE deletion_group_id=? AND version=? AND state IN ('trashed','blocked'))",
          [group.deletion_group_id, group.version],
        ),
        d1Guard(
          this.db,
          "EXISTS(SELECT 1 FROM documents WHERE id=? AND trash_group_id=?)",
          [group.issue_id, group.deletion_group_id],
        ),
      ],
      [
        this.db
          .prepare(
            "UPDATE documents SET trash_group_id=NULL,version=version+1 WHERE id=?",
          )
          .bind(group.issue_id),
        this.db
          .prepare(
            "UPDATE library_asset_inventory SET version=version+1 WHERE issue_id=?",
          )
          .bind(group.issue_id),
        this.db
          .prepare(
            "UPDATE library_trash_groups SET state='restored',version=version+1,updated_at=? WHERE deletion_group_id=?",
          )
          .bind(this.clock(), group.deletion_group_id),
        this.receipt(input.idempotency_key, replay.fingerprint, result),
      ],
    );
    return result;
  }
  async purge(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = libraryPurgeSchema.parse(raw),
      replay = await this.replay(input, "purge");
    if (replay.result) return this.purgeBatch(input.deletion_group_id);
    const preview = await this.preview({
        action: "purge",
        deletion_group_id: input.deletion_group_id,
      }),
      group = await this.group(input.deletion_group_id);
    if (
      preview.impact_token !== input.impact_token ||
      group.version !== input.expected_version
    )
      throw new LibraryError("impact_conflict", "Deletion impact changed", 409);
    if (group.state === "purging") {
      await d1Batch(
        this.db,
        [
          d1Guard(
            this.db,
            "EXISTS(SELECT 1 FROM library_trash_groups WHERE deletion_group_id=? AND state='purging')",
            [group.deletion_group_id],
          ),
        ],
        [
          this.receipt(input.idempotency_key, replay.fingerprint, {
            state: "purging",
            deletion_group_id: group.deletion_group_id,
          }),
        ],
      );
      return this.purgeBatch(group.deletion_group_id);
    }
    return this.claim(group, input.idempotency_key, replay.fingerprint);
  }
  private async claim(group: Group, key?: string, fingerprint?: string) {
    const issue = await this.issue(group.issue_id),
      facts = await this.facts(issue);
    if (facts.blockers.length) {
      await this.db
        .prepare(
          "UPDATE library_trash_groups SET state='blocked',blockers_json=?,updated_at=? WHERE deletion_group_id=? AND state IN ('trashed','blocked')",
        )
        .bind(
          JSON.stringify(facts.blockers),
          this.clock(),
          group.deletion_group_id,
        )
        .run();
      return {
        state: "blocked",
        deletion_group_id: group.deletion_group_id,
        blockers: facts.blockers,
      };
    }
    const pending = {
      state: "purging",
      deletion_group_id: group.deletion_group_id,
      restorable: false,
    };
    await d1Batch(
      this.db,
      [
        d1Guard(
          this.db,
          "EXISTS(SELECT 1 FROM library_trash_groups WHERE deletion_group_id=? AND version=? AND state IN ('trashed','blocked'))",
          [group.deletion_group_id, group.version],
        ),
        d1Guard(
          this.db,
          "EXISTS(SELECT 1 FROM documents WHERE id=? AND version=? AND trash_group_id=?)",
          [issue.id, issue.version, group.deletion_group_id],
        ),
        d1Guard(
          this.db,
          "NOT EXISTS(SELECT 1 FROM documents d JOIN json_each(d.references_json) r WHERE d.id<>? AND r.value=?)",
          [issue.id, issue.canonical_path],
        ),
        ...(facts.owned.length
          ? [
              d1Guard(
                this.db,
                "NOT EXISTS(SELECT 1 FROM documents d WHERE NOT EXISTS(SELECT 1 FROM library_asset_inventory i WHERE i.issue_id=d.id AND i.version=d.version AND i.complete=1))",
              ),
            ]
          : []),
        d1Guard(
          this.db,
          `NOT EXISTS(SELECT 1 FROM json_each(?) a WHERE
          NOT EXISTS(SELECT 1 FROM library_assets WHERE object_key=a.value AND owned=1 AND state='live')
          OR EXISTS(SELECT 1 FROM library_asset_uploads WHERE object_key=a.value)
          OR EXISTS(SELECT 1 FROM library_asset_refs WHERE object_key=a.value AND issue_id<>?))`,
          [JSON.stringify(facts.owned.map((a) => a.object_key)), issue.id],
        ),
      ],
      [
        this.db
          .prepare(
            "UPDATE library_trash_groups SET state='purging',version=version+1,updated_at=?,blockers_json='[]' WHERE deletion_group_id=?",
          )
          .bind(this.clock(), group.deletion_group_id),
        this.db
          .prepare(
            "UPDATE library_assets SET state='purging' WHERE object_key IN(SELECT value FROM json_each(?))",
          )
          .bind(JSON.stringify(facts.owned.map((a) => a.object_key))),
        this.db
          .prepare(
            "INSERT INTO library_trash_assets(deletion_group_id,object_key) SELECT ?,value FROM json_each(?)",
          )
          .bind(
            group.deletion_group_id,
            JSON.stringify(facts.owned.map((a) => a.object_key)),
          ),
        ...(key ? [this.receipt(key, fingerprint!, pending)] : []),
      ],
    );
    return this.purgeBatch(group.deletion_group_id);
  }
  private async purgeBatch(id: string): Promise<Record<string, unknown>> {
    const group = await this.group(id);
    if (group.state === "purged")
      return { state: "purged", deletion_group_id: id, restorable: false };
    if (group.state !== "purging")
      throw new LibraryError(
        "purge_conflict",
        "Deletion is not in progress",
        409,
      );
    await this.db
      .prepare(
        "UPDATE library_trash_groups SET updated_at=? WHERE deletion_group_id=? AND state='purging'",
      )
      .bind(this.clock(), id)
      .run();
    const assets = await this.db
      .prepare(
        "SELECT object_key FROM library_trash_assets WHERE deletion_group_id=? AND state='pending' ORDER BY object_key LIMIT 20",
      )
      .bind(id)
      .all<{ object_key: string }>();
    if (assets.results.length) {
      const keys = assets.results.map(asset => asset.object_key);
      try {
        // A failed bulk request may have deleted only part of the batch. Keep all
        // keys pending and retry the immutable keys; R2 delete is idempotent.
        await this.media.delete(keys);
      } catch {
        await this.db.prepare("UPDATE library_trash_groups SET blockers_json=?,updated_at=? WHERE deletion_group_id=?")
          .bind(JSON.stringify([{code:"asset_delete_retry",message:"Object deletion did not finish; retry will resume it."}]),this.clock(),id).run();
        return {state:"purging",deletion_group_id:id,restorable:false,reason:"asset_delete_retry"};
      }
      await d1Batch(this.db,[],[
        this.db.prepare("UPDATE library_trash_assets SET state='deleted' WHERE deletion_group_id=? AND object_key IN(SELECT value FROM json_each(?))").bind(id,JSON.stringify(keys)),
        this.db.prepare("UPDATE library_assets SET state='purged' WHERE object_key IN(SELECT value FROM json_each(?))").bind(JSON.stringify(keys)),
      ]);
    }

    const remaining = await this.db
      .prepare(
        "SELECT 1 FROM library_trash_assets WHERE deletion_group_id=? AND state='pending'",
      )
      .bind(id)
      .first();
    if (remaining)
      return { state: "purging", deletion_group_id: id, restorable: false };
    const result = {
      state: "purged",
      deletion_group_id: id,
      restorable: false,
    };
    await d1Batch(
      this.db,
      [
        d1Guard(
          this.db,
          "EXISTS(SELECT 1 FROM library_trash_groups WHERE deletion_group_id=? AND state='purging')",
          [id],
        ),
      ],
      [
        this.db
          .prepare("DELETE FROM documents WHERE id=? AND trash_group_id=?")
          .bind(group.issue_id, id),
        this.db
          .prepare(
            "UPDATE library_trash_groups SET state='purged',updated_at=?,blockers_json='[]' WHERE deletion_group_id=?",
          )
          .bind(this.clock(), id),
      ],
    );
    return result;
  }
  async trashList(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const input = libraryTrashListSchema.parse(raw);
    const rows = await this.db
      .prepare(
        `SELECT * FROM library_trash_groups ${input.state === "pending" ? "WHERE state IN ('trashed','blocked','purging')" : ""} ORDER BY trashed_at DESC,deletion_group_id LIMIT ? OFFSET ?`,
      )
      .bind(input.limit + 1, input.offset)
      .all<Group>();
    return {
      items: rows.results.slice(0, input.limit).map((g) => ({
        ...g,
        blockers: JSON.parse(g.blockers_json),
        restorable: ["trashed", "blocked"].includes(g.state),
      })),
      next_offset:
        rows.results.length > input.limit ? input.offset + input.limit : null,
      management_enabled: this.enabled,
      maintenance: await this.db
        .prepare("SELECT * FROM library_maintenance_runs WHERE run_id='trash'")
        .first(),
    };
  }
  async status(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const { idempotency_key } = z
      .object({ idempotency_key: requestKey })
      .strict()
      .parse(raw);
    const row = await this.db
      .prepare(
        "SELECT result_json FROM library_operation_receipts WHERE request_key=?",
      )
      .bind(idempotency_key)
      .first<{ result_json: string }>();
    if (!row) return { found: false, state: "not_confirmed" };
    const result = JSON.parse(row.result_json);
    return {
      found: true,
      result,
      ...(result.deletion_group_id
        ? { current: await this.group(result.deletion_group_id) }
        : {}),
    };
  }
  async auditDue(id: string): Promise<Record<string, unknown>> {
    const group = await this.group(id);
    const facts = await this.facts(await this.issue(group.issue_id));
    return {
      state: "dry_run",
      deletion_group_id: id,
      purge_after: group.purge_after,
      blockers: facts.blockers,
    };
  }
  async purgeDue(id: string): Promise<Record<string, unknown>> {
    const group = await this.group(id);
    if (
      !["trashed", "blocked", "purging"].includes(group.state) ||
      group.purge_after > this.clock()
    )
      return { state: "not_due" };
    if (group.state === "purging") return this.purgeBatch(id);
    await indexExistingIssues(this.db);
    return this.claim(group);
  }
}
export function libraryManagementOperations(
  service: LibraryManagementService,
  actor: OperationActor,
) {
  const op = (
    schema: z.ZodObject,
    effects: "read" | "trash" | "restore" | "purge",
    run: (raw: unknown) => Promise<Record<string, unknown>>,
  ) =>
    defineOperation({
      schema,
      outputSchema: z.looseObject({
        state: z.string().optional(),
        deletion_group_id: z.string().optional(),
        expected_version: z.number().optional(),
        impact_token: z.string().optional(),
        items: z.array(z.unknown()).optional(),
        operations: z.record(z.string(), z.unknown()).optional(),
      }),
      scope: effects === "read" ? "library.read" : "library.write",
      description:
        "Manage Library deletion groups, preserving publication identity and shared assets.",
      effects,
      retry: effects === "read" ? "read" : "request_key",
      enabled: effects === "read" || service.enabled,
      surfaces: ["mcp", "http"],
      run,
    });
  const operations: Record<string, OperationDefinition<z.ZodObject>> = {
    library_management_preview: op(libraryPreviewSchema, "read", (raw) =>
      service.preview(raw),
    ),
    library_issue_trash: op(libraryTrashSchema, "trash", (raw) =>
      service.trash(raw),
    ),
    library_trash_list: op(libraryTrashListSchema, "read", (raw) =>
      service.trashList(raw),
    ),
    library_trash_restore: op(libraryRestoreSchema, "restore", (raw) =>
      service.restore(raw),
    ),
    library_trash_purge: op(libraryPurgeSchema, "purge", (raw) =>
      service.purge(raw),
    ),
    library_operation_status: op(
      z.object({ idempotency_key: requestKey }).strict(),
      "read",
      (raw) => service.status(raw),
    ),
  };
  operations.library_capabilities = op(
    z.object({}).strict(),
    "read",
    async () => ({ operations: operationCapabilities(actor, operations) }),
  );
  return operations;
}
export async function executeLibraryManagement(
  service: LibraryManagementService,
  actor: OperationActor,
  name: string,
  raw: unknown,
) {
  const op = libraryManagementOperations(service, actor)[name];
  if (!op)
    throw new LibraryError(
      "operation_not_found",
      "Library operation was not found",
      404,
    );
  return executeOperation(actor, op, raw);
}
