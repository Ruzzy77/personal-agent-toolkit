import { SELF, env, runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import corpusPlugin from "../../../plugins/corpus/.claude-plugin/plugin.json";
import hypesPlugin from "../../../plugins/hypes/.claude-plugin/plugin.json";
import sensePlugin from "../../../plugins/sense/.claude-plugin/plugin.json";
import { contentSha256, sha256Hex } from "../src/canonical";
import {
  compactStoredSourceAnchor,
  compactStoredStructurePath,
  decodeStoredStructurePath,
} from "../src/corpus-shard";
import { CorpusService } from "../src/corpus";
import { HypesService } from "../src/hypes";
import { handleMcp } from "../src/mcp";
import {
  corpusFileReadSchema,
  corpusFileWriteSchema,
} from "../src/schemas";
import { SenseService } from "../src/sense";
import { MCP_SURFACES } from "../src/surfaces";
import type { Env, Principal } from "../src/types";

const runtime = env as unknown as Env;
const syncHeaders = {
  Authorization: "Bearer test-device-token",
  "X-Personal-Agent-Device": "test-mac",
  "Content-Type": "application/json",
};

it("keeps remote Work file budgets aligned with the local Corpus contract", () => {
  expect(
    corpusFileReadSchema.parse({ space_id: "notes" }),
  ).toMatchObject({
    max_bytes: 2 * 1024 * 1024,
    max_chars: 30_000,
    start_char: 0,
  });
  expect(
    corpusFileReadSchema.safeParse({
      space_id: "notes",
      max_bytes: 2 * 1024 * 1024 + 1,
    }).success,
  ).toBe(false);
  expect(
    corpusFileReadSchema.safeParse({
      space_id: "notes",
      max_chars: 200_001,
    }).success,
  ).toBe(false);
  expect(
    corpusFileWriteSchema.safeParse({
      space_id: "notes",
      relative_path: "note.txt",
      content: "x".repeat(6 * 1024 * 1024 + 1),
      content_encoding: "utf8",
      expected_version: "absent",
    }).success,
  ).toBe(false);
});

it("losslessly compacts legacy Source-anchor invariants", () => {
  const structure = { paragraph: 2, structural_only: true };
  const compacted = compactStoredSourceAnchor(
    JSON.stringify({
      canonical_locator: "folder/note.txt",
      content_hash: "a".repeat(64),
      document_id: "doc_test",
      revision_id: "rev_test",
      projection_id: "projection_test",
      structural_locator: structure,
      source_span: { paragraph: 2 },
      absolute_path: "/private/source/note.txt",
    }),
    JSON.stringify(structure),
    "doc_test",
    "rev_test",
    "projection_test",
    "folder/note.txt",
    "a".repeat(64),
  );
  expect(compacted.compacted).toBe(true);
  expect(compacted.value).toBe(
    'compact-v1:{"source_span":{"paragraph":2}}',
  );
});

it("losslessly compacts repeated table-cell structure", () => {
  const structure = {
    cell: "r16",
    col: 0,
    col_span: 24,
    container_kind: "table_cell",
    container_path: [
      {
        cell: "r16",
        col: 0,
        col_span: 24,
        is_header: false,
        kind: "table_cell",
        list_record: 16,
        object: "r14",
        owner_paragraph_record: 1,
        row: 0,
        row_span: 2,
        table: "r14",
      },
    ],
    head_type: "none",
    is_header: false,
    object: "r14",
    owner_paragraph_record: 1,
    paragraph: 2,
    row: 0,
    row_span: 2,
    section: 1,
    section_stream: "Section0",
    table: "r14",
  };
  const original = JSON.stringify(structure);
  const compacted = compactStoredStructurePath(original);
  expect(compacted.compacted).toBe(true);
  expect(compacted.value.length).toBeLessThan(original.length);
  expect(decodeStoredStructurePath(compacted.value)).toEqual(structure);
  expect(compactStoredStructurePath(compacted.value)).toEqual(compacted);
});

async function body(response: Response): Promise<Record<string, unknown>> {
  const text = await response.text();
  expect(response.headers.get("content-type")).toContain("application/json");
  return JSON.parse(text) as Record<string, unknown>;
}

function syncPost(path: string, value: unknown): Promise<Response> {
  return SELF.fetch(`https://context.test${path}`, {
    method: "POST",
    headers: syncHeaders,
    body: JSON.stringify(value),
  });
}

function nextSocketMessage(
  socket: WebSocket,
): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error("WebSocket message timed out")),
      3_000,
    );
    socket.addEventListener(
      "message",
      (event) => {
        clearTimeout(timeout);
        resolve(JSON.parse(String(event.data)) as Record<string, unknown>);
      },
      { once: true },
    );
  });
}

function nextSocketMessages(
  socket: WebSocket,
  count: number,
): Promise<Array<Record<string, unknown>>> {
  return new Promise((resolve, reject) => {
    const values: Array<Record<string, unknown>> = [];
    const timeout = setTimeout(() => {
      socket.removeEventListener("message", onMessage);
      reject(new Error("WebSocket messages timed out"));
    }, 3_000);
    const onMessage = (event: MessageEvent) => {
      values.push(JSON.parse(String(event.data)) as Record<string, unknown>);
      if (values.length !== count) return;
      clearTimeout(timeout);
      socket.removeEventListener("message", onMessage);
      resolve(values);
    };
    socket.addEventListener("message", onMessage);
  });
}

const ownerPrincipal: Principal = {
  ownerId: "owner_test",
  scopes: new Set([
    "corpus.read",
    "corpus.write",
    "corpus.sync",
    "sense.read",
    "sense.write",
    "hypes.read",
    "hypes.write",
  ]),
  clientId: "test",
  auth: "oauth",
};

async function mcpPayload(
  response: Response,
): Promise<Record<string, unknown>> {
  const text = await response.text();
  if (response.headers.get("content-type")?.includes("application/json")) {
    return JSON.parse(text) as Record<string, unknown>;
  }
  const line = text.split("\n").find((value) => value.startsWith("data: "));
  if (!line) throw new Error(`MCP response contains no data: ${text}`);
  return JSON.parse(line.slice(6)) as Record<string, unknown>;
}

describe("remote personal context service", () => {
  it("publishes separate protected-resource metadata for all MCP resources", async () => {
    const health = await SELF.fetch("https://context.test/health");
    expect(health.status).toBe(200);
    expect(await body(health)).toMatchObject({
      ok: true,
      service: "personal-agent-context",
      resources: ["sense", "corpus", "hypes"],
    });

    for (const kind of ["sense", "corpus", "hypes"] as const) {
      const response = await SELF.fetch(
        `https://context.test/.well-known/oauth-protected-resource/${kind}/mcp`,
      );
      expect(response.status).toBe(200);
      expect(await body(response)).toMatchObject({
        resource: `https://context.test/${kind}/mcp`,
        authorization_servers: ["https://auth.test"],
      });
    }
  });

  it("advertises stable, object-rooted MCP schemas for all three products", async () => {
    for (const kind of ["sense", "hypes", "corpus"] as const) {
      const response = await handleMcp(
        new Request(`https://context.test/${kind}/mcp`, {
          method: "POST",
          headers: {
            Accept: "application/json, text/event-stream",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
        }),
        runtime,
        ownerPrincipal,
        kind,
      );
      expect(response.status, await response.clone().text()).toBe(200);
      const payload = await mcpPayload(response);
      const result = payload.result as {
        tools: Array<{ name: string; inputSchema: object }>;
      };
      expect(result.tools.map((tool) => tool.name)).toEqual(
        MCP_SURFACES[kind].tools,
      );
      for (const tool of result.tools) {
        expect(tool.inputSchema).toMatchObject({ type: "object" });
      }
    }
  });

  it("keeps remote MCP server versions aligned with the client plugins", async () => {
    const expectedVersions = {
      sense: `${sensePlugin.version}-remote.1`,
      corpus: `${corpusPlugin.version}-remote.3`,
      hypes: `${hypesPlugin.version}-remote.1`,
    } as const;
    for (const kind of ["sense", "corpus", "hypes"] as const) {
      expect(MCP_SURFACES[kind].version).toBe(expectedVersions[kind]);
      const response = await handleMcp(
        new Request(`https://context.test/${kind}/mcp`, {
          method: "POST",
          headers: {
            Accept: "application/json, text/event-stream",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            jsonrpc: "2.0",
            id: 1,
            method: "initialize",
            params: {
              protocolVersion: "2025-11-25",
              capabilities: {},
              clientInfo: { name: "version-contract-test", version: "1" },
            },
          }),
        }),
        runtime,
        ownerPrincipal,
        kind,
      );
      expect(response.status, await response.clone().text()).toBe(200);
      const payload = await mcpPayload(response);
      expect(payload).toMatchObject({
        result: { serverInfo: { version: expectedVersions[kind] } },
      });
    }
  });

  it("imports, reads, and conflict-checks the same Sense profile", async () => {
    const profile = {
      schema_version: 2 as const,
      sections: [
        {
          id: "questions-and-choices",
          purpose: "Keep decisions aligned with the owner's request.",
          text: "Ask only when an owner choice is necessary.",
          origins: ["user_set" as const],
          sensitivity: "ordinary" as const,
        },
      ],
    };
    const imported = await syncPost("/sync/v1/import/sense", {
      profile,
      skills: [
        {
          section_id: "questions-and-choices",
          name: "questions-and-choices",
          description: "Apply the owner's question policy.",
          instructions: "Continue autonomously while a safe path remains.",
        },
      ],
    });
    expect(imported.status, await imported.clone().text()).toBe(200);

    const service = new SenseService(runtime.STATE_DB, "owner_test");
    const read = await service.read("sections", ["questions-and-choices"]);
    expect(JSON.stringify(read)).toContain("Continue autonomously");
    const section = profile.sections[0]!;
    const updated = await service.revise({
      changes: [
        {
          section_id: section.id,
          previous_section_sha256: await contentSha256(section),
          new_section: {
            ...section,
            text: "Ask only when no safe path remains.",
          },
        },
      ],
    });
    expect(updated.changed).toBe(true);
    await expect(
      service.revise({
        changes: [
          {
            section_id: section.id,
            previous_section_sha256: await contentSha256(section),
            new_section: section,
          },
        ],
      }),
    ).rejects.toMatchObject({ code: "section_conflict", status: 409 });
  });

  it("keeps Hypes graph rewrites reference-closed and atomic", async () => {
    const nodeA = "node_11111111111111111111111111111111";
    const nodeB = "node_22222222222222222222222222222222";
    const predicate = "pred_33333333333333333333333333333333";
    const edge = "edge_44444444444444444444444444444444";
    const imported = await syncPost("/sync/v1/import/hypes", {
      nodes: [
        {
          node_id: nodeA,
          labels: ["preference"],
          name: "concise",
          aliases: [],
          attributes: {},
        },
        {
          node_id: nodeB,
          labels: ["output"],
          name: "answer",
          aliases: [],
          attributes: {},
        },
      ],
      predicates: [{ predicate_id: predicate, name: "prefers", aliases: [] }],
      edges: [
        {
          edge_id: edge,
          source_id: nodeA,
          predicate_id: predicate,
          target_id: nodeB,
          qualifiers: {},
        },
      ],
    });
    expect(imported.status, await imported.clone().text()).toBe(200);
    const service = new HypesService(runtime.STATE_DB, "owner_test");
    const focused = await service.read({
      focus: "concise",
      max_hops: 1,
      limit: 10,
    });
    expect(focused.nodes).toHaveLength(2);
    expect(focused.edges).toHaveLength(1);

    await expect(
      service.rewrite({
        operations: [
          { op: "delete", ref: nodeB },
          {
            op: "put_edge",
            ref: edge,
            value: {
              source_ref: nodeA,
              predicate_ref: predicate,
              target_ref: nodeB,
              qualifiers: {},
            },
          },
        ],
      }),
    ).rejects.toMatchObject({ code: "dangling_edge" });
    const unchanged = await service.read({
      seed_refs: [nodeB],
      max_hops: 0,
      limit: 10,
    });
    expect(unchanged.nodes).toHaveLength(1);

    const created = await service.rewrite({
      operations: [
        {
          op: "put_node",
          ref: "$style",
          value: { labels: [], name: "polite", aliases: [], attributes: {} },
        },
      ],
    });
    expect((created.ref_map as Record<string, string>).$style).toMatch(
      /^node_[0-9a-f]{32}$/,
    );
  });

  it("stages Corpus projections and preserves the last good revision on failure", async () => {
    const corpusId = "durable-test";
    const documentId = "doc_durable_test";
    const firstContent = "durable marker in the committed record";
    const firstHash = await sha256Hex(firstContent);
    const begin = {
      uploadId: "upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      corpusId,
      document: {
        documentId,
        relativePath: "folder/한글.txt",
        extension: ".txt",
        sourceState: "available",
      },
      revision: {
        revisionId: "rev_first",
        sha256: "1".repeat(64),
        sourceSize: firstContent.length,
        capturedAt: "2026-09-02T00:00:00.000Z",
      },
      projection: {
        projectionId: "projection_first",
        adapterId: "document-files.text",
        adapterVersion: "2",
        configHash: "2".repeat(64),
        resultManifestHash: "3".repeat(64),
        completenessState: "complete",
        coverage: { text_content: "complete" },
        capabilityManifest: { text: true },
        assuranceState: "declared",
        declaredUnitCount: 1,
      },
    };
    expect(
      (await syncPost(`/sync/v1/corpora/${corpusId}/projections:begin`, begin))
        .status,
    ).toBe(200);
    const unit = {
      unitId: "unit_first",
      ordinal: 1,
      unitType: "paragraph",
      structurePath: { paragraph: 1 },
      sourceAnchor: {
        canonical_locator: "folder/한글.txt",
        content_hash: begin.revision.sha256,
        document_id: documentId,
        extraction_schema_version: 5,
        projection_id: begin.projection.projectionId,
        relative_path: "folder/한글.txt",
        revision_id: begin.revision.revisionId,
        schema_version: 2,
        source_span: { paragraph: 1 },
        structure_path: { paragraph: 1 },
        structural_locator: { paragraph: 1 },
      },
      content: firstContent,
      contentSha256: firstHash,
      previousUnitId: null,
      nextUnitId: null,
      extractionIssues: [],
      derivationMethod: "native_text",
      geometry: {},
      confidence: 1,
      ocr: false,
      qualityFlags: [],
    };
    expect(
      (
        await syncPost(`/sync/v1/corpora/${corpusId}/projection-units:append`, {
          uploadId: begin.uploadId,
          units: [unit],
        })
      ).status,
    ).toBe(200);
    expect(
      (
        await syncPost(`/sync/v1/corpora/${corpusId}/projections:commit`, {
          uploadId: begin.uploadId,
          expectedUnitCount: 1,
          expectedManifestHash: begin.projection.resultManifestHash,
        })
      ).status,
    ).toBe(200);

    const resolvedRevision = await syncPost(
      `/sync/v1/corpora/${corpusId}/revisions:resolve`,
      {
        corpusId,
        documentId,
        sha256: begin.revision.sha256,
        sourceSize: begin.revision.sourceSize,
      },
    );
    expect(await body(resolvedRevision)).toMatchObject({
      result: {
        corpusId,
        documentId,
        revisionId: "rev_first",
      },
    });
    const unresolvedRevision = await syncPost(
      `/sync/v1/corpora/${corpusId}/revisions:resolve`,
      {
        corpusId,
        documentId,
        sha256: "9".repeat(64),
        sourceSize: begin.revision.sourceSize,
      },
    );
    expect(await body(unresolvedRevision)).toMatchObject({
      result: { revisionId: null },
    });
    const conflictingSize = await syncPost(
      `/sync/v1/corpora/${corpusId}/revisions:resolve`,
      {
        corpusId,
        documentId,
        sha256: begin.revision.sha256,
        sourceSize: begin.revision.sourceSize + 1,
      },
    );
    expect(conflictingSize.status).toBe(409);
    expect(await body(conflictingSize)).toMatchObject({
      error: { code: "revision_identity_conflict" },
    });

    const collidingBegin = {
      ...begin,
      uploadId: "upload_cccccccccccccccccccccccccccccccc",
      revision: { ...begin.revision, revisionId: "rev_wrong" },
      projection: {
        ...begin.projection,
        projectionId: "projection_wrong",
        resultManifestHash: "8".repeat(64),
        declaredUnitCount: 0,
      },
    };
    await syncPost(
      `/sync/v1/corpora/${corpusId}/projections:begin`,
      collidingBegin,
    );
    const collidingCommit = await syncPost(
      `/sync/v1/corpora/${corpusId}/projections:commit`,
      {
        uploadId: collidingBegin.uploadId,
        expectedUnitCount: 0,
        expectedManifestHash: collidingBegin.projection.resultManifestHash,
      },
    );
    expect(collidingCommit.status).toBe(409);
    expect(await body(collidingCommit)).toMatchObject({
      error: {
        code: "revision_identity_conflict",
        details: { existingRevisionId: "rev_first" },
      },
    });

    const repeatedBegin = await syncPost(
      `/sync/v1/corpora/${corpusId}/projections:begin`,
      {
        ...begin,
        uploadId: "upload_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        revision: {
          ...begin.revision,
          capturedAt: "2026-09-02T00:00:01.000Z",
        },
      },
    );
    expect(repeatedBegin.status, await repeatedBegin.clone().text()).toBe(200);
    expect(await body(repeatedBegin)).toMatchObject({
      result: {
        projectionId: "projection_first",
        unitCount: 1,
        alreadyCommitted: true,
      },
    });
    const refreshedInventory = await syncPost(
      `/sync/v1/corpora/${corpusId}/inventory`,
      { documentOffset: 0, projectionOffset: 0, limit: 10 },
    );
    expect(await body(refreshedInventory)).toMatchObject({
      result: {
        projections: [
          {
            projection_id: "projection_first",
            captured_at: "2026-09-02T00:00:01.000Z",
          },
        ],
      },
    });

    const shard = runtime.CORPUS_SHARDS.get(
      runtime.CORPUS_SHARDS.idFromName(`owner_test:${corpusId}`),
    );
    const searched = await shard.fetch("https://internal/search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Owner-Id": "owner_test",
      },
      body: JSON.stringify({ query: "durable marker", limit: 10 }),
    });
    expect(JSON.stringify(await body(searched))).toContain("unit_first");

    const badBegin = {
      ...begin,
      uploadId: "upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      revision: {
        ...begin.revision,
        revisionId: "rev_second",
        sha256: "4".repeat(64),
      },
      projection: {
        ...begin.projection,
        projectionId: "projection_second",
        resultManifestHash: "5".repeat(64),
      },
    };
    await syncPost(`/sync/v1/corpora/${corpusId}/projections:begin`, badBegin);
    const failedCommit = await syncPost(
      `/sync/v1/corpora/${corpusId}/projections:commit`,
      {
        uploadId: badBegin.uploadId,
        expectedUnitCount: 1,
        expectedManifestHash: badBegin.projection.resultManifestHash,
      },
    );
    expect(failedCommit.status).toBe(409);

    const searchedAgain = await shard.fetch("https://internal/search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Owner-Id": "owner_test",
      },
      body: JSON.stringify({ query: "durable marker", limit: 10 }),
    });
    expect(JSON.stringify(await body(searchedAgain))).toContain("rev_first");

    const stateResponse = await syncPost(
      `/sync/v1/corpora/${corpusId}/documents/${documentId}/source-state`,
      {
        corpusId,
        documentId,
        sourceState: "unavailable",
        observedAt: "2026-09-02T01:00:00.000Z",
      },
    );
    expect(stateResponse.status).toBe(200);
    const repeatedStateResponse = await syncPost(
      `/sync/v1/corpora/${corpusId}/documents/${documentId}/source-state`,
      {
        corpusId,
        documentId,
        sourceState: "unavailable",
        observedAt: "2026-09-02T01:00:01.000Z",
      },
    );
    expect(await body(repeatedStateResponse)).toMatchObject({
      result: { changed: false },
    });
    const read = await shard.fetch("https://internal/units/read", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Owner-Id": "owner_test",
      },
      body: JSON.stringify({ unitIds: ["unit_first"] }),
    });
    const readBody = await body(read);
    expect(readBody).toMatchObject({
      result: {
        units: [
          {
            dependency_state: "source_unavailable",
            source_anchor: {
              canonical_locator: "folder/한글.txt",
              content_hash: begin.revision.sha256,
              document_id: documentId,
              projection_id: begin.projection.projectionId,
              revision_id: begin.revision.revisionId,
              structural_locator: { paragraph: 1 },
            },
          },
        ],
      },
    });
    expect(readBody).not.toHaveProperty(
      "result.units.0.source_anchor.structure_path",
    );
  });

  it("keeps structural-only units out of the derived search index", async () => {
    const corpusId = "search-storage-test";
    const content = "searchable storage marker 검색 저장";
    const header = {
      uploadId: "upload_ffffffffffffffffffffffffffffffff",
      corpusId,
      document: {
        documentId: "doc_search_storage",
        relativePath: "notes/storage.txt",
        extension: ".txt",
        sourceState: "available",
      },
      revision: {
        revisionId: "rev_search_storage",
        sha256: "c".repeat(64),
        sourceSize: content.length,
        capturedAt: "2026-09-02T00:00:00.000Z",
      },
      projection: {
        projectionId: "projection_search_storage",
        adapterId: "document-files.text",
        adapterVersion: "2",
        configHash: "d".repeat(64),
        resultManifestHash: "e".repeat(64),
        completenessState: "complete",
        coverage: { text_content: "complete", structure: "complete" },
        capabilityManifest: { text: true, structure: true },
        assuranceState: "declared",
        declaredUnitCount: 2,
      },
    };
    const units = [
      {
        unitId: "unit_search_storage_text",
        ordinal: 1,
        unitType: "paragraph",
        structurePath: { paragraph: 1 },
        sourceAnchor: { relative_path: "notes/storage.txt" },
        content,
        contentSha256: await sha256Hex(content),
        previousUnitId: null,
        nextUnitId: "unit_search_storage_structure",
        extractionIssues: [],
        derivationMethod: "native_text",
        geometry: {},
        confidence: 1,
        ocr: false,
        qualityFlags: [],
      },
      {
        unitId: "unit_search_storage_structure",
        ordinal: 2,
        unitType: "table_cell",
        structurePath: {
          cell: "r16",
          col: 0,
          col_span: 2,
          container_kind: "table_cell",
          container_path: [
            {
              cell: "r16",
              col: 0,
              col_span: 2,
              is_header: false,
              kind: "table_cell",
              list_record: 16,
              object: "r14",
              owner_paragraph_record: 1,
              row: 0,
              row_span: 1,
              table: "r14",
            },
          ],
          is_header: false,
          object: "r14",
          owner_paragraph_record: 1,
          row: 0,
          row_span: 1,
          structural_only: true,
          table: "r14",
        },
        sourceAnchor: { relative_path: "notes/storage.txt" },
        content: " \n\t",
        contentSha256: await sha256Hex(" \n\t"),
        previousUnitId: "unit_search_storage_text",
        nextUnitId: null,
        extractionIssues: [],
        derivationMethod: "native_text",
        geometry: {},
        confidence: 1,
        ocr: false,
        qualityFlags: [],
      },
    ];
    expect(
      (
        await syncPost(
          `/sync/v1/corpora/${corpusId}/projections:begin`,
          header,
        )
      ).status,
    ).toBe(200);
    expect(
      (
        await syncPost(
          `/sync/v1/corpora/${corpusId}/projection-units:append`,
          { uploadId: header.uploadId, units },
        )
      ).status,
    ).toBe(200);
    expect(
      (
        await syncPost(`/sync/v1/corpora/${corpusId}/projections:commit`, {
          uploadId: header.uploadId,
          expectedUnitCount: 2,
          expectedManifestHash: header.projection.resultManifestHash,
        })
      ).status,
    ).toBe(200);

    const inventory = await syncPost(
      `/sync/v1/corpora/${corpusId}/inventory`,
      {
        documentOffset: 0,
        projectionOffset: 0,
        limit: 10,
        includeStorageDetails: true,
        hotspotLimit: 1,
      },
    );
    expect(inventory.status, await inventory.clone().text()).toBe(200);
    expect(await body(inventory)).toMatchObject({
      result: {
        counts: { units: 2 },
        storage: { search_index_pending_projections: 0 },
        storage_details: {
          unit_count: 2,
          indexed_unit_count: 1,
          searchable_unit_count: 1,
          structural_only_unit_count: 1,
          source_unit_payload_logical_bytes: expect.any(Number),
          structure_path_logical_bytes: expect.any(Number),
          source_anchor_logical_bytes: expect.any(Number),
          normalized_content_logical_bytes: expect.any(Number),
          compacted_structure_path_count: 1,
          structure_path_compaction_write_enabled: true,
          structure_path_storage_version: 1,
          structure_path_storage_scan_complete: true,
          legacy_redundant_index_count: 0,
          pending_source_anchor_compaction_count: 0,
          hotspots: [
            {
              projection_id: "projection_search_storage",
              unit_count: 2,
            },
          ],
        },
      },
    });

    const shard = runtime.CORPUS_SHARDS.get(
      runtime.CORPUS_SHARDS.idFromName(`owner_test:${corpusId}`),
    );
    await runInDurableObject(shard, (_instance, state) => {
      state.storage.sql.exec(
        "UPDATE source_units_fts SET structure_path = ? WHERE unit_id = ?",
        '{"legacystructuretoken":true}',
        "unit_search_storage_text",
      );
      state.storage.sql.exec(
        "UPDATE projections SET search_index_version = 1 WHERE projection_id = ?",
        "projection_search_storage",
      );
    });
    const legacyStructureSearch = await shard.fetch("https://internal/search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Owner-Id": "owner_test",
      },
      body: JSON.stringify({ query: "legacystructuretoken", limit: 10 }),
    });
    expect(await body(legacyStructureSearch)).toMatchObject({
      result: { count: 1 },
    });

    const reindexed = await syncPost(
      `/sync/v1/corpora/${corpusId}/maintenance`,
      {
        corpusId,
        removeProjectionIds: [],
        removeDocumentIds: [],
        removeUploadIds: [],
        compactSearchIndexLimit: 10,
      },
    );
    expect(reindexed.status, await reindexed.clone().text()).toBe(200);
    expect(await body(reindexed)).toMatchObject({
      result: {
        search_index: {
          version: 2,
          processed_projections: 1,
          reindexed_searchable_rows: 1,
          removed_structural_only_rows: 0,
          excluded_structure_path_logical_bytes: expect.any(Number),
          pending_projections: 0,
          legacy_index_reclaimed: false,
        },
      },
    });
    const structureSearchAfterCompaction = await shard.fetch(
      "https://internal/search",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Owner-Id": "owner_test",
        },
        body: JSON.stringify({ query: "legacystructuretoken", limit: 10 }),
      },
    );
    expect(await body(structureSearchAfterCompaction)).toMatchObject({
      result: { count: 0 },
    });
    const contentSearchAfterCompaction = await shard.fetch(
      "https://internal/search",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Owner-Id": "owner_test",
        },
        body: JSON.stringify({ query: "searchable storage marker", limit: 10 }),
      },
    );
    expect(await body(contentSearchAfterCompaction)).toMatchObject({
      result: {
        count: 1,
        candidates: [{ unit_id: "unit_search_storage_text" }],
      },
    });
    const koreanSearchAfterCompaction = await shard.fetch(
      "https://internal/search",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Owner-Id": "owner_test",
        },
        body: JSON.stringify({ query: "검색 저장", limit: 10 }),
      },
    );
    expect(await body(koreanSearchAfterCompaction)).toMatchObject({
      result: {
        count: 1,
        candidates: [{ unit_id: "unit_search_storage_text" }],
      },
    });

    const restored = await shard.fetch("https://internal/units/read", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Owner-Id": "owner_test",
      },
      body: JSON.stringify({ unitIds: ["unit_search_storage_structure"] }),
    });
    expect(await body(restored)).toMatchObject({
      result: { units: [{ structure_path: units[1]!.structurePath }] },
    });

    await runInDurableObject(shard, (_instance, state) => {
      state.storage.sql.exec(
        "UPDATE source_units SET structure_path_json = ? WHERE unit_id = ?",
        JSON.stringify(units[1]!.structurePath),
        "unit_search_storage_structure",
      );
      state.storage.sql.exec(
        "DELETE FROM shard_meta WHERE key = 'structure_path_storage_version'",
      );
    });

    const compacted = await syncPost(
      `/sync/v1/corpora/${corpusId}/maintenance`,
      {
        corpusId,
        removeProjectionIds: [],
        removeDocumentIds: [],
        removeUploadIds: [],
        compactUnitMetadataLimit: 10,
      },
    );
    expect(compacted.status, await compacted.clone().text()).toBe(200);
    expect(await body(compacted)).toMatchObject({
      result: {
        unit_metadata: {
          scanned_units: 2,
          rewritten_units: 1,
          compacted_units: 0,
          structure_path_scanned_units: 2,
          compacted_structure_path_units: 1,
          source_anchor_complete: true,
          structure_path_complete: true,
          complete: true,
        },
      },
    });

    const repeatedCompaction = await syncPost(
      `/sync/v1/corpora/${corpusId}/maintenance`,
      {
        corpusId,
        removeProjectionIds: [],
        removeDocumentIds: [],
        removeUploadIds: [],
        compactUnitMetadataLimit: 10,
      },
    );
    expect(await body(repeatedCompaction)).toMatchObject({
      result: {
        unit_metadata: {
          scanned_units: 0,
          rewritten_units: 0,
          compacted_structure_path_units: 0,
          complete: true,
        },
      },
    });
  });

  it("imports durable document metadata without activating historical projections", async () => {
    const corpusId = "history-test";
    const documentId = "doc_history_test";
    const now = "2026-09-02T00:00:00.000Z";
    const imported = await syncPost(
      `/sync/v1/corpora/${corpusId}/documents:import`,
      {
        corpusId,
        documents: [
          {
            documentId,
            relativePath: "notes/history.txt",
            extension: ".txt",
            sourceState: "available",
            mediaType: "text/plain",
            logicalSize: 7,
            modifiedNs: "123456789",
            residencyState: "resident",
            eligibilityState: "supported",
            currentRevisionId: "rev_history_new",
            lifecycleState: "active",
            retentionClass: "managed",
            lastUserAccessAt: null,
            firstSeenAt: now,
            lastSeenAt: now,
            deletedAt: null,
          },
        ],
      },
    );
    expect(imported.status, await imported.clone().text()).toBe(200);

    const upload = async (
      suffix: "old" | "new",
      activate: boolean,
      makeCurrent: boolean,
    ) => {
      const content = suffix === "new" ? "current" : "historic";
      const header = {
        uploadId: `upload_${(suffix === "new" ? "c" : "d").repeat(32)}`,
        corpusId,
        document: {
          documentId,
          relativePath: "notes/history.txt",
          extension: ".txt",
          sourceState: "available",
          mediaType: "text/plain",
          logicalSize: 7,
          modifiedNs: "123456789",
          residencyState: "resident",
          eligibilityState: "supported",
          lifecycleState: "active",
          retentionClass: "managed",
          lastUserAccessAt: null,
          deletedAt: null,
        },
        revision: {
          revisionId: `rev_history_${suffix}`,
          sha256: suffix === "new" ? "6".repeat(64) : "7".repeat(64),
          sourceSize: content.length,
          capturedAt: suffix === "new" ? "2026-09-02T00:00:01.000Z" : now,
          predecessorRevisionId: suffix === "new" ? "rev_history_old" : null,
          makeCurrent,
        },
        projection: {
          projectionId: `projection_history_${suffix}`,
          adapterId: "document-files.text",
          adapterVersion: "2",
          configHash: "8".repeat(64),
          resultManifestHash:
            suffix === "new" ? "9".repeat(64) : "a".repeat(64),
          completenessState: "complete",
          coverage: { text_content: "complete" },
          capabilityManifest: { text: true },
          issues: [],
          assuranceState: "declared",
          declaredUnitCount: 1,
          activate,
          createdAt: now,
        },
      };
      const unit = {
        unitId: `unit_history_${suffix}`,
        ordinal: 1,
        unitType: "paragraph",
        structurePath: { paragraph: 1 },
        sourceAnchor: { relative_path: "notes/history.txt" },
        content,
        contentSha256: await sha256Hex(content),
        previousUnitId: null,
        nextUnitId: null,
        extractionIssues: [],
        derivationMethod: "native_text",
        geometry: {},
        confidence: 1,
        ocr: false,
        qualityFlags: [],
      };
      expect(
        (
          await syncPost(
            `/sync/v1/corpora/${corpusId}/projections:begin`,
            header,
          )
        ).status,
      ).toBe(200);
      expect(
        (
          await syncPost(
            `/sync/v1/corpora/${corpusId}/projection-units:append`,
            {
              uploadId: header.uploadId,
              units: [unit],
            },
          )
        ).status,
      ).toBe(200);
      expect(
        (
          await syncPost(`/sync/v1/corpora/${corpusId}/projections:commit`, {
            uploadId: header.uploadId,
            expectedUnitCount: 1,
            expectedManifestHash: header.projection.resultManifestHash,
          })
        ).status,
      ).toBe(200);
    };

    await upload("old", false, false);
    await upload("new", true, true);
    const externalPayload = {
      corpusId,
      bindings: [
        {
          bindingId: "binding_history",
          providerKind: "gmail",
          selector: { label: "research" },
          state: "active",
          lastCompleteRunId: "run_history",
          lastCompleteAt: now,
          createdAt: now,
          updatedAt: now,
        },
      ],
      runs: [
        {
          runId: "run_history",
          bindingId: "binding_history",
          baseCompleteRunId: null,
          status: "complete",
          startedAt: now,
          completedAt: now,
          supersededAt: null,
        },
      ],
      records: [
        {
          sourceRecordId: "record_history",
          bindingId: "binding_history",
          externalId: "message-1",
          parentExternalId: null,
          occurredAt: now,
          title: "Source-linked message",
          participants: ["owner@example.test"],
          labelIds: ["research"],
          attachments: [],
          providerMetadata: { thread: "thread-1" },
          locator: { message_id: "message-1" },
          freshnessIdentity: "message-1:v1",
          metadataSha256: "b".repeat(64),
          membershipState: "active",
          lastSeenRunId: "run_history",
          firstSeenAt: now,
          lastSeenAt: now,
        },
      ],
    };
    const externalImport = await syncPost(
      `/sync/v1/corpora/${corpusId}/external:import`,
      externalPayload,
    );
    expect(externalImport.status, await externalImport.clone().text()).toBe(
      200,
    );
    const repeatedExternalImport = await syncPost(
      `/sync/v1/corpora/${corpusId}/external:import`,
      externalPayload,
    );
    expect(
      repeatedExternalImport.status,
      await repeatedExternalImport.clone().text(),
    ).toBe(200);
    expect(await body(repeatedExternalImport)).toMatchObject({
      result: { changed: false, importedRecordCount: 1 },
    });
    const shard = runtime.CORPUS_SHARDS.get(
      runtime.CORPUS_SHARDS.idFromName(`owner_test:${corpusId}`),
    );
    const inventory = await shard.fetch("https://internal/inventory", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Owner-Id": "owner_test",
      },
      body: "{}",
    });
    const inventoryText = JSON.stringify(await body(inventory));
    expect(inventoryText).toContain('"current_revision_id":"rev_history_new"');
    expect(inventoryText).toContain('"residency_state":"resident"');
    expect(inventoryText).toContain(
      '"external":{"binding_count":1,"run_count":1,"record_count":1}',
    );

    const historicSearch = await shard.fetch("https://internal/search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Owner-Id": "owner_test",
      },
      body: JSON.stringify({ query: "historic", limit: 10 }),
    });
    expect(JSON.stringify(await body(historicSearch))).not.toContain(
      "unit_history_old",
    );
    const currentSearch = await shard.fetch("https://internal/search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Owner-Id": "owner_test",
      },
      body: JSON.stringify({ query: "current", limit: 10 }),
    });
    expect(JSON.stringify(await body(currentSearch))).toContain(
      "unit_history_new",
    );
    let lastUserAccessAt: string | null = null;
    await runInDurableObject(shard, (_instance, state) => {
      const rows = [
        ...state.storage.sql.exec<{ last_user_access_at: string | null }>(
          "SELECT last_user_access_at FROM documents WHERE document_id = ?",
          documentId,
        ),
      ];
      lastUserAccessAt = rows[0]?.last_user_access_at ?? null;
      state.storage.sql.exec(
        `UPDATE documents SET lifecycle_state = 'archived',
           archived_at = '2020-01-01T00:00:00.000Z'
         WHERE document_id = ?`,
        documentId,
      );
    });
    expect(lastUserAccessAt).not.toBeNull();

    const restoredSource = await syncPost(
      `/sync/v1/corpora/${corpusId}/documents/${documentId}/source-state`,
      {
        corpusId,
        documentId,
        sourceState: "available",
        observedAt: "2026-09-02T01:00:00.000Z",
      },
    );
    expect(await body(restoredSource)).toMatchObject({
      result: { changed: true },
    });
    let restoredLifecycle: {
      lifecycle_state: string;
      archived_at: string | null;
      trashed_at: string | null;
    } | null = null;
    await runInDurableObject(shard, (_instance, state) => {
      restoredLifecycle = [
        ...state.storage.sql.exec<{
          lifecycle_state: string;
          archived_at: string | null;
          trashed_at: string | null;
        }>(
          `SELECT lifecycle_state, archived_at, trashed_at
           FROM documents WHERE document_id = ?`,
          documentId,
        ),
      ][0] ?? null;
    });
    expect(restoredLifecycle).toEqual({
      lifecycle_state: "active",
      archived_at: null,
      trashed_at: null,
    });

    const maintained = await syncPost(
      `/sync/v1/corpora/${corpusId}/maintenance`,
      {
        corpusId,
        removeProjectionIds: [
          "projection_history_old",
          "projection_history_new",
        ],
        removeDocumentIds: [],
        removeUploadIds: [],
      },
    );
    expect(maintained.status, await maintained.clone().text()).toBe(200);
    expect(await body(maintained)).toMatchObject({
      result: {
        removed: { projections: 1, revisions: 1, units: 1 },
        protected: { projections: 1 },
        storage: { database_size_bytes: expect.any(Number) },
      },
    });

    await runInDurableObject(shard, (_instance, state) => {
      state.storage.sql.exec(
        `UPDATE documents SET source_state = 'unavailable',
           deleted_at = '2020-01-01T00:00:00.000Z',
           lifecycle_state = 'archived', retention_class = 'protected',
           last_user_access_at = NULL,
           archived_at = '2020-01-01T00:00:00.000Z', trashed_at = NULL
         WHERE document_id = ?`,
        documentId,
      );
    });
    const protectedRetention = await syncPost(
      `/sync/v1/corpora/${corpusId}/maintenance`,
      {
        corpusId,
        removeProjectionIds: [],
        removeDocumentIds: [documentId],
        removeUploadIds: [],
        applyRetentionLimit: 10,
      },
    );
    expect(await body(protectedRetention)).toMatchObject({
      result: {
        retention: {
          actions: [{ document_id: documentId, action: "restore_protected" }],
          action_count: 1,
          purged: { documents: 0 },
        },
        protected: { documents: 1 },
      },
    });

    await runInDurableObject(shard, (_instance, state) => {
      state.storage.sql.exec(
        `UPDATE documents SET lifecycle_state = 'active',
           retention_class = 'managed', archived_at = NULL, trashed_at = NULL
         WHERE document_id = ?`,
        documentId,
      );
    });
    const retentionRequest = {
      corpusId,
      removeProjectionIds: [],
      removeDocumentIds: [],
      removeUploadIds: [],
      applyRetentionLimit: 10,
    };
    const retentionPreview = await syncPost(
      `/sync/v1/corpora/${corpusId}/maintenance`,
      { ...retentionRequest, retentionDryRun: true },
    );
    expect(await body(retentionPreview)).toMatchObject({
      result: {
        retention: {
          dry_run: true,
          actions: [{ document_id: documentId, action: "archive" }],
        },
      },
    });

    const archived = await syncPost(
      `/sync/v1/corpora/${corpusId}/maintenance`,
      retentionRequest,
    );
    expect(await body(archived)).toMatchObject({
      result: { retention: { actions: [{ action: "archive" }] } },
    });
    await runInDurableObject(shard, (_instance, state) => {
      state.storage.sql.exec(
        "UPDATE documents SET archived_at = ? WHERE document_id = ?",
        "2020-01-01T00:00:00.000Z",
        documentId,
      );
    });
    const trashed = await syncPost(
      `/sync/v1/corpora/${corpusId}/maintenance`,
      retentionRequest,
    );
    expect(await body(trashed)).toMatchObject({
      result: { retention: { actions: [{ action: "trash" }] } },
    });
    await runInDurableObject(shard, (_instance, state) => {
      state.storage.sql.exec(
        "UPDATE documents SET trashed_at = ? WHERE document_id = ?",
        "2020-01-01T00:00:00.000Z",
        documentId,
      );
    });
    const purged = await syncPost(
      `/sync/v1/corpora/${corpusId}/maintenance`,
      retentionRequest,
    );
    expect(await body(purged)).toMatchObject({
      result: {
        retention: {
          actions: [{ action: "purge" }],
          purged: { documents: 1, revisions: 1, projections: 1, units: 1 },
        },
      },
    });
  });

  it("imports path-free Corpus metadata and serves one shared Space", async () => {
    const now = "2026-09-02T00:00:00.000Z";
    const imported = await syncPost("/sync/v1/import/corpus-metadata", {
      schemaVersion: 1,
      sourceDigest: "a".repeat(64),
      sourceSchemaVersion: 1,
      spaces: [
        {
          spaceId: "durable-space",
          displayName: "Durable Space",
          state: "active",
          accessScope: "remote_allowed",
          primaryWorkConnectionId: "main",
          updatedAt: now,
        },
      ],
      contexts: [
        {
          spaceId: "durable-space",
          title: "Durable Context",
          purpose: "Keep interpreted knowledge independent of source location.",
          scope: { topic: "durability" },
          version: 1,
          updatedAt: now,
          items: [
            {
              itemId: "item_durable",
              kind: "finding",
              bodyText:
                "The last committed record remains readable while its source is offline.",
              attributes: { status: "adopted" },
              disclosureState: "restricted",
              lifecycleState: "active",
              supersedesItemId: null,
              createdAt: now,
            },
          ],
          sources: [],
          skill: null,
        },
      ],
      devices: [
        {
          deviceId: "test-mac",
          displayName: "Test Mac",
          status: "active",
          capabilities: ["work.file.read"],
          createdAt: now,
          updatedAt: now,
        },
      ],
      connections: [
        {
          spaceId: "durable-space",
          connectionId: "main",
          displayName: "Documents",
          roles: ["source", "work"],
          accessScope: "remote_allowed",
          permission: "read_write",
          indexMode: "indexed",
          corpusId: "durable-test",
          deviceId: "test-mac",
          localConnectionKey: "connection_durable",
          generation: 1,
          configurationState: "ready",
          sourceState: "ready",
          recordState: "committed",
          capturedAt: now,
          updatedAt: now,
        },
      ],
      currentFiles: [
        {
          spaceId: "durable-space",
          connectionId: "main",
          relativePath: "drafts/note.md",
          versionToken: "sha256:1234",
          state: "ready",
          reason: null,
          residencyState: "resident",
          size: 42,
          modifiedNs: "1",
          updatedAt: now,
        },
      ],
    });
    expect(imported.status, await imported.clone().text()).toBe(200);
    const service = new CorpusService(runtime, ownerPrincipal);
    const listing = await service.spaceList({});
    expect(JSON.stringify(listing)).toContain("Durable Context");
    expect(JSON.stringify(listing)).not.toContain("/Users/");
    const opened = await service.spaceGet({ space_id: "durable-space" });
    expect(JSON.stringify(opened)).toContain("last committed record");
    expect(JSON.stringify(opened)).toContain("drafts/note.md");
    const verification = await SELF.fetch(
      "https://context.test/sync/v1/verification-summary",
      { headers: syncHeaders },
    );
    expect(verification.status, await verification.clone().text()).toBe(200);
    expect(await body(verification)).toMatchObject({
      result: {
        mcp_surfaces: {
          sense: {
            version: "0.3.5-remote.1",
            tools: expect.arrayContaining(["sense_read"]),
          },
          corpus: {
            version: "0.21.5-remote.3",
            tools: expect.arrayContaining([
              "corpus_source_refresh",
              "corpus_job_status",
            ]),
          },
          hypes: { version: "0.9.4-remote.1", tools: ["hypes_read", "hypes_rewrite"] },
        },
        corpus_metadata: { source_digest: "a".repeat(64) },
      },
    });
  });

  it("queues bounded Work and Source jobs while the Sync device is offline", async () => {
    const now = "2026-09-02T00:00:00.000Z";
    await runtime.STATE_DB.batch([
      runtime.STATE_DB.prepare(
        `INSERT INTO sync_devices(
           owner_id, device_id, display_name, credential_id, status,
           capabilities_json, last_seen_at, created_at, updated_at
         ) VALUES (?, ?, ?, ?, 'active', '[]', NULL, ?, ?)`,
      ).bind(
        "owner_test",
        "offline-mac",
        "Offline Mac",
        "environment:offline-mac",
        now,
        now,
      ),
      runtime.STATE_DB.prepare(
        `INSERT INTO corpus_spaces(
           owner_id, space_id, display_name, state, access_scope,
           primary_work_connection_id, updated_at
         ) VALUES (?, ?, ?, 'active', 'remote_allowed', 'main', ?)`,
      ).bind("owner_test", "offline-space", "Offline Space", now),
      runtime.STATE_DB.prepare(
        `INSERT INTO corpus_connections(
           owner_id, space_id, connection_id, display_name, roles_json,
           access_scope, permission, index_mode, corpus_id, device_id,
           local_connection_key, generation, configuration_state, source_state,
           record_state, captured_at, updated_at
         ) VALUES (?, ?, 'main', 'Offline Source and Work', '["source","work"]',
                   'remote_allowed', 'read_write', 'indexed', 'offline-corpus',
                   'offline-mac', 'offline-key', 1, 'ready', 'available',
                   'committed', ?, ?)`,
      ).bind("owner_test", "offline-space", now, now),
    ]);

    const service = new CorpusService(runtime, ownerPrincipal);
    await expect(
      service.fileList({
        space_id: "offline-space",
        connection_id: "main",
        mode: "list_directory",
        limit: 10,
      }),
    ).resolves.toMatchObject({
      pending: true,
      state: "queued",
      device_online: false,
    });
    const stored = await runtime.STATE_DB.prepare(
      `SELECT job_id, operation, state, maximum_response_bytes
       FROM sync_jobs WHERE owner_id = ? AND device_id = ? AND operation = ?`,
    )
      .bind("owner_test", "offline-mac", "work.file.list")
      .first<{
        job_id: string;
        operation: string;
        state: string;
        maximum_response_bytes: number;
      }>();
    expect(stored).toMatchObject({
      operation: "work.file.list",
      state: "queued",
      maximum_response_bytes: 2 * 1024 * 1024,
    });
    await runtime.STATE_DB.prepare(
      "UPDATE sync_jobs SET expires_at = ? WHERE owner_id = ? AND job_id = ?",
    )
      .bind("2020-01-01T00:00:00.000Z", "owner_test", stored!.job_id)
      .run();
    await expect(
      service.jobStatus({ job_id: stored!.job_id }),
    ).resolves.toMatchObject({
      state: "expired",
      expires_at: "2020-01-01T00:00:00.000Z",
    });

    const documentId = `doc_${"b".repeat(32)}`;
    await expect(
      service.sourceRefresh({
        space_id: "offline-space",
        connection_id: "main",
        document_id: documentId,
        expected_revision_sha256: "c".repeat(64),
      }),
    ).resolves.toMatchObject({
      pending: true,
      state: "queued",
      device_online: false,
    });
    const refresh = await runtime.STATE_DB.prepare(
      `SELECT job_id, operation, state, request_json, expires_at, created_at
       FROM sync_jobs WHERE owner_id = ? AND device_id = ? AND operation = ?`,
    )
      .bind("owner_test", "offline-mac", "source.refresh")
      .first<{
        job_id: string;
        operation: string;
        state: string;
        request_json: string;
        expires_at: string;
        created_at: string;
      }>();
    expect(refresh).toMatchObject({ operation: "source.refresh", state: "queued" });
    expect(JSON.parse(refresh!.request_json)).toMatchObject({
      document_id: documentId,
      expected_revision_sha256: "c".repeat(64),
    });
    const refreshTtl =
      Date.parse(refresh!.expires_at) - Date.parse(refresh!.created_at);
    expect(refreshTtl).toBeGreaterThanOrEqual(30 * 60_000);
    expect(refreshTtl).toBeLessThan(30 * 60_000 + 1000);
    await expect(
      service.jobStatus({ job_id: refresh!.job_id }),
    ).resolves.toMatchObject({ operation: "source.refresh", state: "queued" });
  });

  it("drains an offline Work backlog without exceeding the in-flight bound", async () => {
    const now = "2026-09-02T00:00:00.000Z";
    await runtime.STATE_DB.batch([
      runtime.STATE_DB.prepare(
        `INSERT INTO sync_devices(
           owner_id, device_id, display_name, credential_id, status,
           capabilities_json, last_seen_at, created_at, updated_at
         ) VALUES (?, ?, ?, ?, 'active', '[]', NULL, ?, ?)`,
      ).bind(
        "owner_test",
        "backlog-mac",
        "Backlog Mac",
        "environment:backlog-mac",
        now,
        now,
      ),
      runtime.STATE_DB.prepare(
        `INSERT INTO corpus_spaces(
           owner_id, space_id, display_name, state, access_scope,
           primary_work_connection_id, updated_at
         ) VALUES (?, ?, ?, 'active', 'remote_allowed', 'main', ?)`,
      ).bind("owner_test", "backlog-space", "Backlog Space", now),
      runtime.STATE_DB.prepare(
        `INSERT INTO corpus_connections(
           owner_id, space_id, connection_id, display_name, roles_json,
           access_scope, permission, index_mode, corpus_id, device_id,
           local_connection_key, generation, configuration_state, source_state,
           record_state, captured_at, updated_at
         ) VALUES (?, ?, 'main', 'Backlog Work', '["work"]', 'remote_allowed',
                   'read_write', 'not_indexed', NULL, 'backlog-mac', 'backlog-key',
                   1, 'ready', NULL, NULL, NULL, ?)`,
      ).bind("owner_test", "backlog-space", now),
    ]);

    const service = new CorpusService(runtime, ownerPrincipal);
    for (let index = 0; index < 21; index += 1) {
      await service.fileList({
        space_id: "backlog-space",
        connection_id: "main",
        mode: "list_directory",
        relative_path: `folder-${index}`,
        limit: 1,
      });
    }
    const staleJobId = `job_${"a".repeat(32)}`;
    await runtime.STATE_DB.prepare(
      `INSERT INTO sync_jobs(
         owner_id, job_id, device_id, operation, scope_json, request_json,
         response_json, idempotency_key, state, maximum_response_bytes,
         expires_at, created_at, updated_at, completed_at
       ) VALUES (?, ?, ?, 'work.file.list', '{}', '{}', '{}', ?, 'succeeded',
                 1024, ?, ?, ?, ?)`,
    )
      .bind(
        "owner_test",
        staleJobId,
        "backlog-mac",
        staleJobId,
        "2020-01-01T00:05:00.000Z",
        "2020-01-01T00:00:00.000Z",
        "2020-01-01T00:00:01.000Z",
        "2020-01-01T00:00:01.000Z",
      )
      .run();

    const connected = await SELF.fetch("https://context.test/sync/v1/connect", {
      headers: {
        Authorization: "Bearer test-device-token",
        "X-Personal-Agent-Device": "backlog-mac",
        Upgrade: "websocket",
      },
    });
    expect(connected.status).toBe(101);
    const socket = connected.webSocket!;
    socket.accept();
    const initialMessages = nextSocketMessages(socket, 21);
    socket.send(
      JSON.stringify({
        type: "hello",
        protocolVersion: 1,
        displayName: "Backlog Mac",
        capabilities: ["work.file.list"],
      }),
    );
    const initial = await initialMessages;
    expect(initial[0]).toMatchObject({ type: "hello_ack" });
    const initialJobs = initial.slice(1);
    expect(initialJobs).toHaveLength(20);
    expect(initialJobs.every((message) => message.type === "job")).toBe(true);
    expect(
      await runtime.STATE_DB.prepare(
        "SELECT job_id FROM sync_jobs WHERE owner_id = ? AND job_id = ?",
      )
        .bind("owner_test", staleJobId)
        .first(),
    ).toBeNull();

    const completionMessages = nextSocketMessages(socket, 2);
    socket.send(
      JSON.stringify({
        type: "job_result",
        jobId: initialJobs[0]!.jobId,
        ok: true,
        result: { entries: [] },
      }),
    );
    const [ack, nextJob] = await completionMessages;
    expect(ack).toMatchObject({
      type: "job_ack",
      jobId: initialJobs[0]!.jobId,
      accepted: true,
    });
    expect(nextJob).toMatchObject({ type: "job" });
    expect(initialJobs.map((message) => message.jobId)).not.toContain(
      nextJob!.jobId,
    );
    socket.close(1000, "test complete");
  });

  it("dispatches a bounded Work job over the outbound Sync WebSocket", async () => {
    const now = "2026-09-02T00:00:00.000Z";
    await runtime.STATE_DB.batch([
      runtime.STATE_DB.prepare(
        `INSERT INTO sync_devices(
           owner_id, device_id, display_name, credential_id, status,
           capabilities_json, last_seen_at, created_at, updated_at
         ) VALUES (?, ?, ?, ?, 'active', '[]', NULL, ?, ?)
         ON CONFLICT(owner_id, device_id) DO UPDATE SET status = 'active'`,
      ).bind(
        "owner_test",
        "socket-mac",
        "Socket Mac",
        "environment:socket-mac",
        now,
        now,
      ),
      runtime.STATE_DB.prepare(
        `INSERT INTO corpus_spaces(
           owner_id, space_id, display_name, state, access_scope,
           primary_work_connection_id, updated_at
         ) VALUES (?, ?, ?, 'active', 'remote_allowed', 'main', ?)
         ON CONFLICT(owner_id, space_id) DO UPDATE SET updated_at = excluded.updated_at`,
      ).bind("owner_test", "socket-space", "Socket Space", now),
      runtime.STATE_DB.prepare(
        `INSERT INTO corpus_connections(
           owner_id, space_id, connection_id, display_name, roles_json,
           access_scope, permission, index_mode, corpus_id, device_id,
           local_connection_key, generation, configuration_state, source_state,
           record_state, captured_at, updated_at
         ) VALUES (?, ?, 'main', 'Socket Work', '["work"]', 'remote_allowed',
                   'read_write', 'not_indexed', NULL, 'socket-mac', 'socket-key',
                   1, 'ready', NULL, NULL, NULL, ?)
         ON CONFLICT(owner_id, space_id, connection_id) DO UPDATE SET
           device_id = 'socket-mac', generation = 1, updated_at = excluded.updated_at`,
      ).bind("owner_test", "socket-space", now),
    ]);

    const connected = await SELF.fetch("https://context.test/sync/v1/connect", {
      headers: {
        Authorization: "Bearer test-device-token",
        "X-Personal-Agent-Device": "socket-mac",
        Upgrade: "websocket",
      },
    });
    expect(connected.status).toBe(101);
    const socket = connected.webSocket;
    expect(socket).not.toBeNull();
    socket!.accept();
    const ackPromise = nextSocketMessage(socket!);
    socket!.send(
      JSON.stringify({
        type: "hello",
        protocolVersion: 1,
        displayName: "Socket Mac",
        capabilities: ["work.file.list"],
      }),
    );
    expect(await ackPromise).toMatchObject({
      type: "hello_ack",
      deviceId: "socket-mac",
    });

    const service = new CorpusService(runtime, ownerPrincipal);
    const resultPromise = service.fileList({
      space_id: "socket-space",
      connection_id: "main",
      mode: "list_directory",
      limit: 10,
    });
    const job = await nextSocketMessage(socket!);
    expect(job).toMatchObject({
      type: "job",
      operation: "work.file.list",
      scope: { spaceId: "socket-space", connectionId: "main", generation: 1 },
    });
    socket!.send(
      JSON.stringify({
        type: "job_result",
        jobId: job.jobId,
        ok: true,
        result: { entries: [{ relative_path: "draft.md", kind: "file" }] },
      }),
    );
    await expect(resultPromise).resolves.toMatchObject({
      entries: [{ relative_path: "draft.md", kind: "file" }],
    });
    socket!.close(1000, "test complete");
  });
});
