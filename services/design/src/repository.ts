import { designManaged } from "./assets";
import { d1Batch, d1Guard } from "@personal-agent/remote-runtime/d1";
import { DesignError } from "./errors";
import type {
  DesignCatalog,
  DesignFileSummary,
  DesignMutationResult,
  DesignRecipe,
  DesignRecipeSummary,
  DesignStatus,
  JsonObject,
} from "./types";

interface RecipeRow {
  trash_group_id?: string | null;
  id: string;
  name: string;
  description: string;
  recipe_version: string;
  status: DesignStatus;
  selection_ready: number;
  metadata_json: string;
  revision: number;
  created_at: string;
  updated_at: string;
}

interface FileRow {
  trash_group_id?: string | null;
  recipe_id: string;
  path: string;
  object_key: string;
  content_type: string;
  byte_size: number;
  sha256: string;
  revision: number;
  updated_at: string;
}

export interface StoredFileInput {
  path: string;
  objectKey: string;
  contentType: string;
  byteSize: number;
  sha256: string;
}

function now(): string {
  return new Date().toISOString();
}

function parseObject(value: string, label: string): JsonObject {
  try {
    const parsed = JSON.parse(value) as unknown;
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      !Array.isArray(parsed)
    ) {
      return parsed as JsonObject;
    }
  } catch {
    // The stored value is normalized below as a service error.
  }
  throw new DesignError(
    "stored_data_invalid",
    `stored ${label} metadata is invalid`,
    500,
  );
}

function summary(row: RecipeRow): DesignRecipeSummary {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    recipeVersion: row.recipe_version,
    status: row.status,
    selectionReady: row.selection_ready === 1,
    revision: row.revision,
    updatedAt: row.updated_at,
  };
}

function fileSummary(row: FileRow): DesignFileSummary {
  return {
    path: row.path,
    contentType: row.content_type,
    byteSize: row.byte_size,
    sha256: row.sha256,
    revision: row.revision,
    updatedAt: row.updated_at,
  };
}

async function rowFor(db: D1Database, id: string): Promise<RecipeRow | null> {
  return db
    .prepare(
      `SELECT * FROM design_recipes WHERE id = ? ${(await designManaged(db)) ? "AND trash_group_id IS NULL" : ""}`,
    )
    .bind(id)
    .first<RecipeRow>();
}

async function fileRows(db: D1Database, id: string): Promise<FileRow[]> {
  const result = await db
    .prepare(
      `SELECT * FROM design_files WHERE recipe_id = ? ${(await designManaged(db)) ? "AND trash_group_id IS NULL" : ""} ORDER BY path`,
    )
    .bind(id)
    .all<FileRow>();
  return result.results ?? [];
}

export async function listRecipes(
  db: D1Database,
  options: {
    status?: DesignStatus | undefined;
    format?: string | undefined;
    lifecycle?: "active" | "trash" | "all" | undefined;
    offset?: number | undefined;
    limit: number;
  },
): Promise<DesignRecipeSummary[]> {
  const conditions: string[] =
    (await designManaged(db)) && options.lifecycle !== "all"
      ? [`trash_group_id IS ${options.lifecycle === "trash" ? "NOT " : ""}NULL`]
      : [];
  const bindings: unknown[] = [];
  if (options.status) {
    conditions.push("status = ?");
    bindings.push(options.status);
  }
  if (options.format) {
    conditions.push(
      "EXISTS (SELECT 1 FROM json_each(design_recipes.metadata_json, '$.formats') WHERE value = ?)",
    );
    bindings.push(options.format);
  }
  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
  bindings.push(options.limit, options.offset ?? 0);
  const result = await db
    .prepare(
      `SELECT * FROM design_recipes ${where}
       ORDER BY selection_ready DESC, name COLLATE NOCASE, id
       LIMIT ? OFFSET ?`,
    )
    .bind(...bindings)
    .all<RecipeRow>();
  return (result.results ?? []).map(summary);
}

export async function readRecipe(
  db: D1Database,
  id: string,
): Promise<DesignRecipe | null> {
  const row = await rowFor(db, id);
  if (!row) return null;
  return {
    ...summary(row),
    metadata: parseObject(row.metadata_json, "recipe"),
    files: (await fileRows(db, id)).map(fileSummary),
    createdAt: row.created_at,
  };
}

export async function readFileRecord(
  db: D1Database,
  id: string,
  path: string,
): Promise<FileRow | null> {
  return db
    .prepare(
      `SELECT * FROM design_files WHERE recipe_id = ? AND path = ? ${(await designManaged(db)) ? "AND trash_group_id IS NULL AND EXISTS(SELECT 1 FROM design_recipes r WHERE r.id=recipe_id AND r.trash_group_id IS NULL)" : ""}`,
    )
    .bind(id, path)
    .first<FileRow>();
}

export async function readCatalog(db: D1Database): Promise<DesignCatalog> {
  const libraryRow = await db
    .prepare("SELECT metadata_json FROM design_library ORDER BY id LIMIT 1")
    .first<{ metadata_json: string }>();
  const patternsResult = await db
    .prepare("SELECT pattern_json FROM design_patterns ORDER BY id")
    .all<{ pattern_json: string }>();
  const recipesResult = await db
    .prepare(
      `SELECT metadata_json FROM design_recipes
       WHERE status != 'deprecated' ${(await designManaged(db)) ? "AND trash_group_id IS NULL" : ""}
       ORDER BY selection_ready DESC, name COLLATE NOCASE, id`,
    )
    .all<{ metadata_json: string }>();
  if (!libraryRow) {
    return {
      catalog_schema_version: 2,
      library: {},
      patterns: [],
      recipes: [],
    };
  }
  return {
    catalog_schema_version: 2,
    library: parseObject(libraryRow.metadata_json, "library"),
    patterns: (patternsResult.results ?? []).map((row) =>
      parseObject(row.pattern_json, "pattern"),
    ),
    recipes: (recipesResult.results ?? []).map((row) =>
      parseObject(row.metadata_json, "recipe"),
    ),
  };
}

function metadataFields(metadata: JsonObject) {
  return {
    id: String(metadata.id),
    name: String(metadata.name),
    description: String(metadata.description),
    recipeVersion: String(metadata.version),
    status: String(metadata.status) as DesignStatus,
    selectionReady: metadata.selection_ready === true ? 1 : 0,
    serialized: JSON.stringify(metadata),
  };
}

export async function createRecipe(
  db: D1Database,
  metadata: JsonObject,
): Promise<DesignMutationResult> {
  const fields = metadataFields(metadata);
  const timestamp = now();
  try {
    await db
      .prepare(
        `INSERT INTO design_recipes
        (id, name, description, recipe_version, status, selection_ready,
         metadata_json, revision, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)`,
      )
      .bind(
        fields.id,
        fields.name,
        fields.description,
        fields.recipeVersion,
        fields.status,
        fields.selectionReady,
        fields.serialized,
        timestamp,
        timestamp,
      )
      .run();
  } catch (error) {
    if (String(error).includes("UNIQUE")) {
      throw new DesignError(
        "already_exists",
        "the Design recipe already exists",
        409,
        {
          id: fields.id,
        },
      );
    }
    throw error;
  }
  return {
    status: "created",
    recipe: (await readRecipe(db, fields.id))!,
  };
}

export async function updateRecipe(
  db: D1Database,
  id: string,
  expectedRevision: number,
  metadata: JsonObject,
): Promise<DesignMutationResult> {
  const fields = metadataFields(metadata);
  if (fields.id !== id) {
    throw new DesignError(
      "invalid_recipe_id",
      "the recipe id cannot change during an update",
    );
  }
  const current = await rowFor(db, id);
  if (!current) {
    throw new DesignError("not_found", "the Design recipe was not found", 404, {
      id,
    });
  }
  if (current.revision !== expectedRevision) {
    throw new DesignError(
      "version_conflict",
      "the Design recipe changed",
      409,
      {
        id,
        expected_revision: expectedRevision,
        current_revision: current.revision,
      },
    );
  }
  if (current.metadata_json === fields.serialized) {
    return { status: "unchanged", recipe: (await readRecipe(db, id))! };
  }
  const timestamp = now();
  const result = await db
    .prepare(
      `UPDATE design_recipes
        SET name = ?, description = ?, recipe_version = ?, status = ?,
            selection_ready = ?, metadata_json = ?, revision = revision + 1,
            updated_at = ?
      WHERE id = ? AND revision = ?`,
    )
    .bind(
      fields.name,
      fields.description,
      fields.recipeVersion,
      fields.status,
      fields.selectionReady,
      fields.serialized,
      timestamp,
      id,
      expectedRevision,
    )
    .run();
  if ((result.meta.changes ?? 0) !== 1) {
    throw new DesignError(
      "version_conflict",
      "the Design recipe changed",
      409,
      { id },
    );
  }
  return { status: "updated", recipe: (await readRecipe(db, id))! };
}

export async function importRecipe(
  db: D1Database,
  library: JsonObject,
  patterns: JsonObject[],
  metadata: JsonObject,
  files: StoredFileInput[],
): Promise<DesignMutationResult> {
  const fields = metadataFields(metadata);
  const timestamp = now();
  const current = await rowFor(db, fields.id);
  const currentFiles = current ? await fileRows(db, fields.id) : [];
  const unchanged = Boolean(
    current &&
      current.metadata_json === fields.serialized &&
      currentFiles.length === files.length &&
      files.every((file) =>
        currentFiles.some(
          (stored) =>
            stored.path === file.path &&
            stored.sha256 === file.sha256 &&
            stored.content_type === file.contentType,
        ),
      ),
  );

  const libraryId =
    typeof library.id === "string" ? library.id : "personal-design";
  const statements: D1PreparedStatement[] = [
    db
      .prepare(
        `INSERT INTO design_library (id, metadata_json, updated_at)
       VALUES (?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET metadata_json = excluded.metadata_json,
         updated_at = excluded.updated_at`,
      )
      .bind(libraryId, JSON.stringify(library), timestamp),
  ];
  for (const pattern of patterns) {
    if (typeof pattern.id !== "string") {
      throw new DesignError(
        "invalid_pattern",
        "a Design pattern id is required",
      );
    }
    statements.push(
      db
        .prepare(
          `INSERT INTO design_patterns (id, pattern_json, updated_at)
         VALUES (?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET pattern_json = excluded.pattern_json,
           updated_at = excluded.updated_at`,
        )
        .bind(pattern.id, JSON.stringify(pattern), timestamp),
    );
  }

  if (!current) {
    statements.push(
      db
        .prepare(
          `INSERT INTO design_recipes
          (id, name, description, recipe_version, status, selection_ready,
           metadata_json, revision, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)`,
        )
        .bind(
          fields.id,
          fields.name,
          fields.description,
          fields.recipeVersion,
          fields.status,
          fields.selectionReady,
          fields.serialized,
          timestamp,
          timestamp,
        ),
    );
  } else if (!unchanged) {
    statements.push(
      db
        .prepare(
          `UPDATE design_recipes
            SET name = ?, description = ?, recipe_version = ?, status = ?,
                selection_ready = ?, metadata_json = ?, revision = revision + 1,
                updated_at = ?
          WHERE id = ?`,
        )
        .bind(
          fields.name,
          fields.description,
          fields.recipeVersion,
          fields.status,
          fields.selectionReady,
          fields.serialized,
          timestamp,
          fields.id,
        ),
    );
  }

  for (const file of files) {
    statements.push(
      db
        .prepare(
          `INSERT INTO design_files
          (recipe_id, path, object_key, content_type, byte_size, sha256,
           revision, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, 1, ?)
         ON CONFLICT(recipe_id, path) DO UPDATE SET
           object_key = excluded.object_key,
           content_type = excluded.content_type,
           byte_size = excluded.byte_size,
           revision = CASE
             WHEN design_files.sha256 = excluded.sha256 AND design_files.content_type = excluded.content_type THEN design_files.revision
             ELSE design_files.revision + 1
           END,
           sha256 = excluded.sha256,
           updated_at = CASE
             WHEN design_files.sha256 = excluded.sha256 AND design_files.content_type = excluded.content_type THEN design_files.updated_at
             ELSE excluded.updated_at
           END`,
        )
        .bind(
          fields.id,
          file.path,
          file.objectKey,
          file.contentType,
          file.byteSize,
          file.sha256,
          timestamp,
        ),
    );
  }
  const managed = await designManaged(db);
  const guards = managed
    ? [
        d1Guard(
          db,
          current
            ? "EXISTS(SELECT 1 FROM design_recipes WHERE id=? AND revision=? AND trash_group_id IS NULL)"
            : "NOT EXISTS(SELECT 1 FROM design_recipes WHERE id=?)",
          current ? [fields.id, current.revision] : [fields.id],
        ),
        d1Guard(db, "(SELECT count(*) FROM design_files WHERE recipe_id=?)=?", [
          fields.id,
          currentFiles.length,
        ]),
        d1Guard(
          db,
          "NOT EXISTS(SELECT 1 FROM json_each(?) f WHERE NOT EXISTS(SELECT 1 FROM design_assets a WHERE a.object_key=json_extract(f.value,'$.objectKey') AND a.state='live'))",
          [JSON.stringify(files)],
        ),
        ...currentFiles.map((file) =>
          d1Guard(
            db,
            "EXISTS(SELECT 1 FROM design_files WHERE recipe_id=? AND path=? AND revision=? AND trash_group_id IS NULL)",
            [fields.id, file.path, file.revision],
          ),
        ),
      ]
    : [];
  await d1Batch(db, guards, statements);
  return {
    status: current ? (unchanged ? "unchanged" : "updated") : "created",
    recipe: (await readRecipe(db, fields.id))!,
  };
}

export async function storeFileRecord(
  db: D1Database,
  recipeId: string,
  file: StoredFileInput,
  expectedFileRevision: number,
): Promise<DesignFileSummary> {
  if (!(await rowFor(db, recipeId))) {
    throw new DesignError("not_found", "the Design recipe was not found", 404, {
      id: recipeId,
    });
  }
  const timestamp = now();
  const statement =
    expectedFileRevision === 0
      ? db
          .prepare(
            `INSERT OR IGNORE INTO design_files
        (recipe_id, path, object_key, content_type, byte_size, sha256,
         revision, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, 1, ?)`,
          )
          .bind(
            recipeId,
            file.path,
            file.objectKey,
            file.contentType,
            file.byteSize,
            file.sha256,
            timestamp,
          )
      : db
          .prepare(
            `UPDATE design_files
          SET object_key = ?, content_type = ?, byte_size = ?, sha256 = ?,
              revision = revision + 1, updated_at = ?
        WHERE recipe_id = ? AND path = ? AND revision = ?`,
          )
          .bind(
            file.objectKey,
            file.contentType,
            file.byteSize,
            file.sha256,
            timestamp,
            recipeId,
            file.path,
            expectedFileRevision,
          );
  const managed = await designManaged(db);
  const result = managed
    ? (
        await d1Batch(
          db,
          [
            d1Guard(
              db,
              "EXISTS(SELECT 1 FROM design_recipes WHERE id=? AND trash_group_id IS NULL)",
              [recipeId],
            ),
            d1Guard(
              db,
              "EXISTS(SELECT 1 FROM design_assets WHERE object_key=? AND state='live')",
              [file.objectKey],
            ),
            d1Guard(
              db,
              expectedFileRevision === 0
                ? "NOT EXISTS(SELECT 1 FROM design_files WHERE recipe_id=? AND path=?)"
                : "EXISTS(SELECT 1 FROM design_files WHERE recipe_id=? AND path=? AND revision=? AND trash_group_id IS NULL)",
              [
                recipeId,
                file.path,
                ...(expectedFileRevision === 0 ? [] : [expectedFileRevision]),
              ],
            ),
          ],
          [
            statement,
            db
              .prepare("UPDATE design_recipes SET updated_at=? WHERE id=?")
              .bind(timestamp, recipeId),
          ],
        )
      )[2]!
    : await statement.run();
  if (!managed && (result.meta.changes ?? 0) !== 1) {
    const current = await readFileRecord(db, recipeId, file.path);
    throw new DesignError("version_conflict", "the Design asset changed", 409, {
      id: recipeId,
      path: file.path,
      expected_file_revision: expectedFileRevision,
      current_file_revision: current?.revision ?? 0,
    });
  }
  if (!managed)
    await db
      .prepare("UPDATE design_recipes SET updated_at = ? WHERE id = ?")
      .bind(timestamp, recipeId)
      .run();
  return fileSummary((await readFileRecord(db, recipeId, file.path))!);
}
