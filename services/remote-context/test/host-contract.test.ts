import { describe, expect, it } from "vitest";
import { HOST_TOOLS, hostForwardArguments, hostRequiredScope } from "../src/host";

const execution = HOST_TOOLS.find((tool) => tool.name === "host_exec")!;

describe("Spark direct execution contract", () => {
  it("runs directly as the Spark owner without a container profile or network claim", () => {
    const parsed = execution.schema.parse({ root: "workspace", argv: ["python3", "-V"] });
    expect(parsed).not.toHaveProperty("profile");
    expect(parsed).not.toHaveProperty("https_hosts");
    expect(execution.description).toContain("directly as the configured Spark owner");
    expect(execution.description).toContain("actual host path");
    expect(execution.description).not.toContain("sandbox");
    expect(execution.annotations.openWorldHint).toBe(true);
  });

  it("passes stdin state and leaves retired selectors for the Host to reject clearly", () => {
    expect(execution.schema.parse({
      root: "workspace", argv: ["cat"], stdin: "first", keep_stdin_open: true,
    })).toMatchObject({ stdin: "first", keep_stdin_open: true });
    expect(execution.schema.parse({
      root: "workspace", argv: ["true"], profile: "web",
    })).toMatchObject({ profile: "web" });
    expect(execution.schema.parse({
      root: "workspace", argv: ["true"], https_hosts: ["registry.npmjs.org"],
    })).toMatchObject({ https_hosts: ["registry.npmjs.org"] });
  });

  it("preserves JSON-shaped stdin across the internal MCP hop", () => {
    const stdin = '{\n  "probe": "toolkit-validation"\n}\n';
    const forwarded = hostForwardArguments("host_exec", {
      root: "workspace", argv: ["cat"], stdin,
    });
    expect(forwarded).not.toHaveProperty("stdin");
    expect(atob(String(forwarded.stdin_base64))).toBe(stdin);
  });

  it("rejects unsupported ad hoc execution flags", () => {
    expect(execution.schema.safeParse({ root: "workspace", internet: true }).success).toBe(false);
    expect(execution.schema.safeParse({ root: "workspace", network: "host" }).success).toBe(false);
    expect(execution.schema.safeParse({ root: "workspace", profile: "" }).success).toBe(false);
  });

  it("base64-wraps later job input over the internal MCP hop", () => {
    const forwarded = hostForwardArguments("host_job_input", {
      job_id: "job-1", stdin: "next", eof: true,
    });
    expect(forwarded).not.toHaveProperty("stdin");
    expect(atob(String(forwarded.stdin_base64))).toBe("next");
    expect(forwarded).toMatchObject({ job_id: "job-1", eof: true });
  });

  it("declares write-scoped streaming job input", () => {
    const input = HOST_TOOLS.find(tool => tool.name === "host_job_input")!;
    expect(input.schema.parse({ job_id: "job-1", stdin: "next", eof: true }))
      .toMatchObject({ job_id: "job-1", stdin: "next", eof: true });
    expect(hostRequiredScope("host_job_input", {})).toBe("host.write");
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
