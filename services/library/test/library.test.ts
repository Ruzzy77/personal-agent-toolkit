import { SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";

const ORIGIN = "https://library.example";
const SITE_HEADERS = {
  Authorization: "Bearer test-site-token",
};

function sourceHtml(title: string): string {
  return `<!doctype html><html lang="ko"><head><title>${title}</title></head><body><header><h1>${title}</h1><p class="lead">도입문</p></header><article><p>본문</p></article></body></html>`;
}

async function body(response: Response): Promise<Record<string, unknown>> {
  return response.json<Record<string, unknown>>();
}

describe("Library service-owned storage", () => {
  it("shares one versioned document and media store with the Site API", async () => {
    const id = "daily:2026-09-03:08";
    const createdResponse = await SELF.fetch(`${ORIGIN}/api/v1/issues`, {
      method: "POST",
      headers: { ...SITE_HEADERS, "Content-Type": "application/json" },
      body: JSON.stringify({
        id,
        published_at: "2026-09-03T08:00:00+09:00",
        source_html: sourceHtml("서비스 정본"),
        references: ["공개 자료"],
      }),
    });
    expect(createdResponse.status, await createdResponse.clone().text()).toBe(201);
    const created = (await body(createdResponse)).result as {
      issue: { canonicalPath: string; version: number };
    };
    expect(created.issue.version).toBe(1);

    const updatedResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/issues/${encodeURIComponent(id)}`,
      {
        method: "PUT",
        headers: { ...SITE_HEADERS, "Content-Type": "application/json" },
        body: JSON.stringify({
          source_html: sourceHtml("충돌 없는 편집"),
          expected_version: 1,
        }),
      },
    );
    expect(updatedResponse.status).toBe(200);
    const updated = (await body(updatedResponse)).result as {
      issue: { title: string; version: number };
    };
    expect(updated.issue).toMatchObject({ title: "충돌 없는 편집", version: 2 });

    const staleResponse = await SELF.fetch(
      `${ORIGIN}/api/v1/issues/${encodeURIComponent(id)}`,
      {
        method: "PUT",
        headers: { ...SITE_HEADERS, "Content-Type": "application/json" },
        body: JSON.stringify({
          source_html: sourceHtml("오래된 편집"),
          expected_version: 1,
        }),
      },
    );
    expect(staleResponse.status).toBe(409);
    expect(await body(staleResponse)).toMatchObject({
      ok: false,
      error: { code: "version_conflict" },
    });

    const byPath = await SELF.fetch(
      `${ORIGIN}/api/v1/issues/by-path?path=${encodeURIComponent(created.issue.canonicalPath)}`,
      { headers: SITE_HEADERS },
    );
    expect(byPath.status).toBe(200);
    expect((await body(byPath)).result).toMatchObject({
      id,
      title: "충돌 없는 편집",
      version: 2,
    });

    const image = new Uint8Array([137, 80, 78, 71]);
    const stored = await SELF.fetch(
      `${ORIGIN}/api/v1/assets/issues/cover.png`,
      {
        method: "PUT",
        headers: { ...SITE_HEADERS, "Content-Type": "image/png" },
        body: image,
      },
    );
    expect(stored.status).toBe(200);
    const loaded = await SELF.fetch(`${ORIGIN}/media/issues/cover.png`, {
      headers: SITE_HEADERS,
    });
    expect(new Uint8Array(await loaded.arrayBuffer())).toEqual(image);
  });

  it("imports a legacy issue without rewriting its source", async () => {
    const legacySource = sourceHtml("기존 원문").replace(
      "</head>",
      "<style>article { max-width: 40rem; }</style></head>",
    );
    const response = await SELF.fetch(`${ORIGIN}/api/v1/import/issues`, {
      method: "POST",
      headers: { ...SITE_HEADERS, "Content-Type": "application/json" },
      body: JSON.stringify({
        id: "daily:2026-08-01",
        collection: "daily",
        date: "2026-08-01",
        published_at: "2026-08-01T08:00:00+09:00",
        title: "기존 원문",
        references: [],
        canonical_path: "/editions/daily/issues/2026-08-01",
        text: "기존 원문\n\n도입문\n\n본문",
        source_html: legacySource,
        cover_path: "/media/library/daily/2026-08-01/cover.webp",
        updated_at: "2026-08-01T00:00:00.000Z",
      }),
    });
    expect(response.status, await response.clone().text()).toBe(201);
    expect((await body(response)).result).toMatchObject({
      status: "created",
      issue: { sourceHtml: legacySource, version: 1 },
    });
  });
});
