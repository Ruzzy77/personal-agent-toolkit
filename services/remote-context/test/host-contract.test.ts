import { describe, expect, it } from "vitest";
import { HOST_TOOLS, hostForwardArguments, hostRequiredScope } from "../src/host";

const execution = HOST_TOOLS.find((tool) => tool.name === "host_exec")!;

describe("Host execution profiles and egress contract", () => {
  it("keeps the existing no-profile request and public egress default", () => {
    const parsed = execution.schema.parse({ root: "workspace", argv: ["python3", "-V"] });
    expect(parsed).not.toHaveProperty("profile");
    expect(parsed).not.toHaveProperty("https_hosts");
    expect(execution.description).toContain("Public IPv4 egress is available by default");
    expect(execution.annotations.openWorldHint).toBe(true);
  });

  it("passes an explicit execution profile without a network selector", () => {
    expect(execution.schema.parse({
      root: "workspace", argv: ["npm", "ci"], profile: "web",
    })).toMatchObject({ profile: "web" });
  });

  it("preserves JSON-shaped stdin across the internal MCP hop", () => {
    const stdin = '{\n  "probe": "toolkit-validation"\n}\n';
    const forwarded = hostForwardArguments("host_exec", {
      root: "workspace", argv: ["cat"], stdin,
    });
    expect(forwarded).not.toHaveProperty("stdin");
    expect(atob(String(forwarded.stdin_base64))).toBe(stdin);
  });

  it("explicitly rejects the retired allowlist input and ad hoc network flags", () => {
    expect(execution.schema.safeParse({
      root: "workspace", argv: ["true"], https_hosts: ["registry.npmjs.org"],
    }).success).toBe(false);
    expect(execution.schema.safeParse({ root: "workspace", internet: true }).success).toBe(false);
    expect(execution.schema.safeParse({ root: "workspace", network: "host" }).success).toBe(false);
    expect(execution.schema.safeParse({ root: "workspace", profile: "" }).success).toBe(false);
  });
});

describe("Host file operations", () => {
  it("keeps read-only operations read scoped and mutations write scoped", () => {
    expect(hostRequiredScope("host_files", { operation: "list" })).toBe("host.read");
    expect(hostRequiredScope("host_files", { operation: "trash" })).toBe("host.write");
    expect(hostRequiredScope("host_transfer", { direction: "download" })).toBe("host.read");
    expect(hostRequiredScope("host_transfer", { direction: "upload" })).toBe("host.write");
  });
  it("never accepts a file button as arbitrary command execution", () => {
    const files = HOST_TOOLS.find(tool => tool.name === "host_files")!;
    expect(files.schema.safeParse({ root: "workspace", operation: "list", shell: "rm" }).success).toBe(false);
  });
  it("rejects uploads above the file limit", () => {
    const transfer = HOST_TOOLS.find(tool => tool.name === "host_transfer")!;
    expect(transfer.schema.safeParse({
      root: "workspace", path: "file.bin", direction: "upload", size: 1073741825,
    }).success).toBe(false);
  });
});
