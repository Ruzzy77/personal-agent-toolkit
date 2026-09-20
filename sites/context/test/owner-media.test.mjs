import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "next/navigation") return {
      url: "data:text/javascript,export function redirect(){throw new Error('redirect')}",
      shortCircuit: true,
    };
    if (specifier === "./owner-session") return {
      url: "data:text/javascript," + encodeURIComponent(`
        export class OwnerSessionError extends Error {
          constructor(status, message) { super(message); this.status = status; }
        }
        export const ownerSessionFromCookies = async () => globalThis.__mediaOwner;
        export const ownerSession = ownerSessionFromCookies;
        export const ownerAccessToken = async () => "private-owner-token";
        export const csrfMatches = () => false;
      `),
      shortCircuit: true,
    };
    return nextResolve(specifier, context);
  },
});
process.env.CONTEXT_SERVICE_URL = "https://context.test";
const { ownerServiceFetch } = await import("../lib/owner-service.ts");

test("Library media uses the owner bridge without exposing credentials or cacheable responses", async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.__mediaOwner = { ownerId: "owner" };
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response(init.method === "HEAD" ? null : "cover", {
      headers: { "Content-Type": "image/jpeg", ETag: '"cover"', "Cache-Control": "public", "Set-Cookie": "upstream=private" },
    });
  };
  try {
    for (const method of ["GET", "HEAD"]) {
      const response = await ownerServiceFetch("/library/media/daily/cover.jpg", { method });
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("Cache-Control"), "private, no-store");
      assert.equal(response.headers.get("Referrer-Policy"), "no-referrer");
      assert.equal(response.headers.get("Set-Cookie"), null);
      assert.equal(response.headers.get("Authorization"), null);
      assert.equal(response.headers.get("ETag"), '"cover"');
      assert.equal(await response.text(), method === "HEAD" ? "" : "cover");
      const call = calls.at(-1);
      assert.equal(call.url, "https://context.test/web/library/media/daily/cover.jpg");
      assert.equal(call.init.headers.get("Authorization"), "Bearer private-owner-token");
      assert.equal(call.init.redirect, "manual");
    }
    for (const method of ["POST", "PUT", "DELETE"]) {
      await assert.rejects(ownerServiceFetch("/library/media/daily/cover.jpg", { method }), error => error.status === 403);
    }
    for (const path of ["/library/private/key", "//outside.test/library/media/key.jpg", "/design/media/key.jpg"]) {
      await assert.rejects(ownerServiceFetch(path), error => error.status === 403);
    }
    globalThis.__mediaOwner = null;
    await assert.rejects(ownerServiceFetch("/library/media/daily/cover.jpg"), error => error.status === 401);
    assert.equal(calls.length, 2);
  } finally {
    globalThis.fetch = original;
    delete globalThis.__mediaOwner;
  }
});
