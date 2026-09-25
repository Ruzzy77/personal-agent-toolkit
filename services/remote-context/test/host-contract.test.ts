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

describe("Flow tool contract", () => {
  it("keeps reads and writes separated without accepting an arbitrary service address", () => {
    expect(hostRequiredScope("flow_work_read", {})).toBe("host.read");
    expect(hostRequiredScope("flow_asset_import", {})).toBe("host.write");
    expect(hostRequiredScope("flow_snapshot_create", {})).toBe("host.write");
    expect(hostRequiredScope("flow_snapshot_list", {})).toBe("host.read");
    expect(hostRequiredScope("flow_snapshot_read", {})).toBe("host.read");
    const savedList=HOST_TOOLS.find(tool=>tool.name==="flow_snapshot_list")!;
    expect(savedList.schema.safeParse({workspace_id:"workspace",query:"검사",offset:20,limit:50}).success).toBe(true);
    expect(savedList.schema.safeParse({workspace_id:"workspace",limit:101}).success).toBe(false);
    const savedRead=HOST_TOOLS.find(tool=>tool.name==="flow_snapshot_read")!;
    expect(savedRead.schema.safeParse({workspace_id:"workspace",source_id:"snapshot:one"}).success).toBe(true);
    expect(savedRead.schema.safeParse({workspace_id:"workspace",source_id:"snapshot:one",service_url:"https://outside.example"}).success).toBe(false);
    expect(hostRequiredScope("flow_change_submit", {})).toBe("host.write");
    expect(hostRequiredScope("flow_change_action", {})).toBe("host.write");
    const action = HOST_TOOLS.find(tool => tool.name === "flow_change_action")!;
    expect(action.schema.safeParse({workspace_id:"workspace",change_id:"change",action:"apply"}).success).toBe(true);
    expect(action.schema.safeParse({workspace_id:"workspace",change_id:"change",action:"execute"}).success).toBe(false);
    const read = HOST_TOOLS.find(tool => tool.name === "flow_work_read")!;
    expect(read.schema.safeParse({workspace_id:"workspace",work_id:"work",service_url:"http://evil"}).success).toBe(false);
    const create = HOST_TOOLS.find(tool => tool.name === "flow_work_create")!;
    const fromSource={workspace_id:"workspace",name:"이어갈 작업",source_id:"snapshot:version-1",idempotency_key:"create-attempt-1"};
    expect(create.schema.safeParse(fromSource).success).toBe(true);
    expect(create.schema.safeParse({...fromSource,artifact:{kind:"content"}}).success).toBe(false);
    expect(create.schema.safeParse({...fromSource,source_id:undefined}).success).toBe(true);
    expect(create.schema.safeParse({...fromSource,service_url:"https://outside.example"}).success).toBe(false);
    const resource={kind:"journal-item",id:"123e4567-e89b-42d3-a456-426614174000"};
    expect(create.schema.safeParse({workspace_id:"workspace",name:"기록 검토",artifact:{kind:"content"},linked_resources:[resource],idempotency_key:"create-attempt-2"}).success).toBe(true);
    expect(create.schema.safeParse({...fromSource,linked_resources:[{...resource,id:"bad"}]}).success).toBe(false);
    const update = HOST_TOOLS.find(tool => tool.name === "flow_work_update")!;
    const linked = {workspace_id:"workspace",work_id:"work",expected_revision:1,idempotency_key:"retry-key-2",linked_resources:[{kind:"journal-item",id:"123e4567-e89b-42d3-a456-426614174000"},{kind:"library-issue",id:"daily:2026-09-24"}]};
    expect(update.schema.safeParse(linked).success).toBe(true);
    expect(update.schema.safeParse({...linked,surface_layout:{order:["artifact-1"],spans:{"artifact-1":8}}}).success).toBe(true);
    expect(update.schema.safeParse({...linked,surface_layout:{order:["artifact-1"],spans:{"artifact-1":5}}}).success).toBe(false);
    expect(update.schema.safeParse({...linked,surface_layout:{order:["artifact-1"],spans:{},color:"red"}}).success).toBe(false);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"context",locator:{product:"corpus",spaceId:"research",documentId:"notes"}},{kind:"context",locator:{product:"sense",sectionId:"conversation-and-writing"}}]}).success).toBe(true);
    const readRef="read1."+Buffer.from(JSON.stringify({version:1,spaceId:"research",connectionId:"main",corpusId:"source-corpus",unitId:"unit-1"})).toString("base64url");
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"context",locator:{product:"source",spaceId:"research",readRef}}]}).success).toBe(true);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"context",locator:{product:"source",spaceId:"research",readRef:"https://outside.example"}}]}).success).toBe(false);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"design-recipe",id:"document-minimal"}]}).success).toBe(true);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"design-recipe",id:"../other"}]}).success).toBe(false);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"host-file",root:"research-note/main",path:"notes/한글.md"}]}).success).toBe(true);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"host-file",root:"workspace",path:"../secret"}]}).success).toBe(false);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"host-file",root:"workspace",path:"notes/file.md",body:"copied"}]}).success).toBe(false);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"context",locator:{product:"corpus",spaceId:"research",documentId:"../secret"}}]}).success).toBe(false);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"context",locator:{product:"sense",sectionId:"conversation-and-writing",skill:false}}]}).success).toBe(false);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"context",locator:{product:"corpus",spaceId:"research",documentId:"notes"},title:"copied"}]}).success).toBe(false);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"unknown",id:"bad"}]}).success).toBe(false);
    expect(update.schema.safeParse({...linked,linked_resources:[{kind:"journal-item",id:"ok",title:"copied content"}]}).success).toBe(false);
    const snapshot = HOST_TOOLS.find(tool => tool.name === "flow_snapshot_create")!;
    const save={workspace_id:"workspace",work_id:"work",artifact_id:"artifact",expected_revision:2,idempotency_key:"save-attempt-1"};
    expect(snapshot.schema.safeParse(save).success).toBe(true);
    expect(snapshot.schema.safeParse({...save,expected_revision:-1}).success).toBe(false);
    expect(snapshot.schema.safeParse({...save,service_url:"https://outside.example"}).success).toBe(false);
    const image = HOST_TOOLS.find(tool => tool.name === "flow_asset_import")!;
    expect(image.schema.safeParse({workspace_id:"workspace",root:"workspace",path:"photos/part.png",expected_version:"sha256:"+"a".repeat(64)}).success).toBe(true);
    expect(image.schema.safeParse({workspace_id:"workspace",root:"workspace",path:"photos/part.png",expected_version:"sha256:"+"a".repeat(64),service_url:"https://outside.example"}).success).toBe(false);
    const submit = HOST_TOOLS.find(tool => tool.name === "flow_change_submit")!;
    expect(submit.schema.safeParse({workspace_id:"workspace",work_id:"work",mode:"proposal",idempotency_key:"retry-key-1",artifact_id:"artifact",base_revision:0,artifact:{title:"안"}}).success).toBe(true);
    expect(submit.schema.safeParse({workspace_id:"workspace",work_id:"work",mode:"initialize",idempotency_key:"retry-key-2",artifact_id:"blank",base_revision:0,artifact:{kind:"content"}}).success).toBe(true);
    expect(submit.schema.safeParse({workspace_id:"workspace",work_id:"work",mode:"proposal",idempotency_key:"retry-key-1",host_path:"/tmp/x"}).success).toBe(false);
  });
});

describe("Flow HTML and library contracts",()=>{
 it("uses revision guards and existing host scopes",()=>{
  for(const operation of ["flow_library_list","flow_library_read"])expect(hostRequiredScope(operation,{})).toBe("host.read");
  expect(hostRequiredScope("flow_library_upsert",{})).toBe("host.write");
  const curate=HOST_TOOLS.find(tool=>tool.name==="flow_library_upsert")!;
  const entry={id:"material-one",title:"보고서 구성",body:"요약과 분석",scope:{kind:"work",workId:"work"}};
  expect(curate.schema.safeParse({workspace_id:"workspace",entry,expected_revision:0,idempotency_key:"material-one-key"}).success).toBe(true);
  expect(curate.schema.safeParse({workspace_id:"workspace",entry,expected_revision:-1,idempotency_key:"material-one-key"}).success).toBe(false);
  const submit=HOST_TOOLS.find(tool=>tool.name==="flow_change_submit")!;
  expect(submit.schema.safeParse({workspace_id:"workspace",work_id:"work",artifact_id:"artifact",base_revision:1,mode:"replace",artifact:{kind:"html",title:"보고서",html:"<h1>보고서</h1>",assets:[]},idempotency_key:"html-replace-key"}).success).toBe(true);
 });
});


describe("UIKit publication references",()=>{
 it("preserves an exact asset and revision without accepting copied files or arbitrary URLs",()=>{
  const update=HOST_TOOLS.find(tool=>tool.name==="flow_work_update")!;
  const base={workspace_id:"workspace",work_id:"work",expected_revision:1,idempotency_key:"uikit-reference-1"};
  const reference={kind:"uikit-asset",id:"continuous-report",revision:"a".repeat(64)};
  expect(update.schema.safeParse({...base,linked_resources:[reference]}).success).toBe(true);
  for(const invalid of [{...reference,revision:"latest"},{...reference,id:"../secret"},{...reference,href:"https://outside.example"},{...reference,body:"copied"}])
   expect(update.schema.safeParse({...base,linked_resources:[invalid]}).success).toBe(false);
 });
});
