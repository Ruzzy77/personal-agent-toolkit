import { SELF } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";

const ORIGIN = "https://design.example";
const SITE_HEADERS = {
  Authorization: "Bearer test-site-token",
  "Content-Type": "application/json",
};

async function body(response: Response): Promise<Record<string, unknown>> {
  return response.json<Record<string, unknown>>();
}

const recipe = {
  id: "quiet-grid",
  name: "Quiet Grid",
  description: "A private test recipe",
  version: "1.0.0",
  status: "validated",
  selection_ready: true,
  kind: "recipe",
  visibility: "private",
  pattern_refs: ["calm-hierarchy"],
  formats: ["web"],
  templates: { main: "templates/main.html" },
};

describe("Design private library", () => {
  beforeAll(async () => {
    const imported = await SELF.fetch(`${ORIGIN}/api/v1/import/recipes`, {
      method: "POST",
      headers: SITE_HEADERS,
      body: JSON.stringify({
        library: { id: "personal-design", name: "Private Design" },
        patterns: [{ id: "calm-hierarchy", name: "Calm hierarchy" }],
        recipe,
        files: [
          {
            path: "templates/main.html",
            content_type: "text/html",
            base64: "aGVsbG8=",
            sha256: "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
          },
        ],
      }),
    });
    expect(imported.status, await imported.clone().text()).toBe(201);
  });

  it("serves one D1 catalog and its R2 assets through the authenticated API", async () => {
    const catalog = await SELF.fetch(`${ORIGIN}/api/v1/catalog`, {
      headers: SITE_HEADERS,
    });
    expect((await body(catalog)).result).toMatchObject({
      catalog_schema_version: 2,
      recipes: [{ id: "quiet-grid", visibility: "private" }],
    });

    const asset = await SELF.fetch(
      `${ORIGIN}/api/v1/recipes/quiet-grid/files/templates/main.html`,
      { headers: SITE_HEADERS },
    );
    expect(asset.status).toBe(200);
    expect(await asset.text()).toBe("hello");
    expect(asset.headers.get("etag")).toBe(
      '"2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"',
    );
  });

  it("rejects missing credentials and stale recipe revisions", async () => {
    expect((await SELF.fetch(`${ORIGIN}/api/v1/catalog`)).status).toBe(401);

    const stale = await SELF.fetch(`${ORIGIN}/api/v1/recipes/quiet-grid`, {
      method: "PUT",
      headers: SITE_HEADERS,
      body: JSON.stringify({ expected_revision: 99, recipe }),
    });
    expect(stale.status).toBe(409);
    expect(await body(stale)).toMatchObject({
      ok: false,
      error: { code: "version_conflict" },
    });
  });
});
