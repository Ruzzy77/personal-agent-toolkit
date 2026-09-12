export class ImmutableObjectConflict extends Error {
  readonly code = "asset_conflict";
}
export async function sha256(bytes: Uint8Array): Promise<string> {
  return [
    ...new Uint8Array(
      await crypto.subtle.digest("SHA-256", Uint8Array.from(bytes)),
    ),
  ]
    .map((v) => v.toString(16).padStart(2, "0"))
    .join("");
}
/** The MIME is part of object identity, not mutable metadata on a byte hash. */
export async function objectIdentity(bytes: Uint8Array, mime: string) {
  const digest = await sha256(bytes);
  return {
    sha256: digest,
    identity: await sha256(new TextEncoder().encode(`${mime}\n${digest}`)),
  };
}
export async function putImmutable(
  bucket: R2Bucket,
  key: string,
  bytes: Uint8Array,
  mime: string,
): Promise<{ reused: boolean }> {
  const stored = await bucket.put(key, bytes, {
    onlyIf: new Headers({ "If-None-Match": "*" }),
    httpMetadata: { contentType: mime },
  });
  if (stored) return { reused: false };
  const existing = await bucket.get(key);
  if (
    !existing ||
    existing.size !== bytes.byteLength ||
    existing.httpMetadata?.contentType !== mime ||
    !new Uint8Array(await existing.arrayBuffer()).every(
      (value, index) => value === bytes[index],
    )
  ) {
    throw new ImmutableObjectConflict(
      "This object key contains different bytes or MIME; choose a new key",
    );
  }
  return { reused: true };
}
