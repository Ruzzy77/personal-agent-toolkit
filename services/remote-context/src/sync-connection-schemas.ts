import { z } from "zod/v4";
import { corpusMetadataImportSchema } from "./schemas";

// Keep the existing Sync metadata row contract, but never accept Context,
// Space, Current File, or device-registry replacement in this narrow route.
export const syncConnectionsUpsertSchema = z.object({
  connections: z.array(corpusMetadataImportSchema.shape.connections.element).min(1).max(20),
  expected_generations: z.record(
    z.string().min(3).max(129),
    z.union([z.literal("absent"), z.number().int().min(1).max(Number.MAX_SAFE_INTEGER - 1)]),
  ),
}).strict();
