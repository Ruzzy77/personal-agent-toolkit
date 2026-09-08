import { z } from "zod/v4";
import { CorpusService } from "./corpus";
import { CorpusDocumentsService } from "./corpus-documents";
import * as native from "./corpus-document-schemas";
import * as schemas from "./schemas";
import { SenseService } from "./sense";
import { ContextError } from "./errors";
import type { Env, Principal } from "./types";

type Operation = {
  schema: z.ZodObject;
  scope: "sense.read" | "sense.write" | "corpus.read" | "corpus.write";
  readOnly: boolean;
  description: string;
  run: (raw: unknown) => Promise<Record<string, unknown>>;
};

export const NATIVE_CORPUS_TOOLS = [
  "corpus_space_create", "corpus_workspace_bind", "corpus_workspace_resolve",
  "corpus_document_create", "corpus_document_list", "corpus_document_read",
  "corpus_document_revise", "corpus_document_restore",
] as const;

export function contextOperations(env: Env, principal: Principal): Record<string, Operation> {
  const sense = new SenseService(env.STATE_DB, principal.ownerId);
  const corpus = new CorpusService(env, principal);
  const docs = new CorpusDocumentsService(env, principal);
  const operation = (schema: z.ZodObject, scope: Operation["scope"], description: string,
    run: Operation["run"]): Operation => ({ schema, scope, description, run, readOnly: scope.endsWith(".read") });
  return {
    sense_read: operation(schemas.senseReadSchema, "sense.read", "Read the index or selected criteria; include_skill chooses connected method text.", async raw => {
      const input = schemas.senseReadSchema.parse(raw);
      return sense.read(input.view, input.section_ids, input.include_skill);
    }),
    sense_revise: operation(schemas.senseReviseSchema, "sense.write", "Revise selected ordinary sections with their current hashes.", raw => sense.revise(raw)),
    sense_skill_revise: operation(schemas.senseSkillReviseSchema, "sense.write", "Revise one approved Section Skill with its current version.", raw => sense.reviseSkill(raw)),
    corpus_space_list: operation(schemas.corpusSpaceListSchema, "corpus.read", "List registered projects.", raw => corpus.spaceList(raw)),
    corpus_space_get: operation(schemas.corpusSpaceGetSchema, "corpus.read", "Read project context and document index; select whether its Skill is included.", raw => corpus.spaceGet(raw)),
    corpus_space_search: operation(schemas.corpusSpaceSearchSchema, "corpus.read", "Find Source or Context candidates without loading every document.", raw => corpus.spaceSearch(raw)),
    corpus_context_items_revise: operation(schemas.corpusContextItemsReviseSchema, "corpus.write", "Revise project Context items with their current version.", raw => corpus.reviseContextItems(raw)),
    corpus_context_skill_revise: operation(schemas.corpusContextSkillReviseSchema, "corpus.write", "Revise the approved project Skill with its current version.", raw => corpus.reviseContextSkill(raw)),
    corpus_file_read: operation(schemas.corpusFileReadSchema, "corpus.read", "Read a registered Source or Work file without modifying the original.", raw => corpus.fileRead(raw)),
    corpus_space_create: operation(native.corpusSpaceCreateSchema, "corpus.write", "Create an explicitly scoped project without a local Source file.", raw => docs.spaceCreate(raw)),
    corpus_workspace_bind: operation(native.corpusWorkspaceBindSchema, "corpus.write", "Bind an explicit host Workspace identity to a project. Local paths and filesystem authority stay with the host.", raw => docs.workspaceBind(raw)),
    corpus_workspace_resolve: operation(native.corpusWorkspaceResolveSchema, "corpus.read", "Resolve a registered Workspace identity; do not guess a permanent binding from folder names.", raw => docs.workspaceResolve(raw)),
    corpus_document_create: operation(native.corpusDocumentCreateSchema, "corpus.write", "Create a complete native Context or explicitly approved guidance document; preserve Markdown and exact provenance.", raw => docs.documentCreate(raw)),
    corpus_document_list: operation(native.corpusDocumentListSchema, "corpus.read", "List native document identity, scope and version without full bodies.", raw => docs.documentList(raw)),
    corpus_document_read: operation(native.corpusDocumentReadSchema, "corpus.read", "Read current or previous Markdown, optionally in version-pinned Unicode ranges.", raw => docs.documentRead(raw)),
    corpus_document_revise: operation(native.corpusDocumentReviseSchema, "corpus.write", "Save a complete canonical document against its current version; retain only its previous saved state.", raw => docs.documentRevise(raw)),
    corpus_document_restore: operation(native.corpusDocumentRestoreSchema, "corpus.write", "Restore the previous document as a new version after comparing the current version.", raw => docs.documentRestore(raw)),
  };
}

export async function executeContextOperation(env: Env, principal: Principal, name: string, raw: unknown): Promise<Record<string, unknown>> {
  const operations = contextOperations(env, principal);
  if (!Object.hasOwn(operations, name)) throw new ContextError("operation_not_found", "Context operation does not exist", 404);
  const operation = operations[name]!;
  if (!principal.scopes.has(operation.scope)) throw new ContextError("insufficient_scope", "The connection does not grant this operation", 403);
  return operation.run(operation.schema.parse(raw));
}
