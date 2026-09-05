import { canonicalJson, contentSha256, nowIso, sha256Hex } from "./canonical";
import { ContextError } from "./errors";
import {
  profileSectionSchema,
  senseProfileSchema,
  senseReviseSchema,
  senseSkillReviseSchema,
} from "./schemas";
import type { ProfileSection, SectionSkill, SenseProfile } from "./types";

interface ProfileRow {
  profile_json: string;
  profile_sha256: string;
  updated_at: string;
}

interface SkillRow {
  section_id: string;
  name: string;
  description: string;
  instructions: string;
  version: string;
  updated_at: string;
}

const SECTION_PRESENTATION: Record<string, { title: string; group: string }> = {
  "questions-and-choices": { title: "질문과 선택", group: "질문과 답" },
  "scope-and-checking": { title: "업무 범위", group: "질문과 답" },
  "evidence-and-judgment": { title: "자료와 해석", group: "자료와 표현" },
  "explanation-and-output": { title: "설명과 산출물 구성", group: "자료와 표현" },
  "conversation-and-writing": { title: "대화와 글", group: "자료와 표현" },
  "visual-production": { title: "시각 설계와 제작", group: "자료와 표현" },
  "research-exploration": { title: "연구 탐색", group: "연구" },
  "research-review": { title: "연구 검토", group: "연구" },
  "research-and-long-term-goals": {
    title: "관계 학습 연구",
    group: "장기 맥락",
  },
  "what-to-keep": { title: "기억 체계", group: "장기 맥락" },
};
const GROUP_ORDER = ["질문과 답", "자료와 표현", "연구", "장기 맥락", "기타 지침"];
const ORIGIN_LABELS = {
  user_set: "사용자 지정",
  learned_from_results: "경험 학습",
} as const;

function skillProjection(skill: SkillRow, includeInstructions: boolean) {
  return {
    name: skill.name,
    description: skill.description,
    version: skill.version,
    updated_at: skill.updated_at,
    provenance: "user_approved_sense_skill",
    scope: "linked_section",
    source_evidence: false,
    ...(includeInstructions ? { instructions: skill.instructions } : {}),
  };
}

async function sectionDigest(section: ProfileSection): Promise<string> {
  return contentSha256(section);
}

function serializeSkill(
  name: string,
  description: string,
  instructions: string,
): string {
  return (
    "---\n" +
    `name: ${name.trim()}\n` +
    `description: ${JSON.stringify(description.trim())}\n` +
    "---\n\n" +
    `${instructions.replace(/\r\n?/g, "\n").trim()}\n`
  );
}

export class SenseService {
  constructor(
    private readonly db: D1Database,
    private readonly ownerId: string,
  ) {}

  private async profile(): Promise<{
    profile: SenseProfile;
    digest: string;
    updatedAt: string;
  }> {
    const row = await this.db
      .prepare(
        "SELECT profile_json, profile_sha256, updated_at FROM sense_profiles WHERE owner_id = ?",
      )
      .bind(this.ownerId)
      .first<ProfileRow>();
    if (!row) {
      throw new ContextError(
        "profile_not_found",
        "the Sense profile has not been migrated",
        404,
      );
    }
    const parsed = senseProfileSchema.safeParse(JSON.parse(row.profile_json));
    if (!parsed.success) {
      throw new ContextError(
        "invalid_stored_profile",
        "the stored Sense profile is invalid",
        500,
      );
    }
    return {
      profile: parsed.data,
      digest: row.profile_sha256,
      updatedAt: row.updated_at,
    };
  }

  private async skills(): Promise<Map<string, SkillRow>> {
    const result = await this.db
      .prepare(
        `SELECT section_id, name, description, instructions, version, updated_at
         FROM sense_section_skills WHERE owner_id = ?`,
      )
      .bind(this.ownerId)
      .all<SkillRow>();
    return new Map(result.results.map((row) => [row.section_id, row]));
  }

  async verificationState(): Promise<Record<string, unknown>> {
    const stored = await this.profile();
    const skills = [...(await this.skills()).values()]
      .sort((left, right) => left.section_id.localeCompare(right.section_id))
      .map((skill) => ({
        section_id: skill.section_id,
        name: skill.name,
        description: skill.description,
        instructions: skill.instructions,
        updated_at: skill.updated_at,
      }));
    return {
      profile_sha256: stored.digest,
      skill_count: skills.length,
      skills_sha256: await contentSha256(skills),
    };
  }

  async read(
    view: "index" | "sections" | "full",
    sectionIds?: string[] | null,
  ): Promise<Record<string, unknown>> {
    const stored = await this.profile();
    const skills = await this.skills();
    if (view === "index") {
      return {
        sections: stored.profile.sections.map((section) => {
          if (section.sensitivity === "sensitive") {
            return {
              id: section.id,
              sensitivity: section.sensitivity,
              available_by_explicit_id: true,
            };
          }
          const skill = skills.get(section.id);
          return {
            id: section.id,
            purpose: section.purpose,
            sensitivity: section.sensitivity,
            ...(skill ? { skill: skillProjection(skill, false) } : {}),
          };
        }),
      };
    }

    if (view === "full") {
      const sections = stored.profile.sections.filter(
        (section) => section.sensitivity === "ordinary",
      );
      return {
        schema_version: 2,
        profile_sha256: stored.digest,
        section_count: sections.length,
        updated_at: stored.updatedAt,
        profile: {
          schema_version: 2,
          sections: sections.map((section) => {
            const skill = skills.get(section.id);
            return {
              ...section,
              ...(skill ? { skill: skillProjection(skill, true) } : {}),
            };
          }),
        },
      };
    }

    if (!sectionIds || sectionIds.length === 0) {
      throw new ContextError(
        "invalid_request",
        "section_ids are required when view=sections",
      );
    }
    const byId = new Map(
      stored.profile.sections.map((section) => [section.id, section]),
    );
    const sections = [];
    for (const id of [...new Set(sectionIds)]) {
      const section = byId.get(id);
      if (!section) {
        throw new ContextError(
          "section_not_found",
          "Sense section was not found",
          404,
          { section_id: id },
        );
      }
      const skill = skills.get(id);
      sections.push({
        ...section,
        section_sha256: await sectionDigest(section),
        ...(skill ? { skill: skillProjection(skill, true) } : {}),
      });
    }
    return { sections };
  }

  async overview(): Promise<Record<string, unknown>> {
    const stored = await this.profile();
    const skills = await this.skills();
    const grouped = new Map<string, Array<Record<string, unknown>>>();
    for (const name of GROUP_ORDER) grouped.set(name, []);
    for (const section of stored.profile.sections) {
      if (section.sensitivity === "sensitive") continue;
      const presentation = SECTION_PRESENTATION[section.id] ?? {
        title: "기타 지침",
        group: "기타 지침",
      };
      const skill = skills.get(section.id);
      grouped.get(presentation.group)?.push({
        title: presentation.title,
        purpose: section.purpose,
        text: section.text,
        origins: section.origins.map((origin) => ORIGIN_LABELS[origin]),
        ...(skill ? { skill: skillProjection(skill, true) } : {}),
      });
    }
    return {
      title: "Sense 지침",
      description:
        "사용자 의도와 의사결정에 관한 범용 지침과 연결된 작업 방법입니다. " +
        "대화 기록과 프로젝트 자료는 각 시스템에서 관리합니다.",
      groups: GROUP_ORDER.flatMap((title) => {
        const sections = grouped.get(title) ?? [];
        return sections.length > 0 ? [{ title, sections }] : [];
      }),
      updated_at: stored.updatedAt,
      privacy: [
        "Sense는 장기 지침과 사용자가 반영한 Section Skill을 저장합니다.",
        "민감 정보 저장은 사용자의 직접 승인을 따릅니다.",
        "이 화면은 일반 지침을 표시합니다.",
      ],
    };
  }

  async revise(input: unknown): Promise<Record<string, unknown>> {
    const parsed = senseReviseSchema.parse(input);
    const stored = await this.profile();
    const current = new Map(
      stored.profile.sections.map((section) => [section.id, section]),
    );
    const changedIds = new Set<string>();

    for (const change of parsed.changes) {
      if (changedIds.has(change.section_id)) {
        throw new ContextError(
          "duplicate_section_change",
          "a Sense section may be replaced only once per revision",
        );
      }
      changedIds.add(change.section_id);
      const section = current.get(change.section_id);
      if (!section) {
        throw new ContextError(
          "section_not_found",
          "Sense section was not found",
          404,
        );
      }
      if (section.sensitivity !== "ordinary") {
        throw new ContextError(
          "local_confirmation_required",
          "sensitive Sense changes remain local-only",
          403,
        );
      }
      if ((await sectionDigest(section)) !== change.previous_section_sha256) {
        throw new ContextError(
          "section_conflict",
          "Sense changed after it was read",
          409,
          { section_id: change.section_id },
        );
      }
      current.set(
        change.section_id,
        profileSectionSchema.parse(change.new_section),
      );
    }

    const nextProfile: SenseProfile = {
      schema_version: 2,
      sections: stored.profile.sections.map(
        (section) => current.get(section.id) ?? section,
      ),
    };
    senseProfileSchema.parse(nextProfile);
    const nextJson = canonicalJson(nextProfile);
    const nextDigest = await sha256Hex(nextJson);
    if (nextDigest === stored.digest) {
      return {
        changed: false,
        schema_version: 2,
        profile_sha256: stored.digest,
        section_count: nextProfile.sections.length,
        updated_at: stored.updatedAt,
      };
    }
    const updatedAt = nowIso();
    const result = await this.db
      .prepare(
        `UPDATE sense_profiles
         SET profile_json = ?, profile_sha256 = ?, updated_at = ?
         WHERE owner_id = ? AND profile_sha256 = ?`,
      )
      .bind(nextJson, nextDigest, updatedAt, this.ownerId, stored.digest)
      .run();
    if ((result.meta.changes ?? 0) !== 1) {
      throw new ContextError(
        "section_conflict",
        "Sense changed after it was read",
        409,
      );
    }
    return {
      changed: true,
      schema_version: 2,
      profile_sha256: nextDigest,
      section_count: nextProfile.sections.length,
      updated_at: updatedAt,
    };
  }

  async reviseSkill(input: unknown): Promise<Record<string, unknown>> {
    const parsed = senseSkillReviseSchema.parse(input);
    const stored = await this.profile();
    const section = stored.profile.sections.find(
      (candidate) => candidate.id === parsed.section_id,
    );
    if (!section) {
      throw new ContextError(
        "section_not_found",
        "Sense section was not found",
        404,
      );
    }
    if (section.sensitivity !== "ordinary") {
      throw new ContextError(
        "local_confirmation_required",
        "sensitive Section Skill changes remain local-only",
        403,
      );
    }

    const current = await this.db
      .prepare(
        `SELECT section_id, name, description, instructions, version, updated_at
         FROM sense_section_skills WHERE owner_id = ? AND section_id = ?`,
      )
      .bind(this.ownerId, parsed.section_id)
      .first<SkillRow>();
    const currentVersion = current?.version ?? "absent";
    if (currentVersion !== parsed.expected_version) {
      throw new ContextError(
        "section_skill_conflict",
        "the Section Skill changed after it was read",
        409,
      );
    }
    const content = serializeSkill(
      parsed.new_skill.name,
      parsed.new_skill.description,
      parsed.new_skill.instructions,
    );
    const version = `sense-section-skill-v1:${await sha256Hex(content)}`;
    if (current?.version === version) {
      return {
        changed: false,
        section_id: parsed.section_id,
        skill: skillProjection(current, true),
      };
    }
    const updatedAt = nowIso();
    if (current) {
      const result = await this.db
        .prepare(
          `UPDATE sense_section_skills
           SET name = ?, description = ?, instructions = ?, version = ?, updated_at = ?
           WHERE owner_id = ? AND section_id = ? AND version = ?`,
        )
        .bind(
          parsed.new_skill.name.trim(),
          parsed.new_skill.description.trim(),
          parsed.new_skill.instructions.replace(/\r\n?/g, "\n").trim(),
          version,
          updatedAt,
          this.ownerId,
          parsed.section_id,
          current.version,
        )
        .run();
      if ((result.meta.changes ?? 0) !== 1) {
        throw new ContextError(
          "section_skill_conflict",
          "the Section Skill changed after it was read",
          409,
        );
      }
    } else {
      await this.db
        .prepare(
          `INSERT INTO sense_section_skills(
             owner_id, section_id, name, description, instructions, version, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
        )
        .bind(
          this.ownerId,
          parsed.section_id,
          parsed.new_skill.name.trim(),
          parsed.new_skill.description.trim(),
          parsed.new_skill.instructions.replace(/\r\n?/g, "\n").trim(),
          version,
          updatedAt,
        )
        .run();
    }
    const row: SkillRow = {
      section_id: parsed.section_id,
      name: parsed.new_skill.name.trim(),
      description: parsed.new_skill.description.trim(),
      instructions: parsed.new_skill.instructions
        .replace(/\r\n?/g, "\n")
        .trim(),
      version,
      updated_at: updatedAt,
    };
    return {
      changed: true,
      section_id: parsed.section_id,
      skill: skillProjection(row, true),
    };
  }

  async importProfile(profileValue: unknown): Promise<Record<string, unknown>> {
    const profile = senseProfileSchema.parse(profileValue);
    const profileJson = canonicalJson(profile);
    const digest = await sha256Hex(profileJson);
    const updatedAt = nowIso();
    await this.db
      .prepare(
        `INSERT INTO sense_profiles(owner_id, profile_json, profile_sha256, updated_at)
         VALUES (?, ?, ?, ?)
         ON CONFLICT(owner_id) DO UPDATE SET
           profile_json = excluded.profile_json,
           profile_sha256 = excluded.profile_sha256,
           updated_at = excluded.updated_at`,
      )
      .bind(this.ownerId, profileJson, digest, updatedAt)
      .run();
    return {
      schema_version: 2,
      profile_sha256: digest,
      section_count: profile.sections.length,
      updated_at: updatedAt,
    };
  }

  async importSkills(value: unknown): Promise<Record<string, unknown>> {
    if (!Array.isArray(value)) {
      throw new ContextError(
        "invalid_import",
        "Sense Skill import must be an array",
      );
    }
    const profile = await this.profile();
    const ordinaryIds = new Set(
      profile.profile.sections
        .filter((section) => section.sensitivity === "ordinary")
        .map((section) => section.id),
    );
    const rows: SkillRow[] = [];
    for (const item of value) {
      if (item === null || Array.isArray(item) || typeof item !== "object") {
        throw new ContextError(
          "invalid_import",
          "Sense Skill import is invalid",
        );
      }
      const candidate = item as Record<string, unknown>;
      const parsed = senseSkillReviseSchema.parse({
        section_id: candidate.section_id,
        expected_version:
          typeof candidate.expected_version === "string"
            ? candidate.expected_version
            : "absent",
        new_skill: {
          name: candidate.name,
          description: candidate.description,
          instructions: candidate.instructions,
        },
      });
      if (!ordinaryIds.has(parsed.section_id)) {
        throw new ContextError(
          "invalid_import",
          "remote Sense import accepts Skills only for ordinary sections",
        );
      }
      const content = serializeSkill(
        parsed.new_skill.name,
        parsed.new_skill.description,
        parsed.new_skill.instructions,
      );
      const version = `sense-section-skill-v1:${await sha256Hex(content)}`;
      rows.push({
        section_id: parsed.section_id,
        name: parsed.new_skill.name.trim(),
        description: parsed.new_skill.description.trim(),
        instructions: parsed.new_skill.instructions
          .replace(/\r\n?/g, "\n")
          .trim(),
        version,
        updated_at:
          typeof candidate.updated_at === "string" &&
          Number.isFinite(Date.parse(candidate.updated_at))
            ? candidate.updated_at
            : nowIso(),
      });
    }
    const statements: D1PreparedStatement[] = [
      this.db
        .prepare("DELETE FROM sense_section_skills WHERE owner_id = ?")
        .bind(this.ownerId),
    ];
    for (const row of rows) {
      statements.push(
        this.db
          .prepare(
            `INSERT INTO sense_section_skills(
               owner_id, section_id, name, description, instructions, version, updated_at
             ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
          )
          .bind(
            this.ownerId,
            row.section_id,
            row.name,
            row.description,
            row.instructions,
            row.version,
            row.updated_at,
          ),
      );
    }
    await this.db.batch(statements);
    return { imported_skill_count: rows.length };
  }
}
