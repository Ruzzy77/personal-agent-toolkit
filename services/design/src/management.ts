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
import { designManaged } from "./assets";
import { DesignError } from "./errors";
const id = z.string().min(1).max(200),
  path = z.string().min(1).max(500),
  key = z.string().min(16).max(160);
const common = {
  expected_version: z.number().int().positive(),
  impact_token: z.string().length(64),
  idempotency_key: key,
};
export const designPreviewSchema = z
  .object({
    action: z.enum(["trash", "restore", "purge"]),
    kind: z.enum(["recipe", "file"]).optional(),
    recipe_id: id.optional(),
    path: path.optional(),
    deletion_group_id: id.optional(),
  })
  .strict();
export const designRecipeTrashSchema = z
  .object({ recipe_id: id, ...common })
  .strict();
export const designFileTrashSchema = z
  .object({ recipe_id: id, path, ...common })
  .strict();
export const designRestoreSchema = z
  .object({ deletion_group_id: id, ...common })
  .strict();
export const designPurgeSchema = designRestoreSchema.extend({
  confirm_permanent_delete: z.literal(true),
});
export const designTrashListSchema = z
  .object({
    state: z.enum(["pending", "all"]).default("pending"),
    limit: z.number().int().min(1).max(100).default(25),
    offset: z.number().int().nonnegative().default(0),
  })
  .strict();
type Group = {
  deletion_group_id: string;
  root_kind: "recipe" | "file";
  recipe_id: string;
  path: string | null;
  version: number;
  state: string;
  trashed_at: string;
  purge_after: string;
  updated_at: string;
  blockers_json: string;
};
type Recipe = { id: string; revision: number; trash_group_id: string | null };
type Member = { kind: "recipe" | "file"; path: string; revision: number };
type Asset = {
  object_key: string;
  state: string;
  owned: number;
  uploads: number;
  shared: number;
};
const hash = (v: unknown) =>
  sha256(new TextEncoder().encode(JSON.stringify(v)));
export class DesignManagementService {
  constructor(
    private db: D1Database,
    private assets: R2Bucket,
    readonly enabled = false,
    private clock = () => new Date().toISOString(),
  ) {}
  private async ready(write = false) {
    if (!(await designManaged(this.db)) || (write && !this.enabled))
      throw new DesignError(
        "management_not_enabled",
        "Design management is not enabled",
        503,
      );
  }
  private async group(id: string) {
    const row = await this.db
      .prepare("SELECT * FROM design_trash_groups WHERE deletion_group_id=?")
      .bind(id)
      .first<Group>();
    if (!row)
      throw new DesignError(
        "group_not_found",
        "Deletion group was not found",
        404,
      );
    return row;
  }
  private async recipe(id: string) {
    const row = await this.db
      .prepare(
        "SELECT id,revision,trash_group_id FROM design_recipes WHERE id=?",
      )
      .bind(id)
      .first<Recipe>();
    if (!row) throw new DesignError("not_found", "Recipe was not found", 404);
    return row;
  }
  private async replay(input: { idempotency_key: string }, operation: string) {
    const fingerprint = await hash({ operation, input });
    const row = await this.db
      .prepare("SELECT * FROM design_operation_receipts WHERE request_key=?")
      .bind(input.idempotency_key)
      .first<{ fingerprint: string; result_json: string }>();
    if (row && row.fingerprint !== fingerprint)
      throw new DesignError(
        "request_key_conflict",
        "Request key was already used",
        409,
      );
    return { fingerprint, result: row ? JSON.parse(row.result_json) : null };
  }
  private receipt(key: string, fingerprint: string, result: unknown) {
    return this.db
      .prepare("INSERT INTO design_operation_receipts VALUES(?,?,?)")
      .bind(key, fingerprint, JSON.stringify(result));
  }
  private async members(
    recipe: Recipe,
    kind: "recipe" | "file",
    path?: string,
    group?: Group,
  ): Promise<Member[]> {
    if (group)
      return (
        await this.db
          .prepare(
            "SELECT kind,path,revision FROM design_trash_members WHERE deletion_group_id=? ORDER BY kind,path",
          )
          .bind(group.deletion_group_id)
          .all<Member>()
      ).results;
    if (recipe.trash_group_id)
      throw new DesignError(
        "already_trashed",
        "Recipe is already in the trash",
        409,
      );
    const files = await this.db
      .prepare(
        `SELECT 'file' AS kind,path,revision FROM design_files WHERE recipe_id=? AND trash_group_id IS NULL ${kind === "file" ? "AND path=?" : ""} ORDER BY path`,
      )
      .bind(recipe.id, ...(kind === "file" ? [path] : []))
      .all<Member>();
    if (kind === "file" && !files.results.length)
      throw new DesignError("not_found", "Current file was not found", 404);
    return [
      ...(kind === "recipe"
        ? [{ kind: "recipe" as const, path: "", revision: recipe.revision }]
        : []),
      ...files.results,
    ];
  }
  private async facts(recipe: Recipe, members: Member[], group?: Group) {
    const paths = members.filter((m) => m.kind === "file").map((m) => m.path),
      assets = (
        await this.db
          .prepare(
            `SELECT DISTINCT a.object_key,a.state,a.owned,
 (SELECT count(*) FROM design_asset_uploads u WHERE u.object_key=a.object_key) AS uploads,
 (SELECT count(*) FROM design_files x WHERE x.object_key=a.object_key AND (x.recipe_id<>? OR x.path NOT IN(SELECT value FROM json_each(?)))) AS shared
 FROM design_assets a JOIN design_files f ON f.object_key=a.object_key WHERE f.recipe_id=? AND f.path IN(SELECT value FROM json_each(?)) ORDER BY a.object_key`,
          )
          .bind(
            recipe.id,
            JSON.stringify(paths),
            recipe.id,
            JSON.stringify(paths),
          )
          .all<Asset>()
      ).results;
    const owned = assets.filter((a) => !a.shared),
      blockers: Array<{ code: string; message: string }> = [];
    if (owned.some((a) => a.uploads))
      blockers.push({
        code: "asset_upload_pending",
        message: "An upload has not confirmed its outcome.",
      });
    if (owned.some((a) => !a.owned))
      blockers.push({
        code: "asset_ownership_unconfirmed",
        message: "Object ownership is not confirmed.",
      });
    if (
      group?.root_kind === "recipe" &&
      (await this.db
        .prepare(
          "SELECT 1 FROM design_trash_groups WHERE recipe_id=? AND deletion_group_id<>? AND state NOT IN ('purged','restored')",
        )
        .bind(recipe.id, group.deletion_group_id)
        .first())
    )
      blockers.push({
        code: "independent_deletion_group",
        message: "Earlier file deletion groups retain their own deadlines.",
      });
    return { assets, owned, blockers };
  }
  async preview(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const input = designPreviewSchema.parse(raw);
    const group = input.deletion_group_id
      ? await this.group(input.deletion_group_id)
      : undefined;
    if (
      (input.action !== "trash" && !group) ||
      (input.action === "trash" && (!input.recipe_id || !input.kind)) ||
      (input.kind === "file" && !input.path)
    )
      throw new DesignError(
        "invalid_target",
        "Select a recipe, file or deletion group",
      );
    const recipe = await this.recipe(group?.recipe_id ?? input.recipe_id!),
      members = await this.members(
        recipe,
        group?.root_kind ?? input.kind!,
        group?.path ?? input.path,
        group,
      ),
      facts = await this.facts(recipe, members, group);
    const value = {
      action: input.action,
      recipe,
      members,
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
      expected_version:
        group?.version ??
        (input.kind === "file" ? members[0]!.revision : recipe.revision),
      impact_token: await hash(value),
      deletion_group_id: group?.deletion_group_id,
    };
  }
  private memberGuards(recipe: Recipe, members: Member[], group?: string) {
    return [
      d1Guard(
        this.db,
        "EXISTS(SELECT 1 FROM design_recipes WHERE id=? AND revision=?)",
        [recipe.id, recipe.revision],
      ),
      d1Guard(
        this.db,
        `NOT EXISTS(SELECT 1 FROM json_each(?) m WHERE
        (json_extract(m.value,'$.kind')='recipe' AND NOT EXISTS(
          SELECT 1 FROM design_recipes WHERE id=? AND revision=json_extract(m.value,'$.revision') AND trash_group_id IS ?)) OR
        (json_extract(m.value,'$.kind')='file' AND NOT EXISTS(
          SELECT 1 FROM design_files WHERE recipe_id=? AND path=json_extract(m.value,'$.path') AND revision=json_extract(m.value,'$.revision') AND trash_group_id IS ?)))`,
        [
          JSON.stringify(members),
          recipe.id,
          group ?? null,
          recipe.id,
          group ?? null,
        ],
      ),
    ];
  }
  async trash(
    kind: "recipe" | "file",
    raw: unknown,
  ): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input =
        kind === "recipe"
          ? designRecipeTrashSchema.parse(raw)
          : designFileTrashSchema.parse(raw),
      replay = await this.replay(input, `${kind}_trash`);
    if (replay.result) return replay.result;
    const preview = await this.preview({
      action: "trash",
      kind,
      recipe_id: input.recipe_id,
      ...("path" in input ? { path: input.path } : {}),
    });
    if (
      preview.impact_token !== input.impact_token ||
      preview.expected_version !== input.expected_version
    )
      throw new DesignError(
        "impact_conflict",
        "The reviewed recipe or file revisions changed",
        409,
      );
    const recipe = preview.recipe as Recipe,
      members = preview.members as Member[],
      now = this.clock(),
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
        ...this.memberGuards(recipe, members),
        ...(kind === "recipe"
          ? [
              d1Guard(
                this.db,
                "(SELECT count(*) FROM design_files WHERE recipe_id=? AND trash_group_id IS NULL)=?",
                [recipe.id, members.length - 1],
              ),
            ]
          : []),
      ],
      [
        this.db
          .prepare(
            "INSERT INTO design_trash_groups VALUES(?,?,?,?,1,'trashed',?,?,?,'[]')",
          )
          .bind(
            group,
            kind,
            recipe.id,
            "path" in input ? input.path : null,
            now,
            result.purge_after,
            now,
          ),
        this.db
          .prepare(
            `INSERT INTO design_trash_members SELECT ?,json_extract(value,'$.kind'),json_extract(value,'$.path'),json_extract(value,'$.revision')+1 FROM json_each(?)`,
          )
          .bind(group, JSON.stringify(members)),
        this.db
          .prepare(
            `UPDATE design_files SET trash_group_id=?,revision=revision+1 WHERE recipe_id=? AND path IN(SELECT path FROM design_trash_members WHERE deletion_group_id=? AND kind='file')`,
          )
          .bind(group, recipe.id, group),
        ...(kind === "recipe"
          ? [
              this.db
                .prepare(
                  "UPDATE design_recipes SET trash_group_id=?,revision=revision+1 WHERE id=?",
                )
                .bind(group, recipe.id),
            ]
          : []),
        this.receipt(input.idempotency_key, replay.fingerprint, result),
      ],
    );
    return result;
  }
  async restore(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = designRestoreSchema.parse(raw),
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
      throw new DesignError("impact_conflict", "Restore impact changed", 409);
    const recipe = preview.recipe as Recipe,
      members = preview.members as Member[],
      result = {
        state: "restored",
        deletion_group_id: group.deletion_group_id,
        version: group.version + 1,
      };
    await d1Batch(
      this.db,
      [
        d1Guard(
          this.db,
          "EXISTS(SELECT 1 FROM design_trash_groups WHERE deletion_group_id=? AND version=? AND state IN ('trashed','blocked'))",
          [group.deletion_group_id, group.version],
        ),
        ...this.memberGuards(recipe, members, group.deletion_group_id),
        ...(group.root_kind === "file"
          ? [
              d1Guard(
                this.db,
                "EXISTS(SELECT 1 FROM design_recipes WHERE id=? AND trash_group_id IS NULL)",
                [recipe.id],
              ),
            ]
          : []),
      ],
      [
        this.db
          .prepare(
            "UPDATE design_recipes SET trash_group_id=NULL,revision=revision+1 WHERE id=? AND trash_group_id=?",
          )
          .bind(recipe.id, group.deletion_group_id),
        this.db
          .prepare(
            "UPDATE design_files SET trash_group_id=NULL,revision=revision+1 WHERE recipe_id=? AND trash_group_id=?",
          )
          .bind(recipe.id, group.deletion_group_id),
        this.db
          .prepare(
            "UPDATE design_trash_groups SET state='restored',version=version+1,updated_at=? WHERE deletion_group_id=?",
          )
          .bind(this.clock(), group.deletion_group_id),
        this.receipt(input.idempotency_key, replay.fingerprint, result),
      ],
    );
    return result;
  }
  async purge(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = designPurgeSchema.parse(raw),
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
      throw new DesignError("impact_conflict", "Deletion impact changed", 409);
    if (group.state === "purging") {
      await d1Batch(
        this.db,
        [
          d1Guard(
            this.db,
            "EXISTS(SELECT 1 FROM design_trash_groups WHERE deletion_group_id=? AND state='purging')",
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
    const recipe = await this.recipe(group.recipe_id),
      members = await this.members(
        recipe,
        group.root_kind,
        group.path ?? undefined,
        group,
      ),
      facts = await this.facts(recipe, members, group);
    if (facts.blockers.length) {
      await this.db
        .prepare(
          "UPDATE design_trash_groups SET state='blocked',blockers_json=?,updated_at=? WHERE deletion_group_id=? AND state IN ('trashed','blocked')",
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
    const paths = JSON.stringify(
      members.filter((m) => m.kind === "file").map((m) => m.path),
    );
    await d1Batch(
      this.db,
      [
        d1Guard(
          this.db,
          "EXISTS(SELECT 1 FROM design_trash_groups WHERE deletion_group_id=? AND version=? AND state IN ('trashed','blocked'))",
          [group.deletion_group_id, group.version],
        ),
        ...this.memberGuards(recipe, members, group.deletion_group_id),
        ...(group.root_kind === "recipe"
          ? [
              d1Guard(
                this.db,
                "NOT EXISTS(SELECT 1 FROM design_trash_groups WHERE recipe_id=? AND deletion_group_id<>? AND state NOT IN ('purged','restored'))",
                [recipe.id, group.deletion_group_id],
              ),
            ]
          : []),
        d1Guard(
          this.db,
          `NOT EXISTS(SELECT 1 FROM json_each(?) a WHERE
          NOT EXISTS(SELECT 1 FROM design_assets WHERE object_key=a.value AND state='live' AND owned=1)
          OR EXISTS(SELECT 1 FROM design_asset_uploads WHERE object_key=a.value)
          OR EXISTS(SELECT 1 FROM design_files WHERE object_key=a.value AND (recipe_id<>? OR path NOT IN(SELECT value FROM json_each(?)))))`,
          [
            JSON.stringify(facts.owned.map((a) => a.object_key)),
            recipe.id,
            paths,
          ],
        ),
      ],
      [
        this.db
          .prepare(
            "UPDATE design_trash_groups SET state='purging',version=version+1,updated_at=?,blockers_json='[]' WHERE deletion_group_id=?",
          )
          .bind(this.clock(), group.deletion_group_id),
        this.db
          .prepare(
            "UPDATE design_assets SET state='purging' WHERE object_key IN(SELECT value FROM json_each(?))",
          )
          .bind(JSON.stringify(facts.owned.map((a) => a.object_key))),
        this.db
          .prepare(
            "INSERT INTO design_trash_assets(deletion_group_id,object_key) SELECT ?,value FROM json_each(?)",
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
      throw new DesignError(
        "purge_conflict",
        "Deletion is not in progress",
        409,
      );
    await this.db
      .prepare(
        "UPDATE design_trash_groups SET updated_at=? WHERE deletion_group_id=? AND state='purging'",
      )
      .bind(this.clock(), id)
      .run();
    const assets = await this.db
      .prepare(
        "SELECT object_key FROM design_trash_assets WHERE deletion_group_id=? AND state='pending' ORDER BY object_key LIMIT 20",
      )
      .bind(id)
      .all<{ object_key: string }>();
    if (assets.results.length) {
      const keys = assets.results.map(asset => asset.object_key);
      try {
        // A failed bulk request may have deleted only part of the batch. Keep all
        // keys pending and retry the immutable keys; R2 delete is idempotent.
        await this.assets.delete(keys);
      } catch {
        await this.db.prepare("UPDATE design_trash_groups SET blockers_json=?,updated_at=? WHERE deletion_group_id=?")
          .bind(JSON.stringify([{code:"asset_delete_retry",message:"Object deletion did not finish; retry will resume it."}]),this.clock(),id).run();
        return {state:"purging",deletion_group_id:id,restorable:false,reason:"asset_delete_retry"};
      }
      await d1Batch(this.db,[],[
        this.db.prepare("UPDATE design_trash_assets SET state='deleted' WHERE deletion_group_id=? AND object_key IN(SELECT value FROM json_each(?))").bind(id,JSON.stringify(keys)),
        this.db.prepare("UPDATE design_assets SET state='purged' WHERE object_key IN(SELECT value FROM json_each(?))").bind(JSON.stringify(keys)),
      ]);
    }

    if (
      await this.db
        .prepare(
          "SELECT 1 FROM design_trash_assets WHERE deletion_group_id=? AND state='pending'",
        )
        .bind(id)
        .first()
    )
      return { state: "purging", deletion_group_id: id, restorable: false };
    const members = (
      await this.db
        .prepare(
          "SELECT kind,path,revision FROM design_trash_members WHERE deletion_group_id=? AND kind='file' ORDER BY path LIMIT 100",
        )
        .bind(id)
        .all<Member>()
    ).results;
    await d1Batch(
      this.db,
      [
        d1Guard(
          this.db,
          "EXISTS(SELECT 1 FROM design_trash_groups WHERE deletion_group_id=? AND state='purging')",
          [id],
        ),
      ],
      [
        this.db
          .prepare(
            "DELETE FROM design_files WHERE recipe_id=? AND trash_group_id=? AND path IN(SELECT value FROM json_each(?))",
          )
          .bind(
            group.recipe_id,
            id,
            JSON.stringify(members.map((m) => m.path)),
          ),
        this.db
          .prepare(
            "DELETE FROM design_trash_members WHERE deletion_group_id=? AND kind='file' AND path IN(SELECT value FROM json_each(?))",
          )
          .bind(id, JSON.stringify(members.map((m) => m.path))),
      ],
    );
    if (
      await this.db
        .prepare(
          "SELECT 1 FROM design_trash_members WHERE deletion_group_id=? AND kind='file'",
        )
        .bind(id)
        .first()
    )
      return { state: "purging", deletion_group_id: id, restorable: false };
    await d1Batch(
      this.db,
      [
        d1Guard(
          this.db,
          "EXISTS(SELECT 1 FROM design_trash_groups WHERE deletion_group_id=? AND state='purging')",
          [id],
        ),
      ],
      [
        ...(group.root_kind === "recipe"
          ? [
              this.db
                .prepare(
                  "DELETE FROM design_recipes WHERE id=? AND trash_group_id=?",
                )
                .bind(group.recipe_id, id),
            ]
          : []),
        this.db
          .prepare(
            "UPDATE design_trash_groups SET state='purged',updated_at=?,blockers_json='[]' WHERE deletion_group_id=?",
          )
          .bind(this.clock(), id),
      ],
    );
    return { state: "purged", deletion_group_id: id, restorable: false };
  }
  async trashList(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const input = designTrashListSchema.parse(raw),
      rows = await this.db
        .prepare(
          `SELECT * FROM design_trash_groups ${input.state === "pending" ? "WHERE state IN ('trashed','blocked','purging')" : ""} ORDER BY trashed_at DESC,deletion_group_id LIMIT ? OFFSET ?`,
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
        .prepare("SELECT * FROM design_maintenance_runs WHERE run_id='trash'")
        .first(),
    };
  }
  async status(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const { idempotency_key } = z
      .object({ idempotency_key: key })
      .strict()
      .parse(raw);
    const row = await this.db
      .prepare(
        "SELECT result_json FROM design_operation_receipts WHERE request_key=?",
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
    const recipe = await this.recipe(group.recipe_id);
    const facts = await this.facts(
      recipe,
      await this.members(
        recipe,
        group.root_kind,
        group.path ?? undefined,
        group,
      ),
      group,
    );
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
    return this.claim(group);
  }
}
export function designManagementOperations(
  service: DesignManagementService,
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
      scope: effects === "read" ? "design.read" : "design.write",
      description:
        "Manage Design recipes and file deletion groups; validation status and shared objects remain distinct.",
      effects,
      retry: effects === "read" ? "read" : "request_key",
      enabled: effects === "read" || service.enabled,
      surfaces: ["mcp", "http"],
      run,
    });
  const operations: Record<string, OperationDefinition<z.ZodObject>> = {
    design_management_preview: op(designPreviewSchema, "read", (raw) =>
      service.preview(raw),
    ),
    design_recipe_trash: op(designRecipeTrashSchema, "trash", (raw) =>
      service.trash("recipe", raw),
    ),
    design_file_trash: op(designFileTrashSchema, "trash", (raw) =>
      service.trash("file", raw),
    ),
    design_trash_list: op(designTrashListSchema, "read", (raw) =>
      service.trashList(raw),
    ),
    design_trash_restore: op(designRestoreSchema, "restore", (raw) =>
      service.restore(raw),
    ),
    design_trash_purge: op(designPurgeSchema, "purge", (raw) =>
      service.purge(raw),
    ),
    design_operation_status: op(
      z.object({ idempotency_key: key }).strict(),
      "read",
      (raw) => service.status(raw),
    ),
  };
  operations.design_capabilities = op(
    z.object({}).strict(),
    "read",
    async () => ({ operations: operationCapabilities(actor, operations) }),
  );
  return operations;
}
export async function executeDesignManagement(
  service: DesignManagementService,
  actor: OperationActor,
  name: string,
  raw: unknown,
) {
  const op = designManagementOperations(service, actor)[name];
  if (!op)
    throw new DesignError(
      "operation_not_found",
      "Design operation was not found",
      404,
    );
  return executeOperation(actor, op, raw);
}
