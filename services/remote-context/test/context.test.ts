import { SELF, env } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { contentSha256, sha256Hex } from "../src/canonical";
import { CorpusService } from "../src/corpus";
import { HypesService } from "../src/hypes";
import { handleMcp } from "../src/mcp";
import { SenseService } from "../src/sense";
import type { Env, Principal } from "../src/types";

const runtime = env as unknown as Env;
const syncHeaders = {
  Authorization: "Bearer test-device-token",
  "X-Personal-Agent-Device": "test-mac",
  "Content-Type": "application/json",
};

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
    const expected = {
      sense: [
        "sense_read",
        "sense_overview",
        "sense_revise",
        "sense_skill_revise",
      ],
      hypes: ["hypes_read", "hypes_rewrite"],
      corpus: [
        "corpus_space_list",
        "corpus_space_get",
        "corpus_context_items_revise",
        "corpus_context_skill_revise",
        "corpus_space_search",
        "corpus_file_list",
        "corpus_file_read",
        "corpus_file_write",
        "corpus_file_delete",
        "corpus_file_select_current",
        "corpus_file_restore",
      ],
    } as const;
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
      expect(result.tools.map((tool) => tool.name)).toEqual(expected[kind]);
      for (const tool of result.tools) {
        expect(tool.inputSchema).toMatchObject({ type: "object" });
      }
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
        revision_id: begin.revision.revisionId,
        schema_version: 2,
        source_span: { paragraph: 1 },
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
    expect(await body(read)).toMatchObject({
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
      result: { corpus_metadata: { source_digest: "a".repeat(64) } },
    });
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
