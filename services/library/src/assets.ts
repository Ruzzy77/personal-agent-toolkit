import {
  ImmutableObjectConflict,
  objectIdentity,
  putImmutable,
} from "@personal-agent/immutable-assets";
import { d1Batch, d1Guard } from "@personal-agent/remote-runtime/d1";
import { LibraryError } from "./errors";
export async function libraryManaged(db: D1Database) {
  return Boolean(
    await db
      .prepare("SELECT 1 FROM sqlite_master WHERE name='library_assets'")
      .first(),
  );
}
/** Structured attributes only. Unsupported encodings fail closed for cleanup. */
export function issueAssets(
  html: string,
  cover: string | null,
): { keys: string[]; complete: boolean } {
  const keys = new Set<string>();
  let complete = true;
  const add = (raw: string) => {
    let value = raw
      .replaceAll("&amp;", "&")
      .replaceAll("&quot;", '"')
      .replaceAll("&#39;", "'");
    if (/&#|\\/.test(value)) {
      complete = false;
      return;
    }
    if (!value.startsWith("/media/")) {
      // Absolute/scheme-relative local media URLs are not canonical references.
      // Do not mistake a potentially shared object for unreferenced storage.
      if (/\/media\//i.test(value)) {
        complete = false;
        return;
      }
      if (/^(?:https?:|data:|#)/i.test(value) || !value) return;
      if (/\.(png|jpg|jpeg|webp|gif|avif)(?:[?#]|$)/i.test(value))
        complete = false;
      return;
    }
    try {
      value = decodeURIComponent(value.slice(7).split(/[?#]/)[0]!);
    } catch {
      complete = false;
      return;
    }
    if (
      !/^[A-Za-z0-9/_-]+\.[A-Za-z0-9]+$/.test(value) ||
      value.includes("..")
    ) {
      complete = false;
      return;
    }
    keys.add(value);
  };
  if (cover) add(cover);
  for (const match of html.matchAll(
    /\b(?:src|href|poster|xlink:href)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi,
  ))
    add(match[1] ?? match[2] ?? match[3] ?? "");
  for (const match of html.matchAll(/\bsrcset\s*=\s*(?:"([^"]*)"|'([^']*)')/gi))
    for (const part of (match[1] ?? match[2] ?? "").split(","))
      add(part.trim().split(/\s+/)[0] ?? "");
  for (const match of html.matchAll(/url\(\s*['"]?([^'"\s)]+)['"]?\s*\)/gi))
    add(match[1]!);
  // CSS escapes, dynamic resource values and unparsed srcset are not an inventory.
  if (
    /\\|\b(?:srcset)\s*=\s*[^'"\s]|image-set\(|var\([^)]*\).*\/media\//i.test(
      html,
    )
  )
    complete = false;
  return { keys: [...keys].sort(), complete };
}
export function inventoryGuards(
  db: D1Database,
  html: string,
  cover: string | null,
) {
  const refs = issueAssets(html, cover);
  return [
    d1Guard(
      db,
      "NOT EXISTS(SELECT 1 FROM library_assets WHERE object_key IN(SELECT value FROM json_each(?)) AND state<>'live')",
      [JSON.stringify(refs.keys)],
    ),
    ...(refs.complete
      ? []
      : [
          d1Guard(
            db,
            "NOT EXISTS(SELECT 1 FROM library_assets WHERE state='purging')",
          ),
        ]),
  ];
}
export function inventoryWrites(
  db: D1Database,
  id: string,
  version: number,
  html: string,
  cover: string | null,
) {
  const refs = issueAssets(html, cover);
  return [
    db.prepare("DELETE FROM library_asset_refs WHERE issue_id=?").bind(id),
    db
      .prepare(
        "INSERT OR IGNORE INTO library_assets(object_key) SELECT value FROM json_each(?)",
      )
      .bind(JSON.stringify(refs.keys)),
    db
      .prepare(
        "INSERT INTO library_asset_refs SELECT ?,value FROM json_each(?)",
      )
      .bind(id, JSON.stringify(refs.keys)),
    db
      .prepare(
        "INSERT INTO library_asset_inventory VALUES(?,?,?) ON CONFLICT(issue_id) DO UPDATE SET version=excluded.version,complete=excluded.complete",
      )
      .bind(id, version, Number(refs.complete)),
  ];
}
export async function indexExistingIssues(db: D1Database) {
  const rows = await db
    .prepare(
      "SELECT id,version,source_html,cover_path FROM documents d WHERE NOT EXISTS(SELECT 1 FROM library_asset_inventory i WHERE i.issue_id=d.id AND i.version=d.version) LIMIT 100",
    )
    .all<{
      id: string;
      version: number;
      source_html: string;
      cover_path: string | null;
    }>();
  for (const row of rows.results)
    await d1Batch(
      db,
      [
        d1Guard(
          db,
          "EXISTS(SELECT 1 FROM documents WHERE id=? AND version=?)",
          [row.id, row.version],
        ),
        ...inventoryGuards(db, row.source_html, row.cover_path),
      ],
      inventoryWrites(db, row.id, row.version, row.source_html, row.cover_path),
    );
}
export async function storeLibraryAsset(
  db: D1Database,
  bucket: R2Bucket,
  key: string,
  bytes: Uint8Array,
  mime: string,
) {
  const managed = await libraryManaged(db),
    upload = crypto.randomUUID(),
    identity = await objectIdentity(bytes, mime);
  if (managed)
    await d1Batch(
      db,
      [
        d1Guard(
          db,
          "NOT EXISTS(SELECT 1 FROM library_assets WHERE object_key=? AND state<>'live')",
          [key],
        ),
      ],
      [
        db
          .prepare("INSERT OR IGNORE INTO library_assets(object_key) VALUES(?)")
          .bind(key),
        db
          .prepare("INSERT INTO library_asset_uploads VALUES(?,?,?)")
          .bind(upload, key, new Date().toISOString()),
      ],
    );
  try {
    await putImmutable(bucket, key, bytes, mime);
  } catch (error) {
    if (error instanceof ImmutableObjectConflict) {
      if (managed)
        await db
          .prepare("DELETE FROM library_asset_uploads WHERE upload_id=?")
          .bind(upload)
          .run();
      throw new LibraryError("asset_conflict", error.message, 409);
    }
    // Unknown R2 outcome retains the admission reservation and blocks deletion.
    throw new LibraryError(
      "asset_upload_outcome_unknown",
      "Asset upload did not confirm completion; its object is protected pending reconciliation",
      503,
      { upload_id: upload },
    );
  }
  if (managed)
    await d1Batch(
      db,
      [],
      [
        db
          .prepare(
            "UPDATE library_assets SET owned=1,content_type=?,sha256=? WHERE object_key=? AND state='live'",
          )
          .bind(mime, identity.sha256, key),
        db
          .prepare("DELETE FROM library_asset_uploads WHERE upload_id=?")
          .bind(upload),
      ],
    );
}
