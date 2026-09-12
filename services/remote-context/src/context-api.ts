import {
  RegistrationManagementService,
  registrationsListSchema,
  connectionDetachSchema,
  workspaceDetachSchema,
} from "./registration-management";
import { SenseManagementService } from "./sense-management";
import * as senseManagement from "./sense-management";
import {
  defineOperation,
  executeOperation,
  operationCapabilities,
  type OperationDefinition,
  type OperationEffects,
} from "@personal-agent/remote-runtime";
import { CorpusManagementService } from "./corpus-management";
import * as management from "./corpus-management-schemas";
import { HypesService } from "./hypes";
import { z } from "zod/v4";
import { CorpusService } from "./corpus";
import { CorpusDocumentsService } from "./corpus-documents";
import * as native from "./corpus-document-schemas";
import * as schemas from "./schemas";
import { SenseService } from "./sense";
import { ContextError } from "./errors";
import type { Env, Principal } from "./types";

type Operation = OperationDefinition<z.ZodObject> & { mcpOutput: z.ZodObject };

export function contextOperations(
  env: Env,
  principal: Principal,
): Record<string, Operation> {
  const registrations = new RegistrationManagementService(env, principal);
  const sense = new SenseService(env.STATE_DB, principal.ownerId);
  const corpus = new CorpusService(env, principal);
  const docs = new CorpusDocumentsService(env, principal);
  const lifecycle = new CorpusManagementService(env, principal);
  const hypes = new HypesService(env.STATE_DB, principal.ownerId);
  const senseLifecycle = new SenseManagementService(env, principal);
  const operation = (
    schema: z.ZodObject,
    scope: Operation["scope"],
    description: string,
    run: Operation["run"],
    effects: OperationEffects = scope.endsWith(".read") ? "read" : "revise",
  ): Operation => ({
    ...defineOperation({
      schema,
      outputSchema: z.record(z.string(), z.unknown()),
      scope,
      description,
      run,
      effects,
      retry:
        effects === "read"
          ? "read"
          : ["move", "trash", "purge"].includes(effects)
            ? "request_key"
            : "version",
      surfaces: ["mcp", "http"],
    }),
    mcpOutput: schemas.contextToolOutputSchema,
  });
  const operations: Record<string, Operation> = {
    sense_read: operation(
      schemas.senseReadSchema,
      "sense.read",
      "Read the index or selected criteria; include_skill chooses connected method text.",
      async (raw) => {
        const input = schemas.senseReadSchema.parse(raw);
        return sense.read(input.view, input.section_ids, input.include_skill);
      },
    ),
    sense_overview: operation(
      z.object({}).strict(),
      "sense.read",
      "Show ordinary Sense guidance and its connected Skills.",
      () => sense.overview(env.CONTEXT_SITE_URL),
    ),
    hypes_read: operation(
      schemas.hypesReadSchema,
      "hypes.read",
      "Read the relationship model and its graph version.",
      (raw) => hypes.read(raw),
    ),
    hypes_rewrite: operation(
      schemas.hypesRewriteSchema,
      "hypes.write",
      "Apply the existing versioned graph patch, including explicit deletes and their edge cascade.",
      (raw) => hypes.rewrite(raw),
      "delete",
    ),
    sense_revise: operation(
      schemas.senseReviseSchema,
      "sense.write",
      "Revise selected ordinary sections with their current hashes.",
      (raw) => sense.revise(raw),
    ),
    sense_skill_revise: operation(
      schemas.senseSkillReviseSchema,
      "sense.write",
      "Revise one approved Section Skill with its current version.",
      (raw) => sense.reviseSkill(raw),
    ),
    sense_section_create: operation(
      senseManagement.senseSectionCreateSchema,
      "sense.write",
      "Create an ordinary section using the profile token and affected Skill version.",
      (raw) => senseLifecycle.create(raw),
      "revise",
    ),
    sense_sections_reorder: operation(
      senseManagement.senseSectionsReorderSchema,
      "sense.write",
      "Reorder every ordinary section; sensitive sections remain in their existing slots.",
      (raw) => senseLifecycle.reorder(raw),
      "revise",
    ),
    sense_management_preview: operation(
      senseManagement.senseManagementPreviewSchema,
      "sense.read",
      "Review ordinary section or Skill lifecycle changes without exposing sensitive text.",
      (raw) => senseLifecycle.preview(raw),
      "read",
    ),
    sense_section_trash: operation(
      senseManagement.senseSectionTrashSchema,
      "sense.write",
      "Trash an ordinary section with its currently owned Skill for 30 days.",
      (raw) => senseLifecycle.trash("section", raw),
      "trash",
    ),
    sense_skill_trash: operation(
      senseManagement.senseSectionTrashSchema,
      "sense.write",
      "Remove an ordinary Section Skill to its own 30-day deletion group.",
      (raw) => senseLifecycle.trash("skill", raw),
      "trash",
    ),
    sense_trash_list: operation(
      senseManagement.senseTrashListSchema,
      "sense.read",
      "List ordinary deletion groups and their due dates and blockers.",
      (raw) => senseLifecycle.trashList(raw),
      "read",
    ),
    sense_trash_restore: operation(
      senseManagement.senseTrashRestoreSchema,
      "sense.write",
      "Restore only the reviewed group against the current profile and Skill versions.",
      (raw) => senseLifecycle.restore(raw),
      "restore",
    ),
    sense_trash_purge: operation(
      senseManagement.senseTrashPurgeSchema,
      "sense.write",
      "Permanently delete a reviewed ordinary group on explicit owner confirmation.",
      (raw) => senseLifecycle.purge(raw),
      "purge",
    ),
    sense_operation_status: operation(
      senseManagement.senseOperationStatusSchema,
      "sense.read",
      "Read a management commit receipt without changing personal guidance.",
      (raw) => senseLifecycle.status(raw),
      "read",
    ),
    corpus_registrations_list: operation(
      registrationsListSchema,
      "corpus.read",
      "Read registration generations and owner Sync detach progress without local paths.",
      (raw) => registrations.list(raw),
    ),
    corpus_connection_detach: operation(
      connectionDetachSchema,
      "corpus.write",
      "Request generation-checked retirement through the owner Sync. Offline or uncertain execution remains pending; Source bytes are retained.",
      (raw) => registrations.detach("connection", raw),
      "delete",
    ),
    corpus_workspace_detach: operation(
      workspaceDetachSchema,
      "corpus.write",
      "Detach an exact host Workspace binding only after its registered Sync acknowledges durable local retirement.",
      (raw) => registrations.detach("workspace", raw),
      "delete",
    ),
    corpus_space_list: operation(
      schemas.corpusSpaceListSchema,
      "corpus.read",
      "List registered projects.",
      (raw) => corpus.spaceList(raw),
    ),
    corpus_space_get: operation(
      schemas.corpusSpaceGetSchema,
      "corpus.read",
      "Read project context and document index; select whether its Skill is included.",
      (raw) => corpus.spaceGet(raw),
    ),
    corpus_space_search: operation(
      schemas.corpusSpaceSearchSchema,
      "corpus.read",
      "Find Source or Context candidates without loading every document.",
      (raw) => corpus.spaceSearch(raw),
    ),
    corpus_context_items_revise: operation(
      schemas.corpusContextItemsReviseSchema,
      "corpus.write",
      "Revise project Context items with their current version.",
      (raw) => corpus.reviseContextItems(raw),
    ),
    corpus_context_skill_revise: operation(
      schemas.corpusContextSkillReviseSchema,
      "corpus.write",
      "Revise the approved project Skill with its current version.",
      (raw) => corpus.reviseContextSkill(raw),
    ),
    corpus_file_read: operation(
      schemas.corpusFileReadSchema,
      "corpus.read",
      "Read a registered Source or Work file without modifying the original.",
      (raw) => corpus.fileRead(raw),
    ),
    corpus_space_create: operation(
      native.corpusSpaceCreateSchema,
      "corpus.write",
      "Create an explicitly scoped project without a local Source file.",
      (raw) => docs.spaceCreate(raw),
    ),
    corpus_workspace_bind: operation(
      native.corpusWorkspaceBindSchema,
      "corpus.write",
      "Bind an explicit host Workspace identity to a project. Local paths and filesystem authority stay with the host.",
      (raw) => docs.workspaceBind(raw),
    ),
    corpus_workspace_resolve: operation(
      native.corpusWorkspaceResolveSchema,
      "corpus.read",
      "Resolve a registered Workspace identity; do not guess a permanent binding from folder names.",
      (raw) => docs.workspaceResolve(raw),
    ),
    corpus_document_create: operation(
      native.corpusDocumentCreateSchema,
      "corpus.write",
      "Create a complete native Context or explicitly approved guidance document; preserve Markdown and exact provenance.",
      (raw) => docs.documentCreate(raw),
    ),
    corpus_document_list: operation(
      native.corpusDocumentListSchema,
      "corpus.read",
      "List native document identity, scope and version without full bodies.",
      (raw) => docs.documentList(raw),
    ),
    corpus_document_read: operation(
      native.corpusDocumentReadSchema,
      "corpus.read",
      "Read current or previous Markdown, optionally in version-pinned Unicode ranges.",
      (raw) => docs.documentRead(raw),
    ),
    corpus_document_revise: operation(
      native.corpusDocumentReviseSchema,
      "corpus.write",
      "Save a complete canonical document against its current version; retain only its previous saved state.",
      (raw) => docs.documentRevise(raw),
    ),
    corpus_document_restore: operation(
      native.corpusDocumentRestoreSchema,
      "corpus.write",
      "Restore the previous document as a new version after comparing the current version.",
      (raw) => docs.documentRestore(raw),
    ),
    corpus_management_preview: operation(
      management.corpusManagementPreviewSchema,
      "corpus.read",
      "Review the exact members, versions, restore destination and deletion blockers.",
      (raw) => lifecycle.preview(raw),
      "read",
    ),
    corpus_document_move: operation(
      management.corpusDocumentMoveSchema,
      "corpus.write",
      "Move a native document preserving its identity, snapshots and Source origins. Old locators become read-only aliases.",
      (raw) => lifecycle.documentMove(raw),
      "move",
    ),
    corpus_context_item_move: operation(
      management.corpusContextItemMoveSchema,
      "corpus.write",
      "Move a Context item without copying its body or moving its Source registration.",
      (raw) => lifecycle.itemMove(raw),
      "move",
    ),
    corpus_trash_list: operation(
      management.corpusTrashListSchema,
      "corpus.read",
      "List deletion groups, due times, members, blockers and maintenance status.",
      (raw) => lifecycle.trashList(raw),
      "read",
    ),
    corpus_trash_restore: operation(
      management.corpusTrashRestoreSchema,
      "corpus.write",
      "Restore only members of the reviewed deletion group, atomically.",
      (raw) => lifecycle.restore(raw),
      "restore",
    ),
    corpus_trash_purge: operation(
      management.corpusTrashPurgeSchema,
      "corpus.write",
      "Permanently delete a reviewed group only on an explicit owner request. External references block deletion.",
      (raw) => lifecycle.purge(raw),
      "purge",
    ),
    corpus_operation_status: operation(
      management.corpusOperationStatusSchema,
      "corpus.read",
      "Look up a committed management request receipt; absence does not prove failure.",
      (raw) => lifecycle.operationStatus(raw),
      "read",
    ),
    corpus_space_revise: operation(
      management.corpusSpaceReviseSchema,
      "corpus.write",
      "Revise Space metadata or archive it with its current Context version.",
      (raw) => lifecycle.spaceRevise(raw),
      "revise",
    ),
    corpus_context_item_create: operation(
      management.corpusContextItemCreateSchema,
      "corpus.write",
      "Create a Context item against its current parent version.",
      (raw) => lifecycle.itemCreate(raw),
      "revise",
    ),
    corpus_document_trash: operation(
      management.corpusDocumentTrashSchema,
      "corpus.write",
      "Trash the reviewed owned material as one group for 30 days. Original files and registrations are excluded.",
      (raw) => lifecycle.trash("document", raw),
      "trash",
    ),
    corpus_context_item_trash: operation(
      management.corpusContextItemTrashSchema,
      "corpus.write",
      "Trash the reviewed owned material as one group for 30 days. Original files and registrations are excluded.",
      (raw) => lifecycle.trash("context_item", raw),
      "trash",
    ),
    corpus_context_skill_trash: operation(
      management.corpusContextSkillTrashSchema,
      "corpus.write",
      "Trash the reviewed owned material as one group for 30 days. Original files and registrations are excluded.",
      (raw) => lifecycle.trash("context_skill", raw),
      "trash",
    ),
    corpus_space_trash: operation(
      management.corpusSpaceTrashSchema,
      "corpus.write",
      "Trash the reviewed owned material as one group for 30 days. Original files and registrations are excluded.",
      (raw) => lifecycle.trash("space", raw),
      "trash",
    ),
    corpus_file_list: operation(
      schemas.corpusFileListSchema,
      "corpus.read",
      "Use the existing locally authorized Sync operation and its version checks.",
      (raw) => corpus.fileList(raw),
    ),
    corpus_source_refresh: operation(
      schemas.corpusSourceRefreshSchema,
      "corpus.write",
      "Use the existing locally authorized Sync operation and its version checks.",
      (raw) => corpus.sourceRefresh(raw),
    ),
    corpus_job_status: operation(
      schemas.corpusJobStatusSchema,
      "corpus.read",
      "Use the existing locally authorized Sync operation and its version checks.",
      (raw) => corpus.jobStatus(raw),
    ),
    corpus_file_write: operation(
      schemas.corpusFileWriteSchema,
      "corpus.write",
      "Use the existing locally authorized Sync operation and its version checks.",
      (raw) => corpus.fileWrite(raw),
    ),
    corpus_file_delete: operation(
      schemas.corpusFileDeleteSchema,
      "corpus.write",
      "Use the existing locally authorized Sync operation and its version checks.",
      (raw) => corpus.fileDelete(raw),
      "delete",
    ),
    corpus_file_select_current: operation(
      schemas.corpusFileSelectSchema,
      "corpus.write",
      "Use the existing locally authorized Sync operation and its version checks.",
      (raw) => corpus.fileSelectCurrent(raw),
    ),
    corpus_file_restore: operation(
      schemas.corpusFileRestoreSchema,
      "corpus.write",
      "Use the existing locally authorized Sync operation and its version checks.",
      (raw) => corpus.fileRestore(raw),
    ),
  };
  for (const [name, definition] of Object.entries(operations)) {
    if (name.endsWith("_detach") || definition.effects === "restore")
      definition.retry = "request_key";
    if (
      name.startsWith("sense_") &&
      /section_create|sections_reorder|_trash/.test(name)
    )
      definition.enabled =
        env.SENSE_MANAGEMENT_WRITE_ENABLED === "true" || definition.readOnly;
    if (
      name.startsWith("corpus_") &&
      /_move$|_detach$|_trash|space_revise|item_create/.test(name)
    )
      definition.enabled =
        env.CORPUS_MANAGEMENT_WRITE_ENABLED === "true" || definition.readOnly;
  }
  for (const product of ["sense", "corpus", "hypes"]) {
    operations[`${product}_capabilities`] = operation(
      z.object({}).strict(),
      `${product}.read`,
      "Read support status separately from this caller's permissions.",
      async () => ({
        operations: operationCapabilities(
          {
            ownerId: principal.ownerId,
            scopes: principal.scopes,
            kind: "owner",
            clientId: principal.clientId,
          },
          Object.fromEntries(
            Object.entries(operations).filter(([name]) =>
              name.startsWith(`${product}_`),
            ),
          ),
          product === "hypes"
            ? {
                hypes_trash: {
                  status: "not_applicable",
                  reason: "Hypes keeps its graph delete/cascade contract",
                },
              }
            : product === "sense"
              ? {
                  sense_sensitive_revise: {
                    status: "policy_restricted",
                    reason:
                      "Sensitive modification remains outside the remote surface",
                  },
                }
              : {},
        ),
      }),
    );
  }
  return operations;
}

export async function executeContextOperation(
  env: Env,
  principal: Principal,
  name: string,
  raw: unknown,
): Promise<Record<string, unknown>> {
  const operations = contextOperations(env, principal);
  if (!Object.hasOwn(operations, name))
    throw new ContextError(
      "operation_not_found",
      "Context operation does not exist",
      404,
    );
  const operation = operations[name]!;
  return executeOperation(
    {
      ownerId: principal.ownerId,
      scopes: principal.scopes,
      kind: "owner",
      clientId: principal.clientId,
    },
    operation,
    raw,
  );
}
