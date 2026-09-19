import { describe, expect, it } from "vitest";
import { HOST_TOOLS } from "../src/host";

const execution = HOST_TOOLS.find((tool) => tool.name === "host_exec")!;

describe("Host execution profiles and egress contract", () => {
  it("preserves the existing no-profile request", () => {
    const parsed = execution.schema.parse({ root: "workspace", argv: ["python3", "-V"] });
    expect(parsed).not.toHaveProperty("profile");
    expect(parsed).not.toHaveProperty("https_hosts");
  });

  it("passes explicit profile and bounded hostname selection to Host", () => {
    expect(execution.schema.parse({
      root: "workspace", argv: ["npm", "ci"], profile: "web",
      https_hosts: ["registry.npmjs.org"],
    })).toMatchObject({ profile: "web", https_hosts: ["registry.npmjs.org"] });
    expect(execution.annotations.openWorldHint).toBe(true);
  });

  it("rejects malformed and excessive input at the adapter", () => {
    expect(execution.schema.safeParse({ root: "workspace", profile: "" }).success).toBe(false);
    expect(execution.schema.safeParse({
      root: "workspace", https_hosts: Array(33).fill("registry.npmjs.org"),
    }).success).toBe(false);
    expect(execution.schema.safeParse({ root: "workspace", internet: true }).success).toBe(false);
  });
});
