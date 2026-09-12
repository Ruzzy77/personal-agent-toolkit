import { OperationError } from "./operations";
/** D1 batches roll back on exceptions, not on zero-row conditional mutations. */
export function d1Guard(
  db: D1Database,
  condition: string,
  values: unknown[] = [],
) {
  return db
    .prepare(
      `SELECT CASE WHEN (${condition}) THEN 1 ELSE json('operation_conflict') END`,
    )
    .bind(...values);
}
export async function d1Batch(
  db: D1Database,
  guards: D1PreparedStatement[],
  writes: D1PreparedStatement[],
  code = "version_conflict",
) {
  try {
    return await db.batch([...guards, ...writes]);
  } catch (error) {
    if (
      error instanceof Error &&
      /malformed JSON|operation_conflict|lifecycle_conflict|asset_deleting|writer_upgrade_required|UNIQUE constraint/.test(
        error.message,
      )
    ) {
      throw new OperationError(
        code,
        "The affected state changed; no part of this transaction was saved",
        409,
        { write_committed: false },
      );
    }
    throw error;
  }
}
