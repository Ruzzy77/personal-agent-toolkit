import { z } from "zod/v4";

import { contentSha256 } from "./canonical";

export const readEtagSchema = z
  .string()
  .regex(/^read-v1:[0-9a-f]{64}$/)
  .nullable()
  .optional()
  .describe(
    "Omit for the legacy response. Use null to receive a full response with a read_etag, then return that tag only while the exact representation remains available in the current context.",
  );

export async function conditionalRead(
  ownerId: string,
  operation: string,
  selection: unknown,
  ifNoneMatch: string | null | undefined,
  read: () => Promise<Record<string, unknown>>,
): Promise<Record<string, unknown>> {
  const result = await read();
  if (ifNoneMatch === undefined) return result;

  const readEtag = `read-v1:${await contentSha256({
    owner_id: ownerId,
    operation,
    selection,
    result,
  })}`;
  if (ifNoneMatch === readEtag) {
    return { read_etag: readEtag, not_modified: true };
  }
  return { ...result, read_etag: readEtag, not_modified: false };
}
