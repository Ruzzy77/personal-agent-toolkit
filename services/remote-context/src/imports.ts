import { canonicalJson, nowIso } from "./canonical";
import { ContextError } from "./errors";
import { corpusMetadataImportSchema } from "./schemas";

const ABSOLUTE_PATH = /^(?:\/|~[/\\]|[A-Za-z]:[\\/]|file:)/i;
const EMBEDDED_PRIVATE_PATH = /(?:\bfile:(?:\/\/)?\/|(?:^|[\s"'(])\/(?:Users|home|Volumes)\/|\b[A-Za-z]:\\)/;

function requireRelativePath(value: string): string {
  const normalized = value.normalize("NFC").replaceAll("\\", "/");
  if (
    ABSOLUTE_PATH.test(normalized) ||
    normalized.split("/").some((part) => part === ".." || part === "")
  ) {
    throw new ContextError(
      "private_path_rejected",
      "remote Corpus migration accepts only normalized relative paths",
    );
  }
  return normalized;
}

function rejectEmbeddedPaths(...values: Array<string | null>): void {
  if (values.some((value) => value != null && EMBEDDED_PRIVATE_PATH.test(value))) {
    throw new ContextError(
      "private_path_rejected",
      "remote Corpus migration cannot retain local absolute paths",
    );
  }
}

export async function importCorpusMetadata(
  db: D1Database,
  ownerId: string,
  raw: unknown,
): Promise<Record<string, unknown>> {
  const input = corpusMetadataImportSchema.parse(raw);
  const receipt = await db
    .prepare(
      `SELECT counts_json, imported_at FROM migration_receipts
       WHERE owner_id = ? AND product = 'corpus-metadata' AND source_digest = ?`,
    )
    .bind(ownerId, input.sourceDigest)
    .first<{ counts_json: string; imported_at: string }>();
  if (receipt) {
    return {
      changed: false,
      sourceDigest: input.sourceDigest,
      counts: JSON.parse(receipt.counts_json),
      importedAt: receipt.imported_at,
    };
  }

  const spaceIds = new Set(input.spaces.map((space) => space.spaceId));
  if (spaceIds.size !== input.spaces.length) {
    throw new ContextError("duplicate_space", "Corpus migration contains duplicate Spaces");
  }
  const contextIds = new Set<string>();
  const itemIds = new Set<string>();
  const sourceIds = new Set<string>();
  for (const context of input.contexts) {
    if (!spaceIds.has(context.spaceId) || contextIds.has(context.spaceId)) {
      throw new ContextError(
        "invalid_context_binding",
        "Corpus Context must bind to one unique migrated Space",
      );
    }
    contextIds.add(context.spaceId);
    rejectEmbeddedPaths(context.title, context.purpose, context.skill?.instructions ?? null);
    const localItemIds = new Set<string>();
    for (const item of context.items) {
      rejectEmbeddedPaths(item.bodyText);
      if (itemIds.has(item.itemId)) {
        throw new ContextError("duplicate_context_item", "Context item ids must be owner-unique");
      }
      itemIds.add(item.itemId);
      localItemIds.add(item.itemId);
    }
    for (const source of context.sources) {
      if (!localItemIds.has(source.itemId) || sourceIds.has(source.sourceRefId)) {
        throw new ContextError(
          "invalid_source_binding",
          "Context Source references must target items in the same Context and be unique",
        );
      }
      sourceIds.add(source.sourceRefId);
    }
  }

  const connectionKeys = new Set<string>();
  const deviceIds = new Set(input.devices.map((device) => device.deviceId));
  for (const connection of input.connections) {
    const key = `${connection.spaceId}\u0000${connection.connectionId}`;
    if (!spaceIds.has(connection.spaceId) || connectionKeys.has(key)) {
      throw new ContextError(
        "invalid_connection_binding",
        "Corpus Connection binding is invalid or duplicated",
      );
    }
    if (connection.deviceId && !deviceIds.has(connection.deviceId)) {
      throw new ContextError(
        "invalid_device_binding",
        "Corpus Connection refers to an unknown Sync device",
      );
    }
    if (connection.localConnectionKey && ABSOLUTE_PATH.test(connection.localConnectionKey)) {
      throw new ContextError(
        "private_path_rejected",
        "local Connection keys must be opaque identifiers rather than paths",
      );
    }
    connectionKeys.add(key);
  }
  for (const current of input.currentFiles) {
    if (!connectionKeys.has(`${current.spaceId}\u0000${current.connectionId}`)) {
      throw new ContextError(
        "invalid_current_file_binding",
        "Current File refers to an unknown Connection",
      );
    }
    requireRelativePath(current.relativePath);
  }

  const statements: D1PreparedStatement[] = [
    db.prepare("DELETE FROM sync_job_events WHERE owner_id = ?").bind(ownerId),
    db.prepare("DELETE FROM sync_jobs WHERE owner_id = ?").bind(ownerId),
    db.prepare("DELETE FROM corpus_current_files WHERE owner_id = ?").bind(ownerId),
    db.prepare("DELETE FROM corpus_connections WHERE owner_id = ?").bind(ownerId),
    db.prepare("DELETE FROM corpus_context_sources WHERE owner_id = ?").bind(ownerId),
    db.prepare("DELETE FROM corpus_context_items WHERE owner_id = ?").bind(ownerId),
    db.prepare("DELETE FROM corpus_context_skills WHERE owner_id = ?").bind(ownerId),
    db.prepare("DELETE FROM corpus_contexts WHERE owner_id = ?").bind(ownerId),
    db.prepare("DELETE FROM corpus_spaces WHERE owner_id = ?").bind(ownerId),
    db.prepare("DELETE FROM sync_devices WHERE owner_id = ?").bind(ownerId),
  ];

  for (const device of input.devices) {
    statements.push(
      db
        .prepare(
          `INSERT INTO sync_devices(
             owner_id, device_id, display_name, credential_id, status,
             capabilities_json, last_seen_at, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)`,
        )
        .bind(
          ownerId,
          device.deviceId,
          device.displayName,
          `environment:${device.deviceId}`,
          device.status,
          canonicalJson(device.capabilities),
          device.createdAt,
          device.updatedAt,
        ),
    );
  }
  for (const space of input.spaces) {
    statements.push(
      db
        .prepare(
          `INSERT INTO corpus_spaces(
             owner_id, space_id, display_name, state, access_scope,
             primary_work_connection_id, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
        )
        .bind(
          ownerId,
          space.spaceId,
          space.displayName,
          space.state,
          space.accessScope,
          space.primaryWorkConnectionId,
          space.updatedAt,
        ),
    );
  }
  for (const context of input.contexts) {
    statements.push(
      db
        .prepare(
          `INSERT INTO corpus_contexts(
             owner_id, space_id, title, purpose, scope_json, version, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
        )
        .bind(
          ownerId,
          context.spaceId,
          context.title,
          context.purpose,
          canonicalJson(context.scope),
          context.version,
          context.updatedAt,
        ),
    );
    if (context.skill) {
      statements.push(
        db
          .prepare(
            `INSERT INTO corpus_context_skills(
               owner_id, space_id, name, description, instructions, version, updated_at
             ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
          )
          .bind(
            ownerId,
            context.spaceId,
            context.skill.name,
            context.skill.description,
            context.skill.instructions,
            context.skill.version,
            context.skill.updatedAt,
          ),
      );
    }
    for (const item of context.items) {
      statements.push(
        db
          .prepare(
            `INSERT INTO corpus_context_items(
               owner_id, space_id, item_id, kind, body_text, attributes_json,
               disclosure_state, lifecycle_state, supersedes_item_id, created_at
             ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          )
          .bind(
            ownerId,
            context.spaceId,
            item.itemId,
            item.kind,
            item.bodyText,
            canonicalJson(item.attributes),
            item.disclosureState,
            item.lifecycleState,
            item.supersedesItemId,
            item.createdAt,
          ),
      );
    }
    for (const source of context.sources) {
      statements.push(
        db
          .prepare(
            `INSERT INTO corpus_context_sources(
               owner_id, source_ref_id, item_id, corpus_id, snapshot_id,
               document_id, revision_id, projection_id, source_unit_id,
               provider_kind, provider_record_id, link_role, source_span_json
             ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          )
          .bind(
            ownerId,
            source.sourceRefId,
            source.itemId,
            source.corpusId,
            source.snapshotId,
            source.documentId,
            source.revisionId,
            source.projectionId,
            source.sourceUnitId,
            source.providerKind,
            source.providerRecordId,
            source.linkRole,
            canonicalJson(source.sourceSpan),
          ),
      );
    }
  }
  for (const connection of input.connections) {
    statements.push(
      db
        .prepare(
          `INSERT INTO corpus_connections(
             owner_id, space_id, connection_id, display_name, roles_json,
             access_scope, permission, index_mode, corpus_id, device_id,
             local_connection_key, generation, configuration_state, source_state,
             record_state, captured_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .bind(
          ownerId,
          connection.spaceId,
          connection.connectionId,
          connection.displayName,
          canonicalJson(connection.roles),
          connection.accessScope,
          connection.permission,
          connection.indexMode,
          connection.corpusId,
          connection.deviceId,
          connection.localConnectionKey,
          connection.generation,
          connection.configurationState,
          connection.sourceState,
          connection.recordState,
          connection.capturedAt,
          connection.updatedAt,
        ),
    );
  }
  for (const current of input.currentFiles) {
    statements.push(
      db
        .prepare(
          `INSERT INTO corpus_current_files(
             owner_id, space_id, connection_id, relative_path, version_token,
             state, reason, residency_state, size, modified_ns, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .bind(
          ownerId,
          current.spaceId,
          current.connectionId,
          requireRelativePath(current.relativePath),
          current.versionToken,
          current.state,
          current.reason,
          current.residencyState,
          current.size,
          current.modifiedNs,
          current.updatedAt,
        ),
    );
  }
  const counts = {
    spaces: input.spaces.length,
    contexts: input.contexts.length,
    contextItems: itemIds.size,
    contextSources: sourceIds.size,
    connections: input.connections.length,
    currentFiles: input.currentFiles.length,
    devices: input.devices.length,
  };
  const importedAt = nowIso();
  statements.push(
    db
      .prepare(
        `INSERT INTO migration_receipts(
           owner_id, product, source_digest, source_schema_version, counts_json, imported_at
         ) VALUES (?, 'corpus-metadata', ?, ?, ?, ?)`,
      )
      .bind(
        ownerId,
        input.sourceDigest,
        input.sourceSchemaVersion,
        canonicalJson(counts),
        importedAt,
      ),
  );
  await db.batch(statements);
  return { changed: true, sourceDigest: input.sourceDigest, counts, importedAt };
}
