import { ContextError } from "./errors";

export async function managementSchemaReady(db: D1Database): Promise<boolean> {
  return Boolean(
    await db
      .prepare(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='corpus_management_owners'",
      )
      .first(),
  );
}

/** A zero-row UPDATE does not abort D1.batch. All guards precede all writes. */
export function guard(
  db: D1Database,
  condition: string,
  values: unknown[] = [],
): D1PreparedStatement {
  return db
    .prepare(
      `SELECT CASE WHEN (${condition}) THEN 1 ELSE json('management_conflict') END AS valid`,
    )
    .bind(...values);
}

export async function guardedBatch(
  db: D1Database,
  guards: D1PreparedStatement[],
  writes: D1PreparedStatement[],
  conflictCode = "management_conflict",
) {
  try {
    return await db.batch([...guards, ...writes]);
  } catch (error) {
    if (
      error instanceof Error &&
      /malformed JSON|management_conflict|registration_detached|UNIQUE constraint/.test(
        error.message,
      )
    ) {
      throw new ContextError(
        conflictCode,
        "The affected state changed; read the current state and prepare the operation again",
        409,
        { write_committed: false },
      );
    }
    throw error;
  }
}

export function activeSpaceGuard(
  db: D1Database,
  owner: string,
  space: string,
  version?: number,
) {
  return guard(
    db,
    `EXISTS (SELECT 1 FROM corpus_spaces AS s JOIN corpus_contexts AS c
    ON c.owner_id=s.owner_id AND c.space_id=s.space_id
    WHERE s.owner_id=? AND s.space_id=? AND s.state='active' AND s.trash_group_id IS NULL
    AND s.access_scope='remote_allowed' ${version === undefined ? "" : "AND c.version=?"})`,
    [owner, space, ...(version === undefined ? [] : [version])],
  );
}

export function sourceBindingsGuard(
  db: D1Database,
  owner: string,
  refs: unknown[],
) {
  return guard(
    db,
    `NOT EXISTS (SELECT 1 FROM json_each(?) r WHERE NOT EXISTS (
    SELECT 1 FROM corpus_connections c JOIN corpus_spaces s ON s.owner_id=c.owner_id AND s.space_id=c.space_id
    WHERE c.owner_id=? AND c.space_id=json_extract(r.value,'$.source_space_id')
      AND c.connection_id=json_extract(r.value,'$.connection_id') AND c.corpus_id=json_extract(r.value,'$.corpus_id')
      AND c.access_scope='remote_allowed' AND s.access_scope='remote_allowed' AND c.index_mode='indexed'
      AND EXISTS (SELECT 1 FROM json_each(c.roles_json) WHERE value='source')
      AND NOT EXISTS (SELECT 1 FROM corpus_trash_groups g WHERE g.owner_id=s.owner_id
        AND g.deletion_group_id=s.trash_group_id AND g.state IN ('purging','purged'))))`,
    [JSON.stringify(refs), owner],
  );
}
