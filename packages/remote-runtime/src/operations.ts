/** Transport-neutral contracts. Product services own validation and transactions. */
export type OperationActor = {
  ownerId: string;
  scopes: ReadonlySet<string>;
  kind: "owner" | "automation" | "maintenance";
  clientId: string;
};

export class OperationError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status = 400,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "OperationError";
  }
}

export type SupportStatus =
  | "available"
  | "not_implemented"
  | "not_applicable"
  | "policy_restricted";
export type OperationEffects =
  | "read"
  | "revise"
  | "delete"
  | "move"
  | "trash"
  | "restore"
  | "purge";
export type OperationSchema<T = unknown> = { parse(value: unknown): T };
export type OperationDefinition<S extends OperationSchema = OperationSchema> = {
  schema: S;
  outputSchema: OperationSchema;
  scope: string;
  description: string;
  effects: OperationEffects;
  retry: "read" | "version" | "request_key";
  surfaces: readonly ("mcp" | "http")[];
  readOnly: boolean;
  enabled?: boolean;
  run: (input: unknown) => Promise<Record<string, unknown>>;
};

export function defineOperation<S extends OperationSchema>(
  definition: Omit<OperationDefinition<S>, "readOnly">,
): OperationDefinition<S> {
  if (definition.effects === "read" && definition.retry !== "read")
    throw new Error("Read operations must be replay safe");
  return { ...definition, readOnly: definition.effects === "read" };
}

export function requireAnyOperationScope(
  actor: OperationActor,
  scopes: readonly string[],
) {
  if (
    actor.kind === "maintenance" ||
    !scopes.some((scope) => actor.scopes.has(scope))
  )
    throw new OperationError(
      "insufficient_scope",
      "The connection does not grant this operation",
      403,
    );
}

export async function executeOperation(
  actor: OperationActor,
  operation: OperationDefinition,
  input: unknown,
): Promise<Record<string, unknown>> {
  // Maintenance is not a broad product write grant. Its bounded due-trash
  // executor is called by the scheduler, never this ordinary operation path.
  if (actor.kind === "maintenance" || !actor.scopes.has(operation.scope)) {
    throw new OperationError(
      "insufficient_scope",
      "The connection does not grant this operation",
      403,
    );
  }
  if (operation.effects === "purge" && actor.kind !== "owner")
    throw new OperationError(
      "owner_confirmation_required",
      "Only the owner can confirm permanent deletion",
      403,
    );
  if (operation.enabled === false)
    throw new OperationError(
      "operation_not_enabled",
      "This operation has not been activated",
      503,
    );
  const result = await operation.run(operation.schema.parse(input));
  operation.outputSchema.parse(result);
  return result;
}

export function operationCapabilities(
  actor: OperationActor,
  operations: Record<string, OperationDefinition>,
  unavailable: Record<
    string,
    { status: Exclude<SupportStatus, "available">; reason: string }
  > = {},
) {
  return Object.fromEntries([
    ...Object.entries(operations).map(([name, operation]) => [
      name,
      {
        status: "available",
        enabled: operation.enabled !== false,
        authorized:
          actor.kind !== "maintenance" &&
          actor.scopes.has(operation.scope) &&
          (operation.effects !== "purge" || actor.kind === "owner"),
        required_scope: operation.scope,
        effects: operation.effects,
        retry: operation.retry,
        surfaces: operation.surfaces,
      },
    ]),
    ...Object.entries(unavailable),
  ]);
}

export function operationAnnotations(operation: OperationDefinition) {
  return {
    readOnlyHint: operation.readOnly,
    destructiveHint: ["delete", "move", "trash", "purge"].includes(
      operation.effects,
    ),
    idempotentHint:
      operation.retry === "read" || operation.retry === "request_key",
  };
}

export const TRASH_RETENTION_MS = 30 * 24 * 60 * 60 * 1000;
export type TrashState =
  | "trashed"
  | "blocked"
  | "purging"
  | "purged"
  | "restored";
export type PurgeBlocker = { code: string; count?: number; message: string };
export function purgeAfter(trashedAt: string): string {
  return new Date(Date.parse(trashedAt) + TRASH_RETENTION_MS).toISOString();
}
