import { afterEach, describe, expect, it, vi } from "vitest";

import { SitesLibraryClient, SitesLibraryError } from "../src/sites-client";
import type { Env } from "../src/types";

const env = {
  SITES_ORIGIN: "https://site.example",
  SITES_BYPASS_TOKEN: "sites-token",
  LIBRARY_BRIDGE_SECRET: "bridge-secret",
} as Env;

afterEach(() => vi.restoreAllMocks());

describe("Sites Library gateway", () => {
  it("uses both private-Sites and bridge authorization", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ issues: [] }));
    const client = new SitesLibraryClient(env);
    await client.listIssues("digest", 12);

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("https://site.example/api/library/issues?limit=12&collection=digest");
    const headers = new Headers(init?.headers);
    expect(headers.get("OAI-Sites-Authorization")).toBe("Bearer sites-token");
    expect(headers.get("x-library-bridge-secret")).toBe("bridge-secret");
  });

  it("updates the shared canonical document without a base version", async () => {
    const issue = {
      id: "daily:2026-08-25",
      collection: "daily",
      date: "2026-08-25",
      publishedAt: "2026-08-25T08:00:00+09:00",
      title: "건너온 것",
      references: [],
      canonicalPath: "/editions/daily/issues/2026-08-25",
      text: "본문",
      sourceHtml: "<!doctype html><h1>건너온 것</h1><article>본문</article>",
      coverPath: null,
      updatedAt: "2026-08-25T08:00:00+09:00",
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({
      status: "updated",
      issue,
    }));
    const client = new SitesLibraryClient(env);
    await client.updateIssue(issue.id, { source_html: issue.sourceHtml });

    const [, init] = fetchMock.mock.calls[0];
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({
      source_html: issue.sourceHtml,
    });
  });

  it("forwards an explicit references replacement without inventing a cover change", async () => {
    const issue = {
      id: "digest:2026-08-25",
      collection: "digest",
      date: "2026-08-25",
      publishedAt: "2026-08-25T10:00:00+09:00",
      title: "자료의 범위",
      references: ["paper-a"],
      canonicalPath: "/editions/digest/issues/2026-08-25",
      text: "본문",
      sourceHtml: "<!doctype html><h1>자료의 범위</h1><article>본문</article>",
      coverPath: "/media/digest/cover.jpg",
      updatedAt: "2026-08-25T10:00:00+09:00",
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({
      status: "updated",
      issue,
    }));
    const client = new SitesLibraryClient(env);
    await client.updateIssue(issue.id, {
      source_html: issue.sourceHtml,
      references: ["paper-a", "paper-b"],
    });

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({
      source_html: issue.sourceHtml,
      references: ["paper-a", "paper-b"],
    });
  });

  it("keeps Sites errors as bounded gateway errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(
      { error: "not_found" },
      { status: 404 },
    ));
    const client = new SitesLibraryClient(env);
    await expect(client.updateIssue("daily:2026-08-25", { source_html: "source" }))
      .rejects.toEqual(new SitesLibraryError(404, "not_found"));
  });
});
