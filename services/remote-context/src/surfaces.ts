import type { ResourceKind } from "./types";

export type McpSurface = {
  name: string;
  version: string;
  tools: readonly string[];
};

export const MCP_SURFACES = {
  sense: {
    name: "Sense",
    version: "0.3.4-remote.1",
    tools: [
      "sense_read",
      "sense_overview",
      "sense_revise",
      "sense_skill_revise",
    ],
  },
  corpus: {
    name: "Corpus",
    version: "0.21.4-remote.3",
    tools: [
      "corpus_space_list",
      "corpus_space_get",
      "corpus_context_items_revise",
      "corpus_context_skill_revise",
      "corpus_space_search",
      "corpus_source_refresh",
      "corpus_job_status",
      "corpus_file_list",
      "corpus_file_read",
      "corpus_file_write",
      "corpus_file_delete",
      "corpus_file_select_current",
      "corpus_file_restore",
    ],
  },
  hypes: {
    name: "Hypes",
    version: "0.9.4-remote.1",
    tools: ["hypes_read", "hypes_rewrite"],
  },
} as const satisfies Record<ResourceKind, McpSurface>;
