import test from "node:test";
import assert from "node:assert/strict";
import { renderIssuePage, issueHtmlResponse } from "../lib/issue-page.ts";

const issue = {
  id: "daily:2026-09-21", collection: "daily", date: "2026-09-21", version: 8,
  sourceHtml: '<!doctype html><html lang="ko"><head><meta http-equiv="Content-Security-Policy" content="default-src \'self\'; font-src \'none\'"></head><body><main><h1>제목</h1><article><p>보존할 원문 <strong>강조</strong></p></article></main></body></html>',
};
test("reader injects shared presentation without changing article markup or identity", () => {
  const rendered = renderIssuePage(issue);
  assert.ok(rendered.includes('<article><p>보존할 원문 <strong>강조</strong></p></article>'));
  assert.ok(rendered.includes('data-library-version="8"'));
  assert.ok(rendered.includes('data-library-issue-id="daily:2026-09-21"'));
  assert.ok(rendered.includes('data-toolkit-reader="true"'));
  assert.ok(rendered.includes('/toolkit-reader.css'));
  assert.ok(rendered.includes('/library-editor.js'));
  assert.ok(rendered.includes("font-src 'self'"));
  assert.ok(rendered.includes("connect-src 'self'"));
  assert.equal(issue.sourceHtml.includes('toolkit-reader'), false);
});
test("Flow reader keeps the article and shared styles but does not load the editor", async () => {
  const rendered = renderIssuePage(issue, true);
  assert.ok(rendered.includes('<article><p>보존할 원문 <strong>강조</strong></p></article>'));
  assert.ok(rendered.includes('/toolkit-reader.css'));
  assert.ok(!rendered.includes('/library-editor.js'));
  assert.ok(!rendered.includes('/library-editor.css'));
  const response = issueHtmlResponse(issue, false, true);
  assert.ok(!(await response.text()).includes('/library-editor.js'));
});
test("reader response allows only same-origin framing and never caches private articles", async () => {
  const response = issueHtmlResponse(issue);
  assert.equal(response.headers.get("content-security-policy"), "frame-ancestors 'self'");
  assert.equal(response.headers.get("x-frame-options"), "SAMEORIGIN");
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.equal(await issueHtmlResponse(issue, true).text(), "");
});

test("embedded reader preserves the existing WebMCP editing registry with scoped cleanup", async () => {
  const {readFile}=await import("node:fs/promises");
  const editor=await readFile(new URL("../public/library-editor.js",import.meta.url),"utf8");
  const reader=await readFile(new URL("../components/library/library-reader.tsx",import.meta.url),"utf8");
  assert.ok(editor.includes('window.parent.location.origin === window.location.origin'));
  assert.ok(editor.includes('window.parent.document.modelContext'));
  assert.ok(editor.includes('document.addEventListener("toolkit-reader-dispose"'));
  assert.ok(reader.includes('doc.dispatchEvent(new Event("toolkit-reader-dispose"))'));
});
