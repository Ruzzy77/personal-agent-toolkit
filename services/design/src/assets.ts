import {
  objectIdentity,
  putImmutable,
  ImmutableObjectConflict,
} from "@personal-agent/immutable-assets";
import { d1Batch, d1Guard } from "@personal-agent/remote-runtime/d1";
import { DesignError } from "./errors";
export async function designManaged(db: D1Database) {
  return Boolean(
    await db
      .prepare("SELECT 1 FROM sqlite_master WHERE name='design_assets'")
      .first(),
  );
}
export async function admitDesignAsset(
  db: D1Database,
  bucket: R2Bucket,
  recipe: string,
  path: string,
  bytes: Uint8Array,
  mime: string,
) {
  const identity = await objectIdentity(bytes, mime),
    key = `recipes/${recipe}/${identity.identity}/${path}`,
    upload = crypto.randomUUID(),
    managed = await designManaged(db);
  if (managed)
    await d1Batch(
      db,
      [
        d1Guard(
          db,
          "NOT EXISTS(SELECT 1 FROM design_assets WHERE object_key=? AND state<>'live')",
          [key],
        ),
        d1Guard(
          db,
          "NOT EXISTS(SELECT 1 FROM design_recipes WHERE id=? AND trash_group_id IS NOT NULL)",
          [recipe],
        ),
      ],
      [
        db
          .prepare(
            "INSERT OR IGNORE INTO design_assets(object_key,content_type,sha256) VALUES(?,?,?)",
          )
          .bind(key, mime, identity.sha256),
        db
          .prepare("INSERT INTO design_asset_uploads VALUES(?,?,?)")
          .bind(upload, key, new Date().toISOString()),
      ],
    );
  try {
    await putImmutable(bucket, key, bytes, mime);
  } catch (error) {
    if (error instanceof ImmutableObjectConflict) {
      if (managed)
        await db
          .prepare("DELETE FROM design_asset_uploads WHERE upload_id=?")
          .bind(upload)
          .run();
      throw new DesignError("asset_conflict", error.message, 409);
    }
    throw new DesignError(
      "asset_upload_outcome_unknown",
      "The object upload outcome is unknown; its reservation is retained",
      503,
      { upload_id: upload },
    );
  }
  return { key, digest: identity.sha256, upload, managed };
}
export async function finishDesignUpload(
  db: D1Database,
  uploads: Array<{ upload: string; managed: boolean }>,
  commit: () => Promise<unknown>,
) {
  try {
    const result = await commit();
    for (const upload of uploads)
      if (upload.managed)
        await db
          .prepare("DELETE FROM design_asset_uploads WHERE upload_id=?")
          .bind(upload.upload)
          .run();
    return result;
  } catch (error) {
    if (
      (error instanceof DesignError && error.status < 500) ||
      (error instanceof Error &&
        "details" in error &&
        (error as { details: { write_committed?: boolean } }).details
          .write_committed === false)
    ) {
      for (const upload of uploads)
        if (upload.managed)
          await db
            .prepare("DELETE FROM design_asset_uploads WHERE upload_id=?")
            .bind(upload.upload)
            .run();
    }
    throw error;
  }
}
