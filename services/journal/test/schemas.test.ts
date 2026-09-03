import { describe, expect, it } from "vitest";

import { promotionRequestSchema } from "../src/schemas";

function promotion(sourcePath: string) {
  return {
    weekId: "2030-01-07",
    itemId: null,
    targetSpace: "toolkit-project",
    sourcePath,
    contentHash: "sha256:example",
    status: "applied" as const,
    details: null,
    idempotencyKey: "test:promotion-path",
    occurredAt: null,
  };
}

describe("Corpus reflection receipt paths", () => {
  it("accepts project-relative paths and non-path labels", () => {
    expect(promotionRequestSchema.safeParse(promotion("docs/release.md")).success).toBe(
      true,
    );
    expect(promotionRequestSchema.safeParse(promotion("skipped")).success).toBe(true);
  });

  it.each([
    "/Users/owner/project/docs/release.md",
    "~/project/docs/release.md",
    "../outside.md",
    "docs/../../outside.md",
    "C:\\Users\\owner\\release.md",
    "file:///Users/owner/release.md",
    "smb://fileserver/Users/owner/release.md",
    "https://example.com/private/release.md",
  ])("rejects local or escaping path %s", (sourcePath) => {
    expect(promotionRequestSchema.safeParse(promotion(sourcePath)).success).toBe(false);
  });
});
