import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { access, readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import worker from "../worker/index.js";

class D1Statement {
  constructor(database, sql, values = []) {
    this.database = database;
    this.sql = sql;
    this.values = values;
  }

  bind(...values) {
    return new D1Statement(this.database, this.sql, values);
  }

  async run() {
    const result = this.database.prepare(this.sql).run(...this.values);
    return { success: true, meta: { changes: Number(result.changes ?? 0) } };
  }

  async first() {
    return this.database.prepare(this.sql).get(...this.values) ?? null;
  }

  async all() {
    return { results: this.database.prepare(this.sql).all(...this.values) };
  }
}

class TestD1 {
  constructor() {
    this.database = new DatabaseSync(":memory:");
  }

  prepare(sql) {
    return new D1Statement(this.database, sql);
  }

  async batch(statements) {
    this.database.exec("BEGIN");
    try {
      const results = [];
      for (const statement of statements) results.push(await statement.run());
      this.database.exec("COMMIT");
      return results;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }
}

class TestR2 {
  constructor() {
    this.objects = new Map();
  }

  async put(key, bytes, options = {}) {
    this.objects.set(key, {
      bytes: bytes.slice(0),
      contentType: options.httpMetadata?.contentType ?? "application/octet-stream",
    });
  }

  async get(key) {
    const stored = this.objects.get(key);
    if (!stored) return null;
    return {
      body: stored.bytes.slice(0),
      etag: `"${stored.bytes.byteLength}"`,
      httpEtag: `"${stored.bytes.byteLength}"`,
      writeHttpMetadata(headers) {
        headers.set("content-type", stored.contentType);
      },
    };
  }
}

function makeEnv() {
  return {
    DB: new TestD1(),
    MEDIA: new TestR2(),
    LIBRARY_BRIDGE_SECRET: "bridge-secret",
    ASSETS: {
      fetch: async (request) => {
        const pathname = new URL(request.url).pathname;
        return new Response(pathname === "/index.html" ? "app" : "missing", {
          status: pathname === "/index.html" ? 200 : 404,
        });
      },
    },
  };
}

function ownerHeaders(extra = {}) {
  return {
    "oai-authenticated-user-id": "owner",
    ...extra,
  };
}

function testSource(title, body = "온라인 원본") {
  return `<!doctype html><html lang="ko"><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'"><title>${title}</title></head><body><main><header><h1>${title}</h1><p class="lead">${body} 도입</p></header><article><p>${body}</p></article></main></body></html>`;
}

async function createTestIssue(env, { id, title, body, coverPath, references } = {}) {
  const response = await worker.fetch(
    new Request("https://example.test/api/library/issues", {
      method: "POST",
      headers: ownerHeaders({ "content-type": "application/json" }),
      body: JSON.stringify({
        id,
        published_at: `${id.split(":")[1]}T08:00:00+09:00`,
        source_html: testSource(title, body),
        cover_path: coverPath,
        references,
      }),
    }),
    env,
  );
  assert.equal(response.status, 201);
  return (await response.json()).issue;
}

async function latestMtime(target) {
  const details = await stat(target);
  if (!details.isDirectory()) return details.mtimeMs;
  const children = await readdir(target);
  const times = await Promise.all(children.map((child) => latestMtime(path.join(target, child))));
  return Math.max(details.mtimeMs, ...times);
}

test("serves existing static assets without a fallback", async () => {
  const calls = [];
  const env = makeEnv();
  env.ASSETS.fetch = async (request) => {
    calls.push(new URL(request.url).pathname);
    return new Response("asset", { status: 200 });
  };
  const response = await worker.fetch(new Request("https://example.test/assets/app.js"), env);
  assert.equal(response.status, 200);
  assert.deepEqual(calls, ["/assets/app.js"]);
});

test("falls back to the app shell for an unknown browser route", async () => {
  const calls = [];
  const env = makeEnv();
  env.ASSETS.fetch = async (request) => {
    const url = new URL(request.url);
    calls.push(url.pathname + url.search);
    return new Response(url.pathname === "/index.html" ? "app" : "missing", {
      status: url.pathname === "/index.html" ? 200 : 404,
    });
  };
  const response = await worker.fetch(
    new Request("https://example.test/flow/step-two?source=share", {
      headers: { accept: "text/html" },
    }),
    env,
  );
  assert.equal(response.status, 200);
  assert.deepEqual(calls, ["/flow/step-two?source=share", "/index.html"]);
});

test("starts with an empty online document store", async () => {
  const env = makeEnv();
  const response = await worker.fetch(new Request("https://example.test/api/library/issues?limit=200"), env);
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.deepEqual(body.issues, []);
});

test("returns only documents created in the online store", async () => {
  const env = makeEnv();
  await createTestIssue(env, { id: "digest:2026-08-25", title: "변화의 두 번째 몸" });
  const response = await worker.fetch(new Request("https://example.test/api/library/issues?limit=200"), env);
  const body = await response.json();
  assert.equal(body.issues.length, 1);
  assert.equal(body.issues[0].id, "digest:2026-08-25");
  assert.equal("sourceHtml" in body.issues[0], false);
});

test("serves the canonical issue and adds editing only for the owner", async () => {
  const env = makeEnv();
  await createTestIssue(env, { id: "digest:2026-08-24", title: "뒤늦은 필압" });
  const ownerResponse = await worker.fetch(
    new Request("https://example.test/editions/digest/issues/2026-08-24", {
      headers: ownerHeaders({ accept: "text/html" }),
    }),
    env,
  );
  const ownerHtml = await ownerResponse.text();
  assert.equal(ownerResponse.status, 200);
  assert.match(ownerHtml, /data-library-issue-id="digest:2026-08-24"/);
  assert.match(ownerHtml, /data-library-collection="digest"/);
  assert.match(ownerHtml, /data-library-date="2026-08-24"/);
  assert.match(ownerHtml, /library-editor\.css/);
  assert.match(ownerHtml, /connect-src 'self'/);
  assert.match(ownerHtml, /font-src 'self'/);

  const readerResponse = await worker.fetch(
    new Request("https://example.test/editions/digest/issues/2026-08-24.html", {
      headers: { accept: "text/html" },
    }),
    env,
  );
  const readerHtml = await readerResponse.text();
  assert.equal(readerResponse.status, 200);
  assert.doesNotMatch(readerHtml, /library-editor\.js/);
  assert.match(readerHtml, /뒤늦은 필압/);
  assert.match(readerHtml, /<span class="reader-publication">Research Digest<\/span>/);
  assert.match(readerHtml, /<time class="reader-date" datetime="2026-08-24">24 AUG 2026<\/time>/);
  assert.match(readerHtml, /font-src 'self'/);
});

test("requires owner or bridge authorization for writes", async () => {
  const env = makeEnv();
  const response = await worker.fetch(
    new Request("https://example.test/api/library/issues/digest%3A2026-08-24", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source_html: testSource("권한 없음") }),
    }),
    env,
  );
  assert.equal(response.status, 403);
});

test("saves direct page edits with last-write-wins behavior", async () => {
  const env = makeEnv();
  await createTestIssue(env, { id: "digest:2026-08-24", title: "원래 제목" });
  const endpoint = "https://example.test/api/library/issues/digest%3A2026-08-24";
  for (const title of ["첫 번째 저장", "두 번째 저장"]) {
    const response = await worker.fetch(
      new Request(endpoint, {
        method: "PATCH",
        headers: ownerHeaders({ "content-type": "application/json" }),
        body: JSON.stringify({
          title,
          lead_text: `${title} 도입`,
          article_html: `<p>${title} 본문</p>`,
        }),
      }),
      env,
    );
    assert.equal(response.status, 200);
  }

  const read = await worker.fetch(new Request(endpoint), env);
  const body = await read.json();
  assert.equal(body.issue.title, "두 번째 저장");
  assert.match(body.issue.sourceHtml, /<p class="lead">두 번째 저장 도입<\/p>/);
  assert.match(body.issue.sourceHtml, /두 번째 저장 본문/);
  assert.doesNotMatch(body.issue.sourceHtml, /첫 번째 저장 본문/);
});

test("lets the authenticated MCP bridge replace complete source HTML", async () => {
  const env = makeEnv();
  const issue = await createTestIssue(env, { id: "daily:2026-08-25", title: "건너온 것" });
  const sourceHtml = issue.sourceHtml.replace("</article>", "<p>공유 원본 편집</p></article>");
  const response = await worker.fetch(
    new Request("https://example.test/api/library/issues/daily%3A2026-08-25", {
      method: "PUT",
      headers: {
        "content-type": "application/json",
        "x-library-bridge-secret": "bridge-secret",
      },
      body: JSON.stringify({ source_html: sourceHtml }),
    }),
    env,
  );
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.status, "updated");
  assert.match(body.issue.text, /공유 원본 편집/);
});

test("a source-only update preserves the existing cover and references", async () => {
  const env = makeEnv();
  const original = await createTestIssue(env, {
    id: "daily:2026-08-25",
    title: "건너온 것",
    coverPath: "/media/daily/cover.webp",
    references: ["공개 자료 A", "공개 자료 B"],
  });
  const endpoint = "https://example.test/api/library/issues/daily%3A2026-08-25";
  const sourceHtml = original.sourceHtml.replace("</article>", "<p>본문 수정</p></article>");
  const update = await worker.fetch(
    new Request(endpoint, {
      method: "PUT",
      headers: {
        "content-type": "application/json",
        "x-library-bridge-secret": "bridge-secret",
      },
      body: JSON.stringify({ source_html: sourceHtml }),
    }),
    env,
  );
  const updated = await update.json();
  assert.equal(updated.issue.coverPath, original.coverPath);
  assert.deepEqual(updated.issue.references, original.references);

  const reread = await (await worker.fetch(new Request(endpoint), env)).json();
  assert.equal(reread.issue.sourceHtml, updated.issue.sourceHtml);
  assert.equal(reread.issue.coverPath, original.coverPath);
  assert.deepEqual(reread.issue.references, original.references);
});

test("an update can replace and clear references without changing the cover", async () => {
  const env = makeEnv();
  const original = await createTestIssue(env, {
    id: "digest:2026-08-25",
    title: "자료의 범위",
    coverPath: "/media/digest/cover.jpg",
    references: ["내부 기록"],
  });
  const endpoint = "https://example.test/api/library/issues/digest%3A2026-08-25";

  const replace = await worker.fetch(
    new Request(endpoint, {
      method: "PUT",
      headers: {
        "content-type": "application/json",
        "x-library-bridge-secret": "bridge-secret",
      },
      body: JSON.stringify({
        source_html: original.sourceHtml,
        references: ["원 논문 A", "원 논문 B"],
      }),
    }),
    env,
  );
  const replaced = await replace.json();
  assert.deepEqual(replaced.issue.references, ["원 논문 A", "원 논문 B"]);
  assert.equal(replaced.issue.coverPath, original.coverPath);

  const clear = await worker.fetch(
    new Request(endpoint, {
      method: "PUT",
      headers: {
        "content-type": "application/json",
        "x-library-bridge-secret": "bridge-secret",
      },
      body: JSON.stringify({
        source_html: original.sourceHtml,
        references: [],
      }),
    }),
    env,
  );
  const cleared = await clear.json();
  assert.deepEqual(cleared.issue.references, []);

  const reread = await (await worker.fetch(new Request(endpoint), env)).json();
  assert.equal(reread.issue.sourceHtml, original.sourceHtml);
  assert.equal(reread.issue.coverPath, original.coverPath);
  assert.deepEqual(reread.issue.references, []);
});

test("does not rewrite an unchanged canonical document", async () => {
  const env = makeEnv();
  await createTestIssue(env, { id: "daily:2026-08-25", title: "건너온 것" });
  const endpoint = "https://example.test/api/library/issues/daily%3A2026-08-25";
  const original = await (await worker.fetch(new Request(endpoint), env)).json();
  const response = await worker.fetch(
    new Request(endpoint, {
      method: "PUT",
      headers: {
        "content-type": "application/json",
        "x-library-bridge-secret": "bridge-secret",
      },
      body: JSON.stringify({ source_html: original.issue.sourceHtml }),
    }),
    env,
  );
  const body = await response.json();
  assert.equal(body.status, "unchanged");
  assert.equal(body.issue.updatedAt, original.issue.updatedAt);
});

test("creates a new online issue once", async () => {
  const env = makeEnv();
  const sourceHtml = testSource("새 글");
  const request = () => new Request("https://example.test/api/library/issues", {
    method: "POST",
    headers: ownerHeaders({ "content-type": "application/json" }),
    body: JSON.stringify({
      id: "daily:2026-08-26",
      collection: "daily",
      date: "2026-08-26",
      published_at: "2026-08-26T08:00:00+09:00",
      source_html: sourceHtml,
      cover_path: "/media/issues/daily-2026-08-26.png",
    }),
  });
  assert.equal((await worker.fetch(request(), env)).status, 201);
  assert.equal((await worker.fetch(request(), env)).status, 409);
});

test("stores and serves generated raster assets", async () => {
  const env = makeEnv();
  const bytes = new Uint8Array([137, 80, 78, 71]);
  const write = await worker.fetch(
    new Request("https://example.test/api/library/assets/issues/daily-2026-08-26.png", {
      method: "PUT",
      headers: ownerHeaders({ "content-type": "image/png" }),
      body: bytes,
    }),
    env,
  );
  assert.equal(write.status, 200);
  assert.deepEqual(await write.json(), {
    status: "stored",
    path: "/media/issues/daily-2026-08-26.png",
    bytes: 4,
  });

  const read = await worker.fetch(
    new Request("https://example.test/media/issues/daily-2026-08-26.png"),
    env,
  );
  assert.equal(read.status, 200);
  assert.equal(read.headers.get("content-type"), "image/png");
  assert.deepEqual(new Uint8Array(await read.arrayBuffer()), bytes);
});

test("keeps an unknown edition as a real 404", async () => {
  const env = makeEnv();
  const response = await worker.fetch(
    new Request("https://example.test/editions/daily/issues/missing", {
      headers: { accept: "text/html" },
    }),
    env,
  );
  assert.equal(response.status, 404);
});

test("emits the files required by Sites packaging", async () => {
  await access(new URL("../dist/client/index.html", import.meta.url));
  await access(new URL("../dist/server/index.js", import.meta.url));
  await access(new URL("../dist/.openai/hosting.json", import.meta.url));
  await access(new URL("../drizzle/0000_keen_ben_parker.sql", import.meta.url));
});

test("bundles only the shared reader and editor assets", async () => {
  const readerCss = await readFile(new URL("../public/reader.css", import.meta.url), "utf8");
  const readerJs = await readFile(new URL("../public/reader.js", import.meta.url), "utf8");
  const editorJs = await readFile(new URL("../public/library-editor.js", import.meta.url), "utf8");
  const homeFonts = await readFile(new URL("../src/fonts.css", import.meta.url), "utf8");
  const homeStyles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
  await access(new URL("../public/fonts/NotoSansKR-Regular-korean.woff2", import.meta.url));
  await access(new URL("../public/fonts/NotoSansKR-Bold-korean.woff2", import.meta.url));
  await access(new URL("../public/fonts/NotoSerifKR-Bold-korean.woff2", import.meta.url));
  await access(new URL("../public/fonts/BarlowCondensed-SemiBold-latin.woff2", import.meta.url));
  await access(new URL("../public/fonts/LibraryBrush-2.2-Regular.woff2", import.meta.url));
  await access(new URL("../public/fonts/OFL-Barlow.txt", import.meta.url));
  await access(new URL("../public/icons/library/library-wordmark.png", import.meta.url));
  assert.match(readerCss, /Library reader/);
  assert.match(readerCss, /--font-title: "Noto Serif KR"/);
  assert.match(readerCss, /--font-sans: "Noto Sans KR"/);
  assert.match(readerCss, /--font-brand: "Library Brush"/);
  assert.match(readerCss, /--font-label: "Library Brush"/);
  assert.match(homeFonts, /BarlowCondensed-SemiBold-latin\.woff2/);
  assert.match(homeFonts, /LibraryBrush-2\.2-Regular\.woff2/);
  assert.match(homeStyles, /font-family: var\(--font-label\)/);
  assert.match(homeStyles, /\.archive-ticket > span:not\(\.library-wordmark\)[\s\S]*?letter-spacing: -0\.025em/);
  assert.match(readerCss, /--tracking-label: -0\.025em/);
  assert.match(readerCss, /\.reader-header h1/);
  assert.match(readerCss, /\.reader-key-sentence::selection/);
  assert.match(readerCss, /color: var\(--accent-ink\) !important/);
  assert.match(readerCss, /background: var\(--accent\) !important/);
  assert.match(readerJs, /data-reader-larger/);
  assert.match(editorJs, /dataset\.libraryIssueId/);
  assert.match(editorJs, /document\.modelContext/);
  assert.match(editorJs, /library_apply_draft/);
  assert.match(editorJs, /library_save_issue/);
  assert.match(editorJs, /library_discard_draft/);
  assert.match(editorJs, /AUTOSAVE_DELAY = 1_200/);
  assert.match(editorJs, /MIN_SAVE_INTERVAL = 3_000/);
  assert.match(editorJs, /saveAfterCurrent/);
  assert.match(editorJs, /event_handler_not_allowed/);
});

test("keeps owner editing always available without persistent editing controls", async () => {
  const editorJs = await readFile(new URL("../public/library-editor.js", import.meta.url), "utf8");
  const editorCss = await readFile(new URL("../public/library-editor.css", import.meta.url), "utf8");
  assert.match(editorJs, /setAttribute\("contenteditable", "plaintext-only"\)/);
  assert.match(editorJs, /article\.setAttribute\("contenteditable", "true"\)/);
  assert.match(editorJs, /library-save-status/);
  assert.doesNotMatch(editorJs, /data-library-edit|data-library-save|data-library-cancel/);
  assert.match(editorCss, /library-owner-editable/);
  assert.match(editorCss, /caret-color: var\(--accent/);
  assert.doesNotMatch(editorCss, /library-editor-state|grid-template-columns/);
});

test("keeps the Sites output newer than its source inputs", async () => {
  const root = fileURLToPath(new URL("../", import.meta.url));
  const inputTimes = await Promise.all([
    latestMtime(path.join(root, "src")),
    latestMtime(path.join(root, "public")),
    latestMtime(path.join(root, "worker")),
    latestMtime(path.join(root, "db")),
    latestMtime(path.join(root, "drizzle")),
    latestMtime(path.join(root, "index.html")),
    latestMtime(path.join(root, ".openai/hosting.json")),
  ]);
  const built = await stat(path.join(root, "dist/client/index.html"));
  assert.ok(built.mtimeMs + 1000 >= Math.max(...inputTimes), "dist is older than the current site inputs");
});
