import type { McpServer } from "@modelcontextprotocol/server";
import { mcpTextError } from "@personal-agent/remote-runtime";
import { z } from "zod";

import registry from "./skill-registry.json";

/**
 * Skill lookup for the one toolkit connection. The same `skills/` source feeds
 * the packaged Skills each client installs, so a client without those files can
 * still read how the toolkit is meant to be used. Reading a Skill does not
 * install it, grant execution, or replace the direct product tools.
 */

export const TOOLKIT_SKILL_TOOLS = [
  "toolkit_skills_list",
  "toolkit_skill_read",
] as const;

type SkillFile = {
  uri: string;
  path: string;
  mime_type: string;
  bytes: number;
  text: string;
};
type Skill = {
  name: string;
  product: string | null;
  description: string;
  uri: string;
  files: SkillFile[];
};

const skills = registry.skills as Skill[];
const byUri = new Map<string, { skill: Skill; file: SkillFile }>();
for (const skill of skills) {
  for (const file of skill.files) byUri.set(file.uri, { skill, file });
}
const products = [
  ...new Set(
    skills.map((skill) => skill.product).filter((x): x is string => !!x),
  ),
];
const MAX_TEXT = 200_000;

export function registerToolkitSkillTools(server: McpServer): void {
  server.registerTool(
    "toolkit_skills_list",
    {
      title: "List toolkit Skills",
      description:
        "List the toolkit's Skills with their description, owning product and uri. " +
        "Read one with toolkit_skill_read when the working method matters; skip it " +
        "for a simple call you already know.",
      inputSchema: z
        .object({
          product: z.enum(products as [string, ...string[]]).optional(),
          query: z.string().min(1).max(200).optional(),
        })
        .strict(),
      outputSchema: z.looseObject({}),
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (input) => {
      const { product, query } = input as { product?: string; query?: string };
      const needle = query?.toLowerCase();
      const listed = skills
        .filter((skill) => !product || skill.product === product)
        .filter(
          (skill) =>
            !needle ||
            skill.name.toLowerCase().includes(needle) ||
            skill.description.toLowerCase().includes(needle),
        )
        .map((skill) => ({
          name: skill.name,
          product: skill.product,
          description: skill.description,
          uri: skill.uri,
          files: skill.files.length,
        }));
      return {
        content: [],
        structuredContent: {
          ok: true as const,
          result: { version: registry.version, skills: listed },
        },
      };
    },
  );

  server.registerTool(
    "toolkit_skill_read",
    {
      title: "Read a toolkit Skill",
      description:
        "Return the text of one Skill file by its uri, for example " +
        "skill://pat/use-host/SKILL.md. Reading SKILL.md also lists that Skill's " +
        "other files. Reading a Skill does not install it or grant any execution.",
      inputSchema: z.object({ uri: z.string().min(1).max(500) }).strict(),
      outputSchema: z.looseObject({}),
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (input) => {
      const { uri } = input as { uri: string };
      const found = byUri.get(uri);
      if (!found) {
        return mcpTextError({
          ok: false as const,
          error: {
            code: "skill_not_found",
            message: `${uri} is not a registered Skill file`,
          },
        });
      }
      const { skill, file } = found;
      const text =
        file.text.length > MAX_TEXT ? file.text.slice(0, MAX_TEXT) : file.text;
      return {
        content: [{ type: "text" as const, text }],
        structuredContent: {
          ok: true as const,
          result: {
            uri: file.uri,
            skill: skill.name,
            product: skill.product,
            mime_type: file.mime_type,
            version: registry.version,
            truncated: text.length < file.text.length,
            text,
            ...(file.path === "SKILL.md"
              ? {
                  files: skill.files.map((other) => ({
                    uri: other.uri,
                    path: other.path,
                    mime_type: other.mime_type,
                    bytes: other.bytes,
                  })),
                }
              : {}),
          },
        },
      };
    },
  );
}
