import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { CorpusDocumentsService } from "../src/corpus-documents";
import { CorpusManagementService } from "../src/corpus-management";
import { CorpusService } from "../src/corpus";
import { contextOperations, executeContextOperation } from "../src/context-api";
import { runTrashMaintenance } from "../src/trash-maintenance";
import { guard, guardedBatch } from "../src/management-db";
import type { Env, Principal } from "../src/types";

const runtime = {
  ...(env as unknown as Env),
  CORPUS_MANAGEMENT_WRITE_ENABLED: "true",
};
async function fixture() {
  const principal: Principal = {
    ownerId: `management_${crypto.randomUUID()}`,
    scopes: new Set(["corpus.read", "corpus.write"]),
    auth: "site",
    clientId: "test",
  };
  const documents = new CorpusDocumentsService(runtime, principal);
  await documents.spaceCreate({ space_id: "a", display_name: "A" });
  await documents.spaceCreate({ space_id: "b", display_name: "B" });
  const input = {
    space_id: "a",
    document_id: "text",
    title: "Text",
    body_markdown: "current",
  };
  await documents.documentCreate(input);
  const lifecycle = new CorpusManagementService(runtime, principal);
  return { principal, documents, lifecycle, input };
}
const key = () => crypto.randomUUID();
async function trashDocument(
  lifecycle: CorpusManagementService,
  expected_version = 1,
) {
  const preview = await lifecycle.preview({
    action: "trash",
    target: { kind: "document", space_id: "a", id: "text" },
  });
  return lifecycle.trash("document", {
    space_id: "a",
    document_id: "text",
    expected_version,
    impact_token: preview.impact_token,
    idempotency_key: key(),
  });
}

describe("Corpus management", () => {
  it("runs the disabled and enabled integrated sweeper against all affected D1 stores without early deletion", async () => {
    const {LibraryService}=await import("personal-agent-library-service/service");
    const {LibraryManagementService}=await import("personal-agent-library-service/management");
    const {DesignService}=await import("personal-agent-design-service/service");
    const {DesignManagementService}=await import("personal-agent-design-service/management");
    const at="2026-09-01T00:00:00.000Z",due="2026-10-01T00:00:00.000Z";
    const {principal}=await fixture();
    const corpus=new CorpusManagementService(runtime,principal,()=>at),corpusGroup=await trashDocument(corpus);
    const library=new LibraryService({DB:runtime.LIBRARY_DB,MEDIA:runtime.LIBRARY_MEDIA});
    const libraryId="daily:2026-09-01:01";
    await library.createIssue({id:libraryId,source_html:'<!doctype html><html><head><title>Isolated sweep</title></head><body><h1>Isolated sweep</h1><p class="lead">Test</p><article><p>Test</p></article></body></html>',references:[]});
    const libraryManagement=new LibraryManagementService(runtime.LIBRARY_DB,runtime.LIBRARY_MEDIA,true,()=>at);
    const lp=await libraryManagement.preview({action:"trash",issue_id:libraryId});
    const libraryGroup=await libraryManagement.trash({issue_id:libraryId,expected_version:lp.expected_version,impact_token:lp.impact_token,idempotency_key:key()});
    const design=new DesignService({DB:runtime.DESIGN_DB,ASSETS:runtime.DESIGN_ASSETS});
    await design.createRecipe({id:"isolated-sweep",name:"Test",description:"Test",version:"1",status:"validated",selection_ready:true,kind:"recipe",visibility:"private",pattern_refs:[],formats:["web"]});
    const designManagement=new DesignManagementService(runtime.DESIGN_DB,runtime.DESIGN_ASSETS,true,()=>at),dp=await designManagement.preview({action:"trash",kind:"recipe",recipe_id:"isolated-sweep"});
    const designGroup=await designManagement.trash("recipe",{recipe_id:"isolated-sweep",expected_version:dp.expected_version,impact_token:dp.impact_token,idempotency_key:key()});
    const disabled=await runTrashMaintenance({...runtime,TRASH_SWEEP_ENABLED:"false"},Date.parse(due));
    expect(disabled).toMatchObject({enabled:false,candidate_count:3});
    expect(await runTrashMaintenance({...runtime,TRASH_SWEEP_ENABLED:"true"},Date.parse(due)-1)).toMatchObject({candidate_count:0});
    const result=await runTrashMaintenance({...runtime,TRASH_SWEEP_ENABLED:"true"},Date.parse(due));
    expect(result).toMatchObject({enabled:true,candidate_count:3});
    for(const [db,table,id] of [[runtime.STATE_DB,"corpus_trash_groups",corpusGroup.deletion_group_id],[runtime.LIBRARY_DB,"library_trash_groups",libraryGroup.deletion_group_id],[runtime.DESIGN_DB,"design_trash_groups",designGroup.deletion_group_id]] as const)
      expect(await db.prepare(`SELECT state FROM ${table} WHERE deletion_group_id=?`).bind(id).first()).toEqual({state:"purged"});
    expect(await runtime.LIBRARY_DB.prepare("SELECT state FROM library_maintenance_runs WHERE run_id='trash'").first()).toEqual({state:"completed"});
  });

  it("allows only one winner between restore and due cleanup, retaining the exact final lifecycle", async () => {
    const { principal, documents, lifecycle } = await fixture();
    const group = await trashDocument(lifecycle);
    const preview = await lifecycle.preview({
      action: "restore",
      deletion_group_id: group.deletion_group_id,
    });
    const due = new CorpusManagementService(runtime, principal, () =>
      String(group.purge_after),
    );
    const outcomes = await Promise.allSettled([
      lifecycle.restore({
        deletion_group_id: group.deletion_group_id,
        expected_version: 1,
        impact_token: preview.impact_token,
        idempotency_key: key(),
      }),
      due.purgeDue(String(group.deletion_group_id)),
    ]);
    expect(outcomes.some((r) => r.status === "fulfilled")).toBe(true);
    const state = await runtime.STATE_DB.prepare(
      "SELECT state FROM corpus_trash_groups WHERE owner_id=? AND deletion_group_id=?",
    )
      .bind(principal.ownerId, group.deletion_group_id)
      .first<{ state: string }>();
    expect(["restored", "purged"]).toContain(state?.state);
    const visible = (await documents.documentList({ space_id: "a" }))
      .items as unknown[];
    expect(visible.length).toBe(state?.state === "restored" ? 1 : 0);
  });

  it("refuses ordinary writes from a maintenance actor and separates rollout activation from authorization", async () => {
    const { principal } = await fixture();
    const { executeOperation, operationCapabilities } = await import(
      "@personal-agent/remote-runtime"
    );
    const op = contextOperations(
      { ...runtime, CORPUS_MANAGEMENT_WRITE_ENABLED: "false" },
      principal,
    ).corpus_document_move!;
    const actor = {
      ownerId: principal.ownerId,
      clientId: "test",
      kind: "owner" as const,
      scopes: principal.scopes,
    };
    expect(operationCapabilities(actor, { move: op }).move).toMatchObject({
      status: "available",
      enabled: false,
      authorized: true,
    });
    await expect(
      executeOperation({ ...actor, kind: "maintenance" }, op, {}),
    ).rejects.toMatchObject({ code: "insufficient_scope" });
    await expect(executeOperation(actor, op, {})).rejects.toMatchObject({
      code: "operation_not_enabled",
    });
  });

  it("moves one identity and both snapshots, resolves aliases only for reading, and replays the exact result", async () => {
    const { documents, lifecycle, input } = await fixture();
    const before = (
      await documents.documentRead({
        space_id: input.space_id,
        document_id: input.document_id,
      })
    ).document as Record<string, unknown>;
    await documents.documentRevise({
      ...input,
      body_markdown: "revised",
      expected_version: 1,
    });
    const move = {
      space_id: "a",
      document_id: "text",
      destination_space_id: "b",
      expected_version: 2,
      expected_source_version: 1,
      expected_destination_version: 1,
      idempotency_key: key(),
    };
    const moved = await lifecycle.documentMove(move);
    expect(await lifecycle.documentMove(move)).toEqual(moved);
    expect(moved).toMatchObject({ uid: before.uid, version: 3, space_id: "b" });
    expect((await documents.documentList({ space_id: "a" })).items).toEqual([]);
    expect(
      await documents.documentRead({
        space_id: input.space_id,
        document_id: input.document_id,
      }),
    ).toMatchObject({
      document: {
        uid: before.uid,
        space_id: "b",
        body_markdown: "revised",
        relocated_from: { space_id: "a" },
      },
    });
    expect(
      await documents.documentRead({
        document_id: input.document_id,
        space_id: "b",
        snapshot: "previous",
      }),
    ).toMatchObject({ document: { body_markdown: "current", version: 1 } });
    await expect(
      documents.documentRevise({ ...input, expected_version: 2 }),
    ).rejects.toMatchObject({ code: "document_not_found" });
    await expect(documents.documentCreate(input)).rejects.toMatchObject({
      code: "document_conflict",
    });
    await expect(
      lifecycle.documentMove({ ...move, destination_document_id: "different" }),
    ).rejects.toMatchObject({ code: "request_key_conflict" });
  });

  it("rejects destination collision and a stale parent without a partial move", async () => {
    const { documents, lifecycle, input } = await fixture();
    await documents.documentCreate({ ...input, space_id: "b" });
    await expect(
      lifecycle.documentMove({
        space_id: "a",
        document_id: "text",
        destination_space_id: "b",
        expected_version: 1,
        expected_source_version: 1,
        expected_destination_version: 1,
        idempotency_key: key(),
      }),
    ).rejects.toMatchObject({ code: "management_conflict" });
    await expect(
      lifecycle.documentMove({
        space_id: "a",
        document_id: "text",
        destination_space_id: "b",
        destination_document_id: "free",
        expected_version: 1,
        expected_source_version: 1,
        expected_destination_version: 9,
        idempotency_key: key(),
      }),
    ).rejects.toMatchObject({ code: "management_conflict" });
    expect(
      await documents.documentRead({
        space_id: input.space_id,
        document_id: input.document_id,
      }),
    ).toMatchObject({ document: { space_id: "a", version: 1 } });
  });

  it("trash retries keep the first deadline, and restore starts no new deadline until a new deletion", async () => {
    const { documents, lifecycle, input } = await fixture();
    const preview = await lifecycle.preview({
      action: "trash",
      target: { kind: "document", space_id: "a", id: "text" },
    });
    const request = {
      space_id: "a",
      document_id: "text",
      expected_version: 1,
      impact_token: preview.impact_token,
      idempotency_key: key(),
    };
    const deleted = await lifecycle.trash("document", request);
    expect(await lifecycle.trash("document", request)).toEqual(deleted);
    expect((await documents.documentList({ space_id: "a" })).items).toEqual([]);
    await expect(
      documents.documentRevise({ ...input, expected_version: 1 }),
    ).rejects.toMatchObject({ code: "document_trashed" });
    const restore = await lifecycle.preview({
      action: "restore",
      deletion_group_id: deleted.deletion_group_id,
    });
    await lifecycle.restore({
      deletion_group_id: deleted.deletion_group_id,
      expected_version: 1,
      impact_token: restore.impact_token,
      idempotency_key: key(),
    });
    expect(
      await documents.documentRead({
        space_id: input.space_id,
        document_id: input.document_id,
      }),
    ).toMatchObject({ document: { body_markdown: "current", version: 3 } });
    const next = await trashDocument(lifecycle, 3);
    expect(next.deletion_group_id).not.toBe(deleted.deletion_group_id);
  });

  it("restores a Space group without reviving previously trashed children; registrations block purge", async () => {
    const { documents, lifecycle, principal, input } = await fixture();
    const earlier = await trashDocument(lifecycle);
    await documents.documentCreate({ ...input, document_id: "second" });
    await documents.workspaceBind({
      host_id: "fixture",
      workspace_id: "a",
      space_id: "a",
      environment_kind: "local",
      expected_version: "absent",
    });
    const preview = await lifecycle.preview({
      action: "trash",
      target: { kind: "space", space_id: "a", id: "a" },
    });
    const deleted = await lifecycle.trash("space", {
      space_id: "a",
      expected_version: 2,
      impact_token: preview.impact_token,
      idempotency_key: key(),
    });
    expect(deleted.member_count).toBe(2);
    const corpus = new CorpusService(runtime, principal);
    expect((await corpus.spaceList({})).spaces).toEqual([
      expect.objectContaining({ space_id: "b" }),
    ]);
    await expect(
      documents.documentCreate({ ...input, document_id: "new" }),
    ).rejects.toMatchObject({ code: "space_not_found" });
    const purge = await lifecycle.preview({
      action: "purge",
      deletion_group_id: deleted.deletion_group_id,
    });
    expect(purge.blockers).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: "registrations_attached" }),
        expect.objectContaining({ code: "independent_deletion_groups" }),
      ]),
    );
    expect(
      await lifecycle.purge({
        deletion_group_id: deleted.deletion_group_id,
        expected_version: 1,
        impact_token: purge.impact_token,
        idempotency_key: key(),
        confirm_permanent_delete: true,
      }),
    ).toMatchObject({ state: "blocked" });
    const restore = await lifecycle.preview({
      action: "restore",
      deletion_group_id: deleted.deletion_group_id,
    });
    await lifecycle.restore({
      deletion_group_id: deleted.deletion_group_id,
      expected_version: 1,
      impact_token: restore.impact_token,
      idempotency_key: key(),
    });
    expect((await documents.documentList({ space_id: "a" })).items).toEqual([
      expect.objectContaining({ document_id: "second" }),
    ]);
    expect(await lifecycle.trashList({})).toMatchObject({
      groups: [
        expect.objectContaining({
          deletion_group_id: earlier.deletion_group_id,
        }),
      ],
    });
  });

  it("does not auto-delete until 30 full days; disabled scheduled runs are nonmutating", async () => {
    const { principal, documents } = await fixture();
    const created = "2026-01-01T12:00:00.000Z";
    const lifecycle = new CorpusManagementService(
      runtime,
      principal,
      () => created,
    );
    const deleted = await trashDocument(lifecycle);
    expect(deleted.purge_after).toBe("2026-01-31T12:00:00.000Z");
    expect(
      await new CorpusManagementService(
        runtime,
        principal,
        () => "2026-01-31T11:59:59.999Z",
      ).purgeDue(String(deleted.deletion_group_id)),
    ).toMatchObject({ state: "not_due" });
    await runTrashMaintenance(
      { ...runtime, TRASH_SWEEP_ENABLED: "false" },
      Date.parse(String(deleted.purge_after)),
    );
    expect(
      (await documents.documentList({ space_id: "a", lifecycle: "trash" }))
        .items,
    ).toHaveLength(1);
    await runTrashMaintenance(
      { ...runtime, TRASH_SWEEP_ENABLED: "true" },
      Date.parse(String(deleted.purge_after)),
    );
    expect(
      (await documents.documentList({ space_id: "a", lifecycle: "all" })).items,
    ).toEqual([]);
    expect(await lifecycle.trashList({ state: "purged" })).toMatchObject({
      groups: [expect.objectContaining({ state: "purged" })],
    });
  });

  it("checks every D1 guard before any write and keeps capability support separate from authority", async () => {
    const { principal } = await fixture();
    await expect(
      guardedBatch(
        runtime.STATE_DB,
        [guard(runtime.STATE_DB, "1=1"), guard(runtime.STATE_DB, "1=0")],
        [
          runtime.STATE_DB.prepare(
            "UPDATE corpus_contexts SET title='incorrect' WHERE owner_id=?",
          ).bind(principal.ownerId),
        ],
      ),
    ).rejects.toMatchObject({ code: "management_conflict" });
    const saved = await runtime.STATE_DB.prepare(
      "SELECT title FROM corpus_contexts WHERE owner_id=? AND space_id='a'",
    )
      .bind(principal.ownerId)
      .first();
    expect(saved).toMatchObject({ title: "A" });
    const readOnly = { ...principal, scopes: new Set(["corpus.read"]) };
    const caps = await executeContextOperation(
      runtime,
      readOnly,
      "corpus_capabilities",
      {},
    );
    expect(caps).toMatchObject({
      operations: {
        corpus_document_move: { status: "available", authorized: false },
        corpus_trash_list: { authorized: true },
      },
    });
    await expect(
      executeContextOperation(runtime, readOnly, "corpus_document_move", {}),
    ).rejects.toMatchObject({ code: "insufficient_scope" });
    for (const operation of Object.values(
      contextOperations(runtime, principal),
    ))
      expect(operation.outputSchema).toBeDefined();
  });
});
