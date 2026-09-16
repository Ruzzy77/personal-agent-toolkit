import { describe, expect, it } from "vitest";

import { conditionalRead } from "../src/conditional-read";

describe("conditional reads", () => {
  it("keeps the legacy response unchanged when the input is omitted", async () => {
    const result = { body: "complete", has_more: false };
    expect(
      await conditionalRead("owner-a", "sense_read", { view: "full" }, undefined, async () => result),
    ).toEqual(result);
  });

  it("returns a full first response and a compact unchanged response", async () => {
    const result = { body: "complete", has_more: false };
    const first = await conditionalRead(
      "owner-a",
      "sense_read",
      { view: "full" },
      null,
      async () => result,
    );
    expect(first).toMatchObject({ ...result, not_modified: false });
    expect(first.read_etag).toMatch(/^read-v1:[0-9a-f]{64}$/);

    await expect(
      conditionalRead(
        "owner-a",
        "sense_read",
        { view: "full" },
        String(first.read_etag),
        async () => result,
      ),
    ).resolves.toEqual({ read_etag: first.read_etag, not_modified: true });
  });

  it("changes tags across results, selections, operations, and owners", async () => {
    const tag = async (
      owner: string,
      operation: string,
      selection: unknown,
      result: Record<string, unknown>,
    ) =>
      String(
        (
          await conditionalRead(owner, operation, selection, null, async () => result)
        ).read_etag,
      );
    const baseline = await tag("owner-a", "sense_read", { page: 1 }, { body: "a" });
    expect(await tag("owner-a", "sense_read", { page: 1 }, { body: "b" })).not.toBe(baseline);
    expect(await tag("owner-a", "sense_read", { page: 2 }, { body: "a" })).not.toBe(baseline);
    expect(await tag("owner-a", "corpus_space_get", { page: 1 }, { body: "a" })).not.toBe(baseline);
    expect(await tag("owner-b", "sense_read", { page: 1 }, { body: "a" })).not.toBe(baseline);
  });

  it("does not suppress read failures", async () => {
    await expect(
      conditionalRead(
        "owner-a",
        "sense_read",
        {},
        `read-v1:${"0".repeat(64)}`,
        async () => {
          throw new Error("authorization or version failure");
        },
      ),
    ).rejects.toThrow("authorization or version failure");
  });
});
