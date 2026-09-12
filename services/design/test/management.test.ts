import { env } from "cloudflare:test";
import { describe, it, expect } from "vitest";
import { DesignService } from "../src/service";
import { DesignManagementService } from "../src/management";
import type { Env } from "../src/types";
const runtime = env as unknown as Env;
const metadata = (id: string) => ({
  id,
  name: id,
  description: "Isolated test",
  version: "1.0",
  status: "validated",
  selection_ready: true,
  kind: "recipe",
  visibility: "private",
  pattern_refs: [],
  formats: ["web"],
});
async function trash(
  manage: DesignManagementService,
  kind: "recipe" | "file",
  id: string,
  path?: string,
) {
  const p = await manage.preview({
    action: "trash",
    kind,
    recipe_id: id,
    ...(path ? { path } : {}),
  });
  const input = {
    recipe_id: id,
    ...(path ? { path } : {}),
    expected_version: p.expected_version,
    impact_token: p.impact_token,
    idempotency_key: crypto.randomUUID(),
  };
  return manage.trash(kind, input);
}
async function purge(manage: DesignManagementService, id: unknown) {
  const p = await manage.preview({ action: "purge", deletion_group_id: id });
  return manage.purge({
    deletion_group_id: id,
    expected_version: p.expected_version,
    impact_token: p.impact_token,
    idempotency_key: crypto.randomUUID(),
    confirm_permanent_delete: true,
  });
}
describe("Design lifecycle and media identity", () => {
  it("trashes a large manifest atomically and resumes bounded asset and file deletion", async () => {
    const service = new DesignService(runtime),
      manage = service.management();
    await service.createRecipe(metadata("large-managed-recipe"));
    for (let index = 0; index < 105; index++)
      await service.uploadFile({
        id: "large-managed-recipe",
        path: `part-${index}.txt`,
        base64: btoa(String(index)),
        content_type: "text/plain",
        expected_file_revision: 0,
      });
    const group = await trash(manage, "recipe", "large-managed-recipe");
    expect(await service.readRecipe("large-managed-recipe")).toBeNull();
    const count = await runtime.DB.prepare(
      "SELECT count(*) AS count FROM design_files WHERE recipe_id='large-managed-recipe' AND trash_group_id=?",
    )
      .bind(group.deletion_group_id)
      .first<{ count: number }>();
    expect(count?.count).toBe(105);
    let result = await purge(manage, group.deletion_group_id),
      passes = 1;
    expect(result.state).toBe("purging");
    while (result.state === "purging" && passes++ < 9)
      result = await purge(manage,group.deletion_group_id);
    expect(result.state).toBe("purged");
    expect(passes).toBeGreaterThan(5);
    expect(
      (
        await runtime.DB.prepare(
          "SELECT count(*) AS count FROM design_files WHERE recipe_id='large-managed-recipe'",
        ).first<{ count: number }>()
      )?.count,
    ).toBe(0);
  });

  it("separates MIME identity, rejects stale file saves without overwriting bytes, and checks included file revisions", async () => {
    const service = new DesignService(runtime);
    await service.createRecipe(metadata("managed-recipe"));
    await service.uploadFile({
      id: "managed-recipe",
      path: "file.txt",
      base64: btoa("same"),
      content_type: "text/plain",
      expected_file_revision: 0,
    });
    const preview = await service.management().preview({
      action: "trash",
      kind: "recipe",
      recipe_id: "managed-recipe",
    });
    await service.uploadFile({
      id: "managed-recipe",
      path: "file.txt",
      base64: btoa("same"),
      content_type: "text/html",
      expected_file_revision: 1,
    });
    await expect(
      service.management().trash("recipe", {
        recipe_id: "managed-recipe",
        expected_version: preview.expected_version,
        impact_token: preview.impact_token,
        idempotency_key: crypto.randomUUID(),
      }),
    ).rejects.toMatchObject({ code: "impact_conflict" });
    await expect(
      service.uploadFile({
        id: "managed-recipe",
        path: "file.txt",
        base64: btoa("different"),
        content_type: "text/plain",
        expected_file_revision: 1,
      }),
    ).rejects.toMatchObject({ code: "version_conflict" });
    const loaded = await service.readFile("managed-recipe", "file.txt");
    expect(loaded?.record.content_type).toBe("text/html");
    expect(await loaded?.object.text()).toBe("same");
  });
  it("keeps earlier file deletion groups separate and does not restore their contents with the recipe", async () => {
    const service = new DesignService(runtime),
      manage = service.management();
    await service.createRecipe(metadata("grouped-recipe"));
    for (const path of ["old.txt", "new.txt"])
      await service.uploadFile({
        id: "grouped-recipe",
        path,
        base64: btoa(path),
        content_type: "text/plain",
        expected_file_revision: 0,
      });
    const earlier = await trash(manage, "file", "grouped-recipe", "old.txt"),
      group = await trash(manage, "recipe", "grouped-recipe");
    expect(await purge(manage, group.deletion_group_id)).toMatchObject({
      state: "blocked",
      blockers: expect.arrayContaining([
        expect.objectContaining({ code: "independent_deletion_group" }),
      ]),
    });
    const p = await manage.preview({
      action: "restore",
      deletion_group_id: group.deletion_group_id,
    });
    await manage.restore({
      deletion_group_id: group.deletion_group_id,
      expected_version: p.expected_version,
      impact_token: p.impact_token,
      idempotency_key: crypto.randomUUID(),
    });
    expect(
      (await service.readRecipe("grouped-recipe"))?.files.map((f) => f.path),
    ).toEqual(["new.txt"]);
    expect(await service.readFile("grouped-recipe", "old.txt")).toBeNull();
    expect(earlier.purge_after).toBeDefined();
  });
  it("resumes R2 deletion after failure, keeps the DB until objects finish, and blocks restoration once purge is claimed", async () => {
    const service = new DesignService(runtime);
    await service.createRecipe(metadata("retry-recipe"));
    await service.uploadFile({
      id: "retry-recipe",
      path: "file.txt",
      base64: btoa("payload"),
      content_type: "text/plain",
      expected_file_revision: 0,
    });
    const failing = new DesignManagementService(
      runtime.DB,
      {
        delete: async () => {
          throw new Error("isolated failure");
        },
      } as unknown as R2Bucket,
      true,
    );
    const group = await trash(failing, "recipe", "retry-recipe");
    expect(await purge(failing, group.deletion_group_id)).toMatchObject({
      state: "purging",
      restorable: false,
    });
    const p = await failing.preview({
      action: "restore",
      deletion_group_id: group.deletion_group_id,
    });
    await expect(
      failing.restore({
        deletion_group_id: group.deletion_group_id,
        expected_version: p.expected_version,
        impact_token: p.impact_token,
        idempotency_key: crypto.randomUUID(),
      }),
    ).rejects.toMatchObject({ code: "version_conflict" });
    expect(
      await runtime.DB.prepare(
        "SELECT id FROM design_recipes WHERE id='retry-recipe'",
      ).first(),
    ).not.toBeNull();
    expect(
      await purge(service.management(), group.deletion_group_id),
    ).toMatchObject({ state: "purged" });
    expect(
      await runtime.DB.prepare(
        "SELECT id FROM design_recipes WHERE id='retry-recipe'",
      ).first(),
    ).toBeNull();
  });
});
