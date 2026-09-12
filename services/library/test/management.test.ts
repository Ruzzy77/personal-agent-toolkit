import { env } from "cloudflare:test";
import { describe, it, expect } from "vitest";
import { LibraryService } from "../src/service";
import { LibraryManagementService } from "../src/management";
import { issueAssets } from "../src/assets";
import type { Env } from "../src/types";
const runtime = env as unknown as Env;
const source = (title: string) =>
  `<!doctype html><html><head><title>${title}</title></head><body><h1>${title}</h1><p class="lead">Introduction</p><article><p>Body</p></article></body></html>`;
async function trash(service: LibraryManagementService, id: string) {
  const p = await service.preview({ action: "trash", issue_id: id });
  return service.trash({
    issue_id: id,
    expected_version: p.expected_version,
    impact_token: p.impact_token,
    idempotency_key: crypto.randomUUID(),
  });
}
async function purge(service: LibraryManagementService, id: unknown) {
  const p = await service.preview({ action: "purge", deletion_group_id: id });
  return service.purge({
    deletion_group_id: id,
    expected_version: p.expected_version,
    impact_token: p.impact_token,
    idempotency_key: crypto.randomUUID(),
    confirm_permanent_delete: true,
  });
}
describe("Library lifecycle and immutable assets", () => {
  it("keeps a partially deleted bulk batch pending and requires manual resume before its deadline", async () => {
    const service=new LibraryService(runtime),first="management/bulk-a.png",second="management/bulk-b.png";
    for(const path of [first,second]) await service.uploadAsset(path,"image/png",new Uint8Array([1,2]));
    await service.createIssue({id:"daily:2026-09-23:09",source_html:source("Bulk").replace("<p>Body</p>",`<img src="/media/${first}"><img src="/media/${second}">`),references:[]});
    const failing=new LibraryManagementService(runtime.DB,{delete:async(keys:string|string[])=>{await runtime.MEDIA.delete(Array.isArray(keys)?keys[0]!:keys);throw new Error("partial R2 failure");}} as unknown as R2Bucket,true);
    const group=await trash(failing,"daily:2026-09-23:09");
    expect(await purge(failing,group.deletion_group_id)).toMatchObject({state:"purging",restorable:false});
    expect((await runtime.DB.prepare("SELECT count(*) AS count FROM library_trash_assets WHERE deletion_group_id=? AND state='pending'").bind(group.deletion_group_id).first<{count:number}>())?.count).toBe(2);
    expect(await service.management().purgeDue(String(group.deletion_group_id))).toEqual({state:"not_due"});
    expect(await purge(service.management(),group.deletion_group_id)).toMatchObject({state:"purged"});
    expect(await service.readAsset(first)).toBeNull();expect(await service.readAsset(second)).toBeNull();
  });

  it("holds uncertain absolute local-media references rather than deleting an unenumerated shared object", () => {
    expect(
      issueAssets('<img src="https://owner.example/media/shared.png">', null)
        .complete,
    ).toBe(false);
    expect(
      issueAssets('<img src="//owner.example/media/shared.png">', null)
        .complete,
    ).toBe(false);
    expect(
      issueAssets("<p>Text mentions /media/not-an-asset.png</p>", null),
    ).toEqual({ keys: [], complete: true });
  });

  it("retains shared media and publication fields, then deletes only the final owned reference", async () => {
    const service = new LibraryService(runtime),
      manage = service.management(),
      bytes = new Uint8Array([1, 2, 3]),
      path = "management/shared.png";
    await service.uploadAsset(path, "image/png", bytes);
    await service.uploadAsset(path, "image/png", bytes);
    await expect(
      service.uploadAsset(path, "image/webp", bytes),
    ).rejects.toMatchObject({ code: "asset_conflict" });
    for (const id of ["daily:2026-09-20:09", "daily:2026-09-20:10"])
      await service.createIssue({
        id,
        source_html: source(id),
        cover_path: `/media/${path}`,
        references: [],
      });
    const first = await trash(manage, "daily:2026-09-20:09");
    expect(await service.readIssue("daily:2026-09-20:09")).toBeNull();
    expect(await purge(manage, first.deletion_group_id)).toMatchObject({
      state: "purged",
    });
    expect(await service.readAsset(path)).not.toBeNull();
    const second = await trash(manage, "daily:2026-09-20:10");
    const restore = await manage.preview({
      action: "restore",
      deletion_group_id: second.deletion_group_id,
    });
    await manage.restore({
      deletion_group_id: second.deletion_group_id,
      expected_version: 1,
      impact_token: restore.impact_token,
      idempotency_key: crypto.randomUUID(),
    });
    expect(await service.readIssue("daily:2026-09-20:10")).toMatchObject({
      collection: "daily",
      date: "2026-09-20",
      coverPath: `/media/${path}`,
    });
    const final = await trash(manage, "daily:2026-09-20:10");
    expect(await purge(manage, final.deletion_group_id)).toMatchObject({
      state: "purged",
    });
    expect(await service.readAsset(path)).toBeNull();
  });
  it("does not claim DB-only completion after an object failure and rejects reference resurrection during purge", async () => {
    const service = new LibraryService(runtime);
    const path = "management/retry.png";
    await service.uploadAsset(path, "image/png", new Uint8Array([4]));
    await service.createIssue({
      id: "daily:2026-09-21:09",
      source_html: source("Retry"),
      cover_path: `/media/${path}`,
      references: [],
    });
    const failing = new LibraryManagementService(
      runtime.DB,
      {
        delete: async () => {
          throw new Error("isolated failure");
        },
      } as unknown as R2Bucket,
      true,
    );
    const group = await trash(failing, "daily:2026-09-21:09");
    expect(await purge(failing, group.deletion_group_id)).toMatchObject({
      state: "purging",
      restorable: false,
    });
    const before = await runtime.DB.prepare(
      "SELECT id FROM documents WHERE id=?",
    )
      .bind("daily:2026-09-21:09")
      .first();
    expect(before).not.toBeNull();
    await expect(
      service.createIssue({
        id: "daily:2026-09-21:10",
        source_html: source("Late"),
        cover_path: `/media/${path}`,
        references: [],
      }),
    ).rejects.toMatchObject({ code: "version_conflict" });
    expect(
      await runtime.DB.prepare("SELECT id FROM documents WHERE id=?")
        .bind("daily:2026-09-21:10")
        .first(),
    ).toBeNull();
    expect(
      await purge(service.management(), group.deletion_group_id),
    ).toMatchObject({ state: "purged" });
  });
  it("blocks uncertain legacy ownership, respects exact 30-day time, and never treats prose as an asset reference", async () => {
    expect(issueAssets("<p>/media/unrelated.png</p>", null)).toEqual({
      keys: [],
      complete: true,
    });
    expect(issueAssets('<img src="/media/photo.png">', null)).toEqual({
      keys: ["photo.png"],
      complete: true,
    });
    const service = new LibraryService(runtime);
    await service.createIssue({
      id: "daily:2026-09-22:09",
      source_html: source("Legacy"),
      cover_path: "/media/legacy.png",
      references: [],
    });
    const at = "2026-09-01T00:00:00.000Z",
      manage = new LibraryManagementService(
        runtime.DB,
        runtime.MEDIA,
        true,
        () => at,
      );
    const group = await trash(manage, "daily:2026-09-22:09");
    const before = new LibraryManagementService(
      runtime.DB,
      runtime.MEDIA,
      false,
      () => "2026-09-30T23:59:59.999Z",
    );
    expect(await before.purgeDue(String(group.deletion_group_id))).toEqual({
      state: "not_due",
    });
    const due = new LibraryManagementService(
      runtime.DB,
      runtime.MEDIA,
      false,
      () => "2026-10-01T00:00:00.000Z",
    );
    expect(await due.purgeDue(String(group.deletion_group_id))).toMatchObject({
      state: "blocked",
      blockers: expect.arrayContaining([
        expect.objectContaining({ code: "asset_ownership_unconfirmed" }),
      ]),
    });
  });
});
