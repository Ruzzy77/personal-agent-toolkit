import { LibraryManagementService } from "personal-agent-library-service/management";
import { DesignManagementService } from "personal-agent-design-service/management";
import { SenseManagementService } from "./sense-management";
import { CorpusManagementService } from "./corpus-management";
import { managementSchemaReady } from "./management-db";
import type { Env } from "./types";

/** Bounded daily maintenance. A disabled deployment reports candidates, never deletes. */
export async function runTrashMaintenance(env: Env, scheduledTime: number) {
  if (!(await managementSchemaReady(env.STATE_DB)))
    return { state: "schema_pending" };
  const startedAt = new Date(scheduledTime).toISOString();
  const enabled = env.TRASH_SWEEP_ENABLED === "true";
  const groups = await env.STATE_DB.prepare(
    `SELECT owner_id,deletion_group_id FROM corpus_trash_groups
    WHERE state IN ('trashed','blocked','purging') AND purge_after<=?
    ORDER BY updated_at,deletion_group_id LIMIT 5`,
  )
    .bind(startedAt)
    .all<{ owner_id: string; deletion_group_id: string }>();
  const results: Array<Record<string, unknown>> = [];
  await env.STATE_DB.prepare(
    `INSERT INTO corpus_maintenance_runs(run_id,started_at,finished_at,state,result_json) VALUES('trash',?,NULL,'running','{}')
    ON CONFLICT(run_id) DO UPDATE SET started_at=excluded.started_at,finished_at=NULL,state='running',result_json='{}'`,
  )
    .bind(startedAt)
    .run();
  for (const group of groups.results) {
    try {
      // This internal principal is never passed to an ordinary operation executor.
      const service = new CorpusManagementService(
        env,
        {
          ownerId: group.owner_id,
          scopes: new Set(),
          clientId: "due-trash-maintenance",
          auth: "sync-device",
        },
        () => startedAt,
      );
      results.push(
        enabled
          ? await service.purgeDue(group.deletion_group_id)
          : await service.auditDue(group.deletion_group_id),
      );
    } catch {
      results.push({
        deletion_group_id: group.deletion_group_id,
        state: "retry_pending",
        code: "maintenance_failed",
      });
      if (enabled)
        await env.STATE_DB.prepare(
          `UPDATE corpus_trash_groups SET updated_at=?,blockers_json=? WHERE owner_id=? AND deletion_group_id=? AND state IN ('trashed','blocked','purging')`,
        )
          .bind(
            startedAt,
            JSON.stringify([
              {
                code: "maintenance_failed",
                message:
                  "Deletion did not finish; the next maintenance run will retry it.",
              },
            ]),
            group.owner_id,
            group.deletion_group_id,
          )
          .run();
    }
  }
  if (
    await env.STATE_DB.prepare(
      "SELECT 1 FROM sqlite_master WHERE name='sense_trash_groups'",
    ).first()
  ) {
    const senseGroups = await env.STATE_DB.prepare(
      `SELECT owner_id,deletion_group_id FROM sense_trash_groups
      WHERE state IN ('trashed','blocked') AND purge_after<=? ORDER BY updated_at,deletion_group_id LIMIT 5`,
    )
      .bind(startedAt)
      .all<{ owner_id: string; deletion_group_id: string }>();
    for (const group of senseGroups.results) {
      try {
        const service = new SenseManagementService(
          env,
          {
            ownerId: group.owner_id,
            scopes: new Set(),
            clientId: "due-trash-maintenance",
            auth: "sync-device",
          },
          () => startedAt,
        );
        results.push({
          product: "sense",
          ...(enabled
            ? await service.purgeDue(group.deletion_group_id)
            : await service.auditDue(group.deletion_group_id)),
        });
      } catch {
        results.push({
          product: "sense",
          deletion_group_id: group.deletion_group_id,
          state: "retry_pending",
          code: "maintenance_failed",
        });
        if (enabled)
          await env.STATE_DB.prepare(
            "UPDATE sense_trash_groups SET updated_at=?,blockers_json=? WHERE owner_id=? AND deletion_group_id=? AND state IN ('trashed','blocked')",
          )
            .bind(
              startedAt,
              JSON.stringify([
                {
                  code: "maintenance_failed",
                  message: "Deletion did not finish; the next run will retry.",
                },
              ]),
              group.owner_id,
              group.deletion_group_id,
            )
            .run();
      }
    }
  }
  for (const product of ["library", "design"] as const) {
    const db = product === "library" ? env.LIBRARY_DB : env.DESIGN_DB;
    if (
      !(await db
        .prepare("SELECT 1 FROM sqlite_master WHERE name=?")
        .bind(`${product}_trash_groups`)
        .first())
    )
      continue;
    const rows = await db
      .prepare(
        `SELECT deletion_group_id FROM ${product}_trash_groups WHERE state IN ('trashed','blocked','purging') AND purge_after<=? ORDER BY updated_at,deletion_group_id LIMIT 5`,
      )
      .bind(startedAt)
      .all<{ deletion_group_id: string }>();
    const service =
      product === "library"
        ? new LibraryManagementService(
            db,
            env.LIBRARY_MEDIA,
            false,
            () => startedAt,
          )
        : new DesignManagementService(
            db,
            env.DESIGN_ASSETS,
            false,
            () => startedAt,
          );
    const productResults = [];
    for (const row of rows.results) {
      try {
        productResults.push(
          enabled
            ? await service.purgeDue(row.deletion_group_id)
            : await service.auditDue(row.deletion_group_id),
        );
      } catch {
        const failure = {
          deletion_group_id: row.deletion_group_id,
          state: "retry_pending",
          code: "maintenance_failed",
        };
        productResults.push(failure);
        if (enabled)
          await db
            .prepare(
              `UPDATE ${product}_trash_groups SET updated_at=?,blockers_json=? WHERE deletion_group_id=? AND state IN ('trashed','blocked','purging')`,
            )
            .bind(
              startedAt,
              JSON.stringify([
                {
                  code: "maintenance_failed",
                  message: "Deletion did not finish; the next run will retry.",
                },
              ]),
              row.deletion_group_id,
            )
            .run();
      }
    }
    const state = !enabled
      ? "dry_run"
      : productResults.some(
            (r) => r.state === "retry_pending" || r.state === "purging",
          )
        ? "retry_pending"
        : "completed";
    await db
      .prepare(
        `INSERT INTO ${product}_maintenance_runs VALUES('trash',?,?,?) ON CONFLICT(run_id) DO UPDATE SET started_at=excluded.started_at,state=excluded.state,result_json=excluded.result_json`,
      )
      .bind(
        startedAt,
        state,
        JSON.stringify({ enabled, results: productResults }),
      )
      .run();
    results.push(...productResults.map((result) => ({ product, ...result })));
  }
  const result = { enabled, results, candidate_count: results.length };
  await env.STATE_DB.prepare(
    "UPDATE corpus_maintenance_runs SET finished_at=?,state=?,result_json=? WHERE run_id='trash' AND started_at=?",
  )
    .bind(
      new Date().toISOString(),
      !enabled
        ? "dry_run"
        : results.some(
              (row) => row.state === "retry_pending" || row.state === "purging",
            )
          ? "retry_pending"
          : "completed",
      JSON.stringify(result),
      startedAt,
    )
    .run();
  return result;
}
