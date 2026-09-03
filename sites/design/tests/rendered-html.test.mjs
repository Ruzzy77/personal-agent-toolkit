import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("디자인 라이브러리를 서버에서 렌더링한다", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>디자인 라이브러리<\/title>/);
  assert.match(html, /디자인 기준 찾기/);
  assert.match(html, /참고 방향/);
  assert.match(html, /레시피와 예시/);
  assert.match(html, /형식/);
  assert.match(html, /웹·앱/);
  assert.match(html, /문서/);
  assert.match(html, /슬라이드/);
  assert.match(html, /이미지/);
  assert.match(html, /내용/);
  assert.match(html, /요청 만들기/);
  assert.match(html, /필수 내용만/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|SkeletonPreview/);

  const catalog = JSON.parse(
    await readFile(new URL("../app/design-catalog.json", import.meta.url), "utf8"),
  );
  for (const recipe of catalog.recipes) {
    const koreanName = recipe.name.replace(/\s*\(.*\)$/, "");
    assert.ok(html.includes(koreanName), `${koreanName} 이름이 화면에 없습니다`);
  }
});

test("디자인 카탈로그와 모든 미리보기를 함께 패키징한다", async () => {
  const catalog = JSON.parse(
    await readFile(new URL("../app/design-catalog.json", import.meta.url), "utf8"),
  );

  assert.equal(catalog.catalog_schema_version, 2);
  assert.equal(catalog.library.license, "Apache-2.0");
  assert.ok(catalog.patterns.length > 0);
  assert.ok(catalog.recipes.length > 0);
  const patternIds = new Set(catalog.patterns.map((pattern) => pattern.id));
  assert.ok(
    catalog.recipes.some(
      (recipe) => recipe.status === "validated" && recipe.selection_ready === true,
    ),
  );
  for (const recipe of catalog.recipes) {
    assert.equal(recipe.schema_version, 3, `${recipe.id}: schema v3가 아닙니다`);
    assert.equal(recipe.kind, "recipe", `${recipe.id}: recipe가 아닙니다`);
    assert.equal(recipe.visibility, "public", `${recipe.id}: 공개 레시피가 아닙니다`);
    assert.ok(recipe.pattern_refs.length, `${recipe.id}: 연결 패턴이 없습니다`);
    assert.ok(
      recipe.pattern_refs.every((patternId) => patternIds.has(patternId)),
      `${recipe.id}: 등록되지 않은 패턴을 참조합니다`,
    );
    assert.equal(recipe.provenance?.license, "Apache-2.0");
    assert.ok(Array.isArray(recipe.provenance?.references));
    assert.ok(recipe.gallery?.korean_name, `${recipe.id}: gallery.korean_name이 없습니다`);
    assert.ok(recipe.gallery?.note, `${recipe.id}: gallery.note가 없습니다`);
    assert.ok(recipe.gallery?.directions?.length, `${recipe.id}: gallery.directions가 없습니다`);
    assert.ok(recipe.gallery?.swatches?.length, `${recipe.id}: gallery.swatches가 없습니다`);
  }
  assert.ok(
    catalog.recipes.every(
      (recipe) =>
        ["web", "document", "slides", "image"].every((format) =>
          recipe.formats.includes(format) && recipe.format_support[format].includes("guidance"),
        ) && recipe.format_guide,
    ),
  );
  assert.doesNotMatch(JSON.stringify(catalog), /library-issue|Personal Library/);

  await Promise.all(
    catalog.recipes.flatMap((recipe) => [
      access(
        new URL(
          `../public/previews/${recipe.id}/index.html`,
          import.meta.url,
        ),
      ),
      access(
        new URL(
          `../public/previews/${recipe.id}/formats.json`,
          import.meta.url,
        ),
      ),
      ...Object.values(recipe.templates).map((templatePath) =>
        access(
          new URL(
            `../public/templates/${recipe.id}/${templatePath}`,
            import.meta.url,
          ),
        ),
      ),
    ]),
  );

  await Promise.all([
    access(new URL("../public/previews/hanji/assets/chart-bars-light.png", import.meta.url)),
    access(new URL("../public/previews/hanji/assets/chart-bars-dark.png", import.meta.url)),
    access(new URL("../public/previews/formwork/assets/flow-blueprint.png", import.meta.url)),
  ]);
  await assert.rejects(
    access(new URL("../public/templates/saegin/templates/library-issue.html", import.meta.url)),
  );
});

test("거푸집 문서 사이트 표준과 HTML 틀을 함께 제공한다", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("../designs/formwork/design.json", import.meta.url), "utf8"),
  );
  const siteCss = await readFile(
    new URL("../designs/formwork/site.css", import.meta.url),
    "utf8",
  );
  const template = await readFile(
    new URL("../designs/formwork/templates/document-site.html", import.meta.url),
    "utf8",
  );

  assert.deepEqual(manifest.profiles.site, ["tokens.css", "base.css", "doc.css", "site.css"]);
  assert.equal(manifest.templates["document-site"], "templates/document-site.html");
  assert.match(siteCss, /\.fw-global-bar\b/);
  assert.match(siteCss, /\.fw-sibling-nav\b/);
  assert.match(siteCss, /\.fw-document-toc\b/);
  assert.match(siteCss, /\.fw-sheet\b/);
  assert.match(template, /profile=site/);
  assert.match(template, /aria-label="같은 영역의 문서"/);
  assert.match(template, /aria-label="현재 문서 목차"/);
});
