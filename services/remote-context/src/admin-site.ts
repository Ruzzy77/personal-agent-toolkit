import { executeOperation } from "@personal-agent/remote-runtime";
import { libraryOperations } from "personal-agent-library-service/operations";
import { LibraryService } from "personal-agent-library-service/service";
import { designOperations } from "personal-agent-design-service/operations";
import { DesignService } from "personal-agent-design-service/service";
import { contextOperations } from "./context-api";
import { authenticateSite, json, readJson } from "./context-site";
import { asContextError, ContextError } from "./errors";
import type { Env } from "./types";

// This surface is deliberately narrower than owner OAuth or the Context editor.
export const ADMIN_OPERATIONS = new Set([
  "sense_operation_status",
  "sense_trash_purge",
  "sense_trash_restore",
  "sense_skill_trash",
  "sense_section_trash",
  "corpus_operation_status",
  "corpus_trash_purge",
  "corpus_trash_restore",
  "corpus_space_trash",
  "corpus_context_skill_trash",
  "corpus_context_item_trash",
  "corpus_document_trash",
  "corpus_connection_detach",
  "corpus_context_item_create",
  "corpus_context_item_move",
  "corpus_document_create",
  "corpus_document_list",
  "corpus_document_move",
  "corpus_management_preview",
  "corpus_registrations_list",
  "corpus_space_create",
  "corpus_space_get",
  "corpus_space_list",
  "corpus_space_revise",
  "corpus_trash_list",
  "corpus_workspace_detach",
  "design_capabilities",
  "design_file_trash",
  "design_list_recipes",
  "design_management_preview",
  "design_operation_status",
  "design_read_recipe",
  "design_recipe_trash",
  "design_trash_list",
  "design_trash_purge",
  "design_trash_restore",
  "library_capabilities",
  "library_issue_trash",
  "library_list_issues",
  "library_management_preview",
  "library_operation_status",
  "library_trash_list",
  "library_trash_purge",
  "library_trash_restore",
  "sense_management_preview",
  "sense_read",
  "sense_section_create",
  "sense_sections_reorder",
  "sense_trash_list",
]);

export async function handleAdminSite(request: Request, env: Env): Promise<Response | null> {
  const path = new URL(request.url).pathname;
  if (!path.startsWith("/admin/")) return null;
  try {
    const route = /^\/admin\/v1\/([a-z][a-z0-9_]{0,95})$/.exec(path);
    if (!route) throw new ContextError("not_found", "Admin route was not found", 404);
    if (request.method !== "POST")
      return json({ ok: false, error: { code: "method_not_allowed", message: "Use POST" } }, 405, {
        Allow: "POST",
      });
    const authenticated = authenticateSite(request, env);
    const principal = {
      ...authenticated,
      clientId: "workspace-management",
      scopes: new Set([
        ...authenticated.scopes,
        "library.read",
        "library.write",
        "design.read",
        "design.write",
      ]),
    };
    const name = route[1]!;
    if (!ADMIN_OPERATIONS.has(name))
      throw new ContextError("operation_not_found", "Admin operation was not found", 404);
    const actor = {
      ownerId: principal.ownerId,
      scopes: principal.scopes,
      clientId: principal.clientId,
      kind: "owner" as const,
    };
    const definitions = name.startsWith("library_")
      ? libraryOperations(
          new LibraryService({
            DB: env.LIBRARY_DB,
            MEDIA: env.LIBRARY_MEDIA,
            MANAGEMENT_WRITE_ENABLED: env.LIBRARY_MANAGEMENT_WRITE_ENABLED,
          }),
          actor,
        )
      : name.startsWith("design_")
        ? designOperations(
            new DesignService({
              DB: env.DESIGN_DB,
              ASSETS: env.DESIGN_ASSETS,
              MANAGEMENT_WRITE_ENABLED: env.DESIGN_MANAGEMENT_WRITE_ENABLED,
            }),
            actor,
          )
        : contextOperations(env, principal);
    const definition = Object.hasOwn(definitions, name) ? definitions[name] : undefined;
    if (!definition?.surfaces.includes("http"))
      throw new ContextError("operation_not_found", "Admin operation was not found", 404);
    const result = await executeOperation(actor, definition, await readJson(request));
    return json({ ok: true, result });
  } catch (error) {
    const normalized = asContextError(error);
    return json(
      {
        ok: false,
        error: {
          code: normalized.code,
          message: normalized.message,
          ...(Object.keys(normalized.details).length ? { details: normalized.details } : {}),
        },
      },
      normalized.status,
    );
  }
}
