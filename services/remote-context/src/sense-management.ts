import { z } from "zod/v4";
import { purgeAfter } from "@personal-agent/remote-runtime";
import { canonicalJson, contentSha256, nowIso } from "./canonical";
import { ContextError } from "./errors";
import { guard, guardedBatch } from "./management-db";
import { profileSectionSchema, senseProfileSchema } from "./schemas";
import type { Env, Principal, ProfileSection, SenseProfile } from "./types";

const sectionId = z
  .string()
  .min(1)
  .max(64)
  .regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
const token = z.string().regex(/^[a-f0-9]{64}$/);
const versions = z.record(sectionId, z.string().min(1).max(160));
const structure = {
  expected_profile_sha256: token,
  expected_skill_versions: versions,
};
const commit = {
  impact_token: z.string().regex(/^impact-v1:[a-f0-9]{64}$/),
  idempotency_key: z.string().min(1).max(200),
};
export const senseSectionCreateSchema = z
  .object({
    ...structure,
    section: profileSectionSchema,
    before_section_id: sectionId.optional(),
  })
  .strict();
export const senseSectionsReorderSchema = z
  .object({ ...structure, section_ids: z.array(sectionId).max(24) })
  .strict();
export const senseManagementPreviewSchema = z
  .object({
    action: z.enum(["trash", "restore", "purge"]),
    kind: z.enum(["section", "skill"]).optional(),
    section_id: sectionId.optional(),
    deletion_group_id: z.string().max(200).optional(),
  })
  .strict()
  .refine((v) =>
    v.action === "trash"
      ? Boolean(v.kind && v.section_id) && !v.deletion_group_id
      : Boolean(v.deletion_group_id) && !v.kind && !v.section_id,
  );
export const senseSectionTrashSchema = z
  .object({ section_id: sectionId, ...structure, ...commit })
  .strict();
export const senseTrashRestoreSchema = z
  .object({
    deletion_group_id: z.string().min(1).max(200),
    expected_version: z.number().int().min(1),
    ...structure,
    ...commit,
  })
  .strict();
export const senseTrashPurgeSchema = senseTrashRestoreSchema.extend({
  confirm_permanent_delete: z.literal(true),
});
export const senseTrashListSchema = z
  .object({
    state: z.enum(["trash", "all"]).default("trash"),
    limit: z.number().int().min(1).max(100).default(50),
    offset: z.number().int().min(0).default(0),
  })
  .strict();
export const senseOperationStatusSchema = z
  .object({ idempotency_key: z.string().min(1).max(200) })
  .strict();
type Skill = {
  section_id: string;
  name: string;
  description: string;
  instructions: string;
  version: string;
  updated_at: string;
};
type Payload = { section?: ProfileSection; index: number; skill: Skill | null };
type Group = {
  deletion_group_id: string;
  kind: "section" | "skill";
  section_id: string;
  state: string;
  version: number;
  trashed_at: string;
  purge_after: string;
  payload_json: string;
  blockers_json: string;
  updated_at: string;
};

export class SenseManagementService {
  constructor(
    private readonly env: Env,
    private readonly principal: Principal,
    private readonly clock = nowIso,
  ) {}
  private get db() {
    return this.env.STATE_DB;
  }
  private get owner() {
    return this.principal.ownerId;
  }
  private async ready(write = false) {
    if (!this.principal.scopes.has(write ? "sense.write" : "sense.read"))
      throw new ContextError(
        "insufficient_scope",
        "The required Sense scope is missing",
        403,
      );
    if (
      !(await this.db
        .prepare("SELECT 1 FROM sqlite_master WHERE name='sense_trash_groups'")
        .first()) ||
      (write && this.env.SENSE_MANAGEMENT_WRITE_ENABLED !== "true")
    )
      throw new ContextError(
        "management_not_enabled",
        "Sense management writes are not enabled",
        503,
      );
  }
  private async current() {
    const row = await this.db
      .prepare(
        "SELECT profile_json,profile_sha256 FROM sense_profiles WHERE owner_id=?",
      )
      .bind(this.owner)
      .first<{ profile_json: string; profile_sha256: string }>();
    if (!row)
      throw new ContextError(
        "profile_not_found",
        "Sense profile does not exist",
        404,
      );
    const profile = senseProfileSchema.parse(JSON.parse(row.profile_json));
    const skills = (
      await this.db
        .prepare(
          "SELECT section_id,name,description,instructions,version,updated_at FROM sense_section_skills WHERE owner_id=?",
        )
        .bind(this.owner)
        .all<Skill>()
    ).results;
    return { profile, digest: row.profile_sha256, skills };
  }
  private ordinary(profile: SenseProfile, id: string) {
    const section = profile.sections.find((s) => s.id === id);
    if (!section)
      throw new ContextError(
        "section_not_found",
        "Sense section does not exist",
        404,
      );
    if (section.sensitivity !== "ordinary")
      throw new ContextError(
        "policy_restricted",
        "Sensitive sections are not changed through this surface",
        403,
      );
    return section;
  }
  private guards(digest: string, expected: Record<string, string>) {
    return [
      guard(
        this.db,
        "EXISTS (SELECT 1 FROM sense_profiles WHERE owner_id=? AND profile_sha256=?)",
        [this.owner, digest],
      ),
      guard(
        this.db,
        `NOT EXISTS (SELECT 1 FROM json_each(?) e WHERE COALESCE((SELECT version FROM sense_section_skills
        WHERE owner_id=? AND section_id=e.key),'absent')<>e.value)`,
        [canonicalJson(expected), this.owner],
      ),
    ];
  }
  private validateVersions(
    ids: string[],
    input: Record<string, string>,
    skills: Skill[],
  ) {
    const expected = Object.fromEntries(
      ids.map((id) => [
        id,
        skills.find((s) => s.section_id === id)?.version ?? "absent",
      ]),
    );
    if (canonicalJson(expected) !== canonicalJson(input))
      throw new ContextError(
        "section_skill_conflict",
        "Read the affected ordinary Skill versions before changing structure",
        409,
      );
  }
  private async profileWrite(profile: SenseProfile) {
    senseProfileSchema.parse(profile);
    const digest = await contentSha256(profile);
    return {
      digest,
      statement: this.db
        .prepare(
          "UPDATE sense_profiles SET profile_json=?,profile_sha256=?,updated_at=? WHERE owner_id=?",
        )
        .bind(canonicalJson(profile), digest, this.clock(), this.owner),
    };
  }
  private ownerWrite() {
    return this.db
      .prepare(
        "INSERT OR IGNORE INTO sense_management_owners(owner_id) VALUES(?)",
      )
      .bind(this.owner);
  }
  async create(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = senseSectionCreateSchema.parse(raw);
    const current = await this.current();
    if (input.section.sensitivity !== "ordinary")
      throw new ContextError(
        "policy_restricted",
        "Sensitive creation remains outside the remote surface",
        403,
      );
    if (current.profile.sections.some((s) => s.id === input.section.id))
      throw new ContextError(
        "section_conflict",
        "This section id already exists",
        409,
      );
    this.validateVersions(
      [input.section.id],
      input.expected_skill_versions,
      current.skills,
    );
    const index = input.before_section_id
      ? current.profile.sections.findIndex(
          (s) =>
            s.id === input.before_section_id && s.sensitivity === "ordinary",
        )
      : current.profile.sections.length;
    if (index < 0)
      throw new ContextError(
        "section_not_found",
        "The insertion target must be an ordinary section",
        404,
      );
    current.profile.sections.splice(index, 0, input.section);
    const next = await this.profileWrite(current.profile);
    await guardedBatch(
      this.db,
      this.guards(input.expected_profile_sha256, input.expected_skill_versions),
      [next.statement, this.ownerWrite()],
      "section_conflict",
    );
    return {
      changed: true,
      section_id: input.section.id,
      profile_sha256: next.digest,
    };
  }
  async reorder(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = senseSectionsReorderSchema.parse(raw);
    const current = await this.current();
    const ordinary = current.profile.sections.filter(
      (s) => s.sensitivity === "ordinary",
    );
    if (
      canonicalJson([...input.section_ids].sort()) !==
      canonicalJson(ordinary.map((s) => s.id).sort())
    )
      throw new ContextError(
        "invalid_section_order",
        "List every ordinary section exactly once",
      );
    this.validateVersions(
      ordinary.map((s) => s.id),
      input.expected_skill_versions,
      current.skills,
    );
    let index = 0;
    current.profile.sections = current.profile.sections.map((s) => {
      if (s.sensitivity === "sensitive") return s;
      const id = input.section_ids[index++];
      return ordinary.find((o) => o.id === id)!;
    });
    const next = await this.profileWrite(current.profile);
    await guardedBatch(
      this.db,
      this.guards(input.expected_profile_sha256, input.expected_skill_versions),
      [next.statement, this.ownerWrite()],
      "section_conflict",
    );
    return {
      changed: next.digest !== current.digest,
      profile_sha256: next.digest,
      section_ids: input.section_ids,
    };
  }
  private async group(id: string) {
    const row = await this.db
      .prepare(
        "SELECT * FROM sense_trash_groups WHERE owner_id=? AND deletion_group_id=?",
      )
      .bind(this.owner, id)
      .first<Group>();
    if (!row)
      throw new ContextError(
        "deletion_group_not_found",
        "Deletion group does not exist",
        404,
      );
    return row;
  }
  private async blockers(
    group: Group,
    action: "restore" | "purge",
    profile: SenseProfile,
    skills: Skill[],
  ) {
    const blockers: { code: string; message: string }[] = [];
    const section = profile.sections.find((s) => s.id === group.section_id);
    const payload = JSON.parse(group.payload_json) as Payload;
    if (action === "restore") {
      if (group.kind === "section" && section)
        blockers.push({
          code: "section_conflict",
          message: "A section already uses this id.",
        });
      if (
        group.kind === "skill" &&
        (!section || section.sensitivity !== "ordinary")
      )
        blockers.push({
          code: "parent_unavailable",
          message: "Restore the ordinary parent section first.",
        });
      if (
        payload.skill &&
        skills.some((s) => s.section_id === group.section_id)
      )
        blockers.push({
          code: "skill_conflict",
          message: "The parent already has a Skill.",
        });
      if (group.kind === "section" && profile.sections.length >= 24)
        blockers.push({
          code: "profile_full",
          message: "The profile has no room for another section.",
        });
    } else if (group.kind === "section") {
      if (
        await this.db
          .prepare(
            "SELECT 1 FROM sense_trash_groups WHERE owner_id=? AND section_id=? AND deletion_group_id<>? AND kind='skill' AND state IN ('trashed','blocked')",
          )
          .bind(this.owner, group.section_id, group.deletion_group_id)
          .first()
      )
        blockers.push({
          code: "independent_skill_deletion",
          message:
            "A previously removed Skill keeps its own group and deadline.",
        });
    }
    return blockers;
  }
  private async impact(input: z.infer<typeof senseManagementPreviewSchema>) {
    const current = await this.current();
    const group = input.deletion_group_id
      ? await this.group(input.deletion_group_id)
      : undefined;
    const id = group?.section_id ?? input.section_id!;
    const kind = group?.kind ?? input.kind!;
    let payload: Payload;
    if (input.action === "trash") {
      const section = this.ordinary(current.profile, id);
      payload = {
        ...(kind === "section" ? { section } : {}),
        index: current.profile.sections.indexOf(section),
        skill: current.skills.find((s) => s.section_id === id) ?? null,
      };
      if (kind === "skill" && !payload.skill)
        throw new ContextError(
          "skill_not_found",
          "The ordinary section has no Skill",
          404,
        );
    } else payload = JSON.parse(group!.payload_json) as Payload;
    const expectedSkills = {
      [id]:
        current.skills.find((s) => s.section_id === id)?.version ?? "absent",
    };
    const blockers = group
      ? await this.blockers(
          group,
          input.action as "restore" | "purge",
          current.profile,
          current.skills,
        )
      : [];
    const material = {
      action: input.action,
      kind,
      section_id: id,
      profile_sha256: current.digest,
      expected_skill_versions: expectedSkills,
      group: group
        ? {
            id: group.deletion_group_id,
            version: group.version,
            state: group.state,
          }
        : null,
      payload,
      blockers,
    };
    return {
      ...material,
      impact_token: `impact-v1:${await contentSha256(material)}`,
      current,
    };
  }
  async preview(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const input = senseManagementPreviewSchema.parse(raw);
    const { current: _current, payload, ...impact } = await this.impact(input);
    return {
      ...impact,
      members: [
        ...(payload.section
          ? [
              {
                kind: "section",
                id: payload.section.id,
                title: payload.section.purpose,
              },
            ]
          : []),
        ...(payload.skill
          ? [
              {
                kind: "skill",
                id: impact.section_id,
                title: payload.skill.name,
                version: payload.skill.version,
              },
            ]
          : []),
      ],
      restore_index: payload.index,
      retention_days: 30,
    };
  }
  private async replay(key: string, input: unknown) {
    const fingerprint = await contentSha256(input);
    const row = await this.db
      .prepare(
        "SELECT fingerprint,result_json FROM sense_operation_receipts WHERE owner_id=? AND request_key=?",
      )
      .bind(this.owner, key)
      .first<{ fingerprint: string; result_json: string }>();
    if (row && row.fingerprint !== fingerprint)
      throw new ContextError(
        "request_key_conflict",
        "The request key already identifies another change",
        409,
      );
    return {
      fingerprint,
      result: row
        ? (JSON.parse(row.result_json) as Record<string, unknown>)
        : null,
    };
  }
  private receipt(
    key: string,
    fingerprint: string,
    result: Record<string, unknown>,
  ) {
    return this.db
      .prepare("INSERT INTO sense_operation_receipts VALUES(?,?,?,?)")
      .bind(this.owner, key, fingerprint, canonicalJson(result));
  }
  async trash(
    kind: "section" | "skill",
    raw: unknown,
  ): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = senseSectionTrashSchema.parse(raw);
    const replay = await this.replay(input.idempotency_key, {
      operation: `${kind}_trash`,
      ...input,
    });
    if (replay.result) return replay.result;
    const impact = await this.impact({
      action: "trash",
      kind,
      section_id: input.section_id,
    });
    if (
      input.impact_token !== impact.impact_token ||
      input.expected_profile_sha256 !== impact.profile_sha256
    )
      throw new ContextError(
        "impact_conflict",
        "The reviewed Sense structure changed",
        409,
      );
    this.validateVersions(
      [input.section_id],
      input.expected_skill_versions,
      impact.current.skills,
    );
    const profile = impact.current.profile;
    if (kind === "section")
      profile.sections = profile.sections.filter(
        (s) => s.id !== input.section_id,
      );
    const next = await this.profileWrite(profile);
    const now = this.clock();
    const id = `sense_trash_${crypto.randomUUID().replaceAll("-", "")}`;
    const result = {
      state: "trashed",
      deletion_group_id: id,
      version: 1,
      trashed_at: now,
      purge_after: purgeAfter(now),
      profile_sha256: next.digest,
    };
    await guardedBatch(
      this.db,
      this.guards(input.expected_profile_sha256, input.expected_skill_versions),
      [
        next.statement,
        this.db
          .prepare(
            "DELETE FROM sense_section_skills WHERE owner_id=? AND section_id=?",
          )
          .bind(this.owner, input.section_id),
        this.db
          .prepare(
            `INSERT INTO sense_trash_groups(owner_id,deletion_group_id,kind,section_id,state,trashed_at,purge_after,payload_json,updated_at)
        VALUES(?,?,?,?,'trashed',?,?,?,?)`,
          )
          .bind(
            this.owner,
            id,
            kind,
            input.section_id,
            now,
            result.purge_after,
            canonicalJson(impact.payload),
            now,
          ),
        this.ownerWrite(),
        this.receipt(input.idempotency_key, replay.fingerprint, result),
      ],
      "section_conflict",
    );
    return result;
  }
  async restore(raw: unknown): Promise<Record<string, unknown>> {
    return this.finish("restore", raw);
  }
  async purge(raw: unknown): Promise<Record<string, unknown>> {
    return this.finish("purge", raw);
  }
  private async finish(
    action: "restore" | "purge",
    raw: unknown,
  ): Promise<Record<string, unknown>> {
    await this.ready(true);
    const input = (
      action === "purge" ? senseTrashPurgeSchema : senseTrashRestoreSchema
    ).parse(raw);
    if (action === "purge" && this.principal.auth === "sync-device")
      throw new ContextError(
        "owner_confirmation_required",
        "An authenticated owner must request permanent deletion",
        403,
      );
    const replay = await this.replay(input.idempotency_key, {
      operation: action,
      ...input,
    });
    if (replay.result) return replay.result;
    const impact = await this.impact({
      action,
      deletion_group_id: input.deletion_group_id,
    });
    if (
      !impact.group ||
      !["trashed", "blocked"].includes(impact.group.state) ||
      input.expected_version !== impact.group.version ||
      impact.impact_token !== input.impact_token ||
      impact.profile_sha256 !== input.expected_profile_sha256
    )
      throw new ContextError(
        "impact_conflict",
        "The reviewed deletion or profile changed",
        409,
      );
    this.validateVersions(
      [impact.section_id],
      input.expected_skill_versions,
      impact.current.skills,
    );
    if (impact.blockers.length)
      return {
        state: "blocked",
        deletion_group_id: input.deletion_group_id,
        blockers: impact.blockers,
      };
    const writes: D1PreparedStatement[] = [];
    let digest = impact.current.digest;
    if (action === "restore") {
      if (impact.payload.section)
        impact.current.profile.sections.splice(
          Math.min(
            impact.payload.index,
            impact.current.profile.sections.length,
          ),
          0,
          impact.payload.section,
        );
      const next = await this.profileWrite(impact.current.profile);
      digest = next.digest;
      writes.push(next.statement);
      const skill = impact.payload.skill;
      if (skill)
        writes.push(
          this.db
            .prepare(
              "INSERT INTO sense_section_skills(owner_id,section_id,name,description,instructions,version,updated_at) VALUES(?,?,?,?,?,?,?)",
            )
            .bind(
              this.owner,
              impact.section_id,
              skill.name,
              skill.description,
              skill.instructions,
              skill.version,
              skill.updated_at,
            ),
        );
    }
    const result = {
      state: action === "restore" ? "restored" : "purged",
      deletion_group_id: input.deletion_group_id,
      version: input.expected_version + 1,
      profile_sha256: digest,
    };
    writes.push(
      this.db
        .prepare(
          "UPDATE sense_trash_groups SET state=?,version=version+1,payload_json='{}',blockers_json='[]',updated_at=? WHERE owner_id=? AND deletion_group_id=?",
        )
        .bind(result.state, this.clock(), this.owner, input.deletion_group_id),
      this.receipt(input.idempotency_key, replay.fingerprint, result),
    );
    const guards = [
      ...this.guards(
        input.expected_profile_sha256,
        input.expected_skill_versions,
      ),
      guard(
        this.db,
        "EXISTS (SELECT 1 FROM sense_trash_groups WHERE owner_id=? AND deletion_group_id=? AND version=? AND state IN ('trashed','blocked'))",
        [this.owner, input.deletion_group_id, input.expected_version],
      ),
    ];
    if (action === "purge" && impact.kind === "section")
      guards.push(
        this.independentSkillGuard(impact.section_id, input.deletion_group_id),
      );
    await guardedBatch(this.db, guards, writes, "section_conflict");
    return result;
  }
  private independentSkillGuard(section: string, group: string) {
    return guard(
      this.db,
      `NOT EXISTS (SELECT 1 FROM sense_trash_groups
    WHERE owner_id=? AND section_id=? AND deletion_group_id<>? AND kind='skill' AND state IN ('trashed','blocked'))`,
      [this.owner, section, group],
    );
  }
  async trashList(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const input = senseTrashListSchema.parse(raw);
    const result = await this.db
      .prepare(
        `SELECT * FROM sense_trash_groups WHERE owner_id=? ${input.state === "trash" ? "AND state IN ('trashed','blocked')" : ""}
      ORDER BY trashed_at,deletion_group_id LIMIT ? OFFSET ?`,
      )
      .bind(this.owner, input.limit + 1, input.offset)
      .all<Group>();
    const current = await this.current();
    return {
      groups: await Promise.all(
        result.results
          .slice(0, input.limit)
          .map(async ({ payload_json, blockers_json: _blockers, ...group }) => {
            const payload = JSON.parse(payload_json) as Payload;
            return {
              ...group,
              title:
                payload.section?.purpose ??
                payload.skill?.name ??
                group.section_id,
              members: [
                ...(payload.section ? ["section"] : []),
                ...(payload.skill ? ["skill"] : []),
              ],
              blockers: ["trashed", "blocked"].includes(group.state)
                ? await this.blockers(
                    { ...group, payload_json, blockers_json: "[]" },
                    "purge",
                    current.profile,
                    current.skills,
                  )
                : [],
            };
          }),
      ),
      management_enabled: this.env.SENSE_MANAGEMENT_WRITE_ENABLED === "true",
      maintenance: {
        enabled: this.env.TRASH_SWEEP_ENABLED === "true",
        recent: await this.db
          .prepare(
            "SELECT started_at,finished_at,state,result_json FROM corpus_maintenance_runs WHERE run_id='trash'",
          )
          .first(),
      },
      has_more: result.results.length > input.limit,
      next_offset:
        result.results.length > input.limit ? input.offset + input.limit : null,
    };
  }
  async status(raw: unknown): Promise<Record<string, unknown>> {
    await this.ready();
    const input = senseOperationStatusSchema.parse(raw);
    const row = await this.db
      .prepare(
        "SELECT result_json FROM sense_operation_receipts WHERE owner_id=? AND request_key=?",
      )
      .bind(this.owner, input.idempotency_key)
      .first<{ result_json: string }>();
    return row
      ? { found: true, result: JSON.parse(row.result_json) }
      : { found: false, state: "not_confirmed" };
  }
  async auditDue(id: string): Promise<Record<string, unknown>> {
    const group = await this.group(id),
      current = await this.current();
    return {
      state: "dry_run",
      deletion_group_id: id,
      purge_after: group.purge_after,
      blockers: await this.blockers(
        group,
        "purge",
        current.profile,
        current.skills,
      ),
    };
  }
  async purgeDue(id: string): Promise<Record<string, unknown>> {
    const group = await this.group(id);
    const current = await this.current();
    if (
      !["trashed", "blocked"].includes(group.state) ||
      Date.parse(group.purge_after) > Date.parse(this.clock())
    )
      return { state: "not_due" };
    const blockers = await this.blockers(
      group,
      "purge",
      current.profile,
      current.skills,
    );
    if (blockers.length) {
      await this.db
        .prepare(
          "UPDATE sense_trash_groups SET state='blocked',blockers_json=?,updated_at=? WHERE owner_id=? AND deletion_group_id=? AND version=? AND state IN ('trashed','blocked')",
        )
        .bind(
          canonicalJson(blockers),
          this.clock(),
          this.owner,
          id,
          group.version,
        )
        .run();
      return { state: "blocked", deletion_group_id: id, blockers };
    }
    await guardedBatch(
      this.db,
      [
        guard(
          this.db,
          `EXISTS (SELECT 1 FROM sense_trash_groups WHERE owner_id=? AND deletion_group_id=? AND version=?
      AND state IN ('trashed','blocked') AND purge_after<=?)`,
          [this.owner, id, group.version, this.clock()],
        ),
        ...(group.kind === "section"
          ? [this.independentSkillGuard(group.section_id, id)]
          : []),
      ],
      [
        this.db
          .prepare(
            "UPDATE sense_trash_groups SET state='purged',payload_json='{}',version=version+1,updated_at=? WHERE owner_id=? AND deletion_group_id=?",
          )
          .bind(this.clock(), this.owner, id),
      ],
    );
    return { state: "purged", deletion_group_id: id };
  }
}
