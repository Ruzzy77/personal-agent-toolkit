import { env } from "cloudflare:test";
import { expect, it } from "vitest";
import { SenseManagementService } from "../src/sense-management";
import { SenseService } from "../src/sense";
import type { Env, Principal } from "../src/types";

const runtime = {
  ...(env as unknown as Env),
  SENSE_MANAGEMENT_WRITE_ENABLED: "true",
};
async function fixture() {
  const principal: Principal = {
    ownerId: `sense-management-${crypto.randomUUID()}`,
    scopes: new Set(["sense.read", "sense.write"]),
    auth: "site",
    clientId: "test",
  };
  const sense = new SenseService(runtime.STATE_DB, principal.ownerId);
  await sense.importProfile({
    schema_version: 2,
    sections: [
      {
        id: "first",
        purpose: "First",
        text: "ordinary",
        origins: ["user_set"],
        sensitivity: "ordinary",
      },
      {
        id: "private",
        purpose: "Private",
        text: "not-for-management-output",
        origins: ["user_set"],
        sensitivity: "sensitive",
      },
      {
        id: "last",
        purpose: "Last",
        text: "ordinary",
        origins: ["user_set"],
        sensitivity: "ordinary",
      },
    ],
  });
  await sense.reviseSkill({
    section_id: "first",
    expected_version: "absent",
    new_skill: {
      name: "first-skill",
      description: "Ordinary method",
      instructions: "Preserved method",
    },
  });
  return {
    principal,
    sense,
    manage: new SenseManagementService(runtime, principal),
  };
}
const key = () => crypto.randomUUID();
async function trash(
  manage: SenseManagementService,
  kind: "section" | "skill",
  section = "first",
) {
  const preview = await manage.preview({
    action: "trash",
    kind,
    section_id: section,
  });
  const input = {
    section_id: section,
    impact_token: preview.impact_token,
    expected_profile_sha256: preview.profile_sha256,
    expected_skill_versions: preview.expected_skill_versions,
    idempotency_key: key(),
  };
  const result = await manage.trash(kind, input);
  expect(await manage.trash(kind, input)).toEqual(result);
  return result;
}
async function restore(manage: SenseManagementService, id: unknown) {
  const preview = await manage.preview({
    action: "restore",
    deletion_group_id: id,
  });
  return manage.restore({
    deletion_group_id: id,
    expected_version: 1,
    impact_token: preview.impact_token,
    expected_profile_sha256: preview.profile_sha256,
    expected_skill_versions: preview.expected_skill_versions,
    idempotency_key: key(),
  });
}
it("creates and reorders ordinary sections while preserving sensitive slots and checking Skill versions", async () => {
  const { sense, manage, principal } = await fixture();
  const read = await sense.read("index");
  const skill = (
    read.sections as Array<{ id: string; skill?: { version: string } }>
  ).find((s) => s.id === "first")!.skill!;
  await expect(
    manage.reorder({
      section_ids: ["last", "first"],
      expected_profile_sha256: read.profile_sha256,
      expected_skill_versions: { first: "absent", last: "absent" },
    }),
  ).rejects.toMatchObject({ code: "section_skill_conflict" });
  await manage.reorder({
    section_ids: ["last", "first"],
    expected_profile_sha256: read.profile_sha256,
    expected_skill_versions: { first: skill.version, last: "absent" },
  });
  const stored = await runtime.STATE_DB.prepare(
    "SELECT profile_json FROM sense_profiles WHERE owner_id=?",
  )
    .bind(principal.ownerId)
    .first<{ profile_json: string }>();
  expect(
    JSON.parse(stored!.profile_json).sections.map((s: { id: string }) => s.id),
  ).toEqual(["last", "private", "first"]);
  const next = await sense.read("index");
  await manage.create({
    section: {
      id: "new",
      purpose: "New",
      text: "New ordinary instruction",
      origins: ["user_set"],
      sensitivity: "ordinary",
    },
    expected_profile_sha256: next.profile_sha256,
    expected_skill_versions: { new: "absent" },
  });
  await expect(
    manage.preview({ action: "trash", kind: "section", section_id: "private" }),
  ).rejects.toMatchObject({ code: "policy_restricted" });
  expect(JSON.stringify(await manage.trashList({}))).not.toContain(
    "not-for-management-output",
  );
});
it("removes/restores section ownership without reviving an independently trashed Skill", async () => {
  const { sense, manage } = await fixture();
  const skill = await trash(manage, "skill");
  const section = await trash(manage, "section");
  await expect(restore(manage, skill.deletion_group_id)).resolves.toMatchObject(
    { state: "blocked" },
  );
  expect(await restore(manage, section.deletion_group_id)).toMatchObject({
    state: "restored",
  });
  expect(JSON.stringify(await sense.read("sections", ["first"]))).not.toContain(
    "Preserved method",
  );
  expect(await restore(manage, skill.deletion_group_id)).toMatchObject({
    state: "restored",
  });
  expect(JSON.stringify(await sense.read("sections", ["first"]))).toContain(
    "Preserved method",
  );
  await expect(sense.importSkills([])).rejects.toMatchObject({
    code: "management_conflict",
  });
});
it("blocks all-or-none restore collisions and purges only newly created due trash", async () => {
  const { sense, manage, principal } = await fixture();
  const clocked = new SenseManagementService(
    runtime,
    principal,
    () => "2026-01-01T00:00:00.000Z",
  );
  const deleted = await trash(clocked, "section");
  const read = await sense.read("index");
  await manage.create({
    section: {
      id: "first",
      purpose: "Replacement",
      text: "Do not replace",
      origins: ["user_set"],
      sensitivity: "ordinary",
    },
    expected_profile_sha256: read.profile_sha256,
    expected_skill_versions: { first: "absent" },
  });
  expect(await restore(manage, deleted.deletion_group_id)).toMatchObject({
    state: "blocked",
  });
  expect(
    await new SenseManagementService(
      runtime,
      principal,
      () => "2026-01-30T23:59:59.999Z",
    ).purgeDue(String(deleted.deletion_group_id)),
  ).toMatchObject({ state: "not_due" });
  expect(
    await new SenseManagementService(
      runtime,
      principal,
      () => "2026-01-31T00:00:00.000Z",
    ).purgeDue(String(deleted.deletion_group_id)),
  ).toMatchObject({ state: "purged" });
  expect(JSON.stringify(await sense.read("sections", ["first"]))).toContain(
    "Do not replace",
  );
});
