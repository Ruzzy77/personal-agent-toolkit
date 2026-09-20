import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
test('Workspace has one management entry and four product views', () => {
  assert.match(read('../app/settings/page.tsx'), /href="\/manage"/);
  assert.match(read('../app/manage/layout.tsx'), /requireOwnerUser/);
  const navigation = read('../app/manage/navigation.tsx');
  for (const path of ['/manage', '/manage/sense', '/manage/library', '/manage/design']) assert.ok(navigation.includes(`"${path}"`));
  assert.match(navigation, /aria-current/);
  assert.match(read('../app/manage/corpus-manager.tsx'), /include_context_skill:\s*true/);
});
test('management uses the authenticated, same-origin Workspace proxy without a new credential', () => {
  const route = read('../app/api/management/[operation]/route.ts');
  for (const boundary of ['proxyOwnerRequest', '/admin/v1/']) assert.ok(route.includes(boundary));
  const helper=read('../lib/owner-service.ts');
  for(const boundary of ['ownerSession','csrfMatches','ownerAccessToken'])assert.ok(helper.includes(boundary));
  assert.ok(!route.includes('CONTEXT_SITE_TOKEN'));
  assert.match(read('../lib/management.ts'), /call\(name, input, "management"\)/);
  assert.ok(!read('../app/api/context/[operation]/route.ts').includes('corpus_document_trash'));
});


test("Toolkit keeps all destinations in a responsive sidebar with an accessible mobile toggle", () => {
  const shell = read("../app/toolkit-shell.tsx"), css = read("../app/workspace.css");
  for (const href of ["/journal", "/library", "/design", "/settings"]) assert.ok(shell.includes(href));
  assert.match(shell, /aria-expanded={menuOpen}/);
  assert.match(shell, /aria-controls="toolkit-menu"/);
  assert.match(css, /\.toolkit-menu\.is-open\{display:flex\}/);
  assert.match(css, /\.toolkit-content\{margin-left:0\}/);
  assert.ok(!css.includes(".file-detail{display:flex}"));
  assert.match(css, /\.file-detail\{[^}]*flex-direction:column/);
});

test("file workspace renders a conditional preview and preserves draft recovery controls", () => {
  const view = read("../app/workspace-files.tsx");
  assert.match(view, /selected&&<section className="file-detail"/);
  assert.match(view, /restoredDraft/);
  assert.match(view, /expected_version:value.version/);
  assert.match(view, /Boolean\(incoming\)/);
  assert.match(view, /file-list-more/);
  assert.match(view, /파일 더 불러오기/);
  assert.match(view, /role={error\?"alert":"status"}/);
});

test("owner web worker may call public services in the same Cloudflare account", () => {
  const config = read("../wrangler.example.jsonc");
  assert.match(config, /global_fetch_strictly_public/);
});

test("clean checkouts build with the public worker configuration", () => {
  const config = read("../vite.config.ts");
  assert.match(config, /existsSync/);
  assert.match(config, /wrangler\.example\.jsonc/);
});

test("embedded products apply tokens at their scope root and share the Toolkit theme", () => {
  const css = read("../styles/products.css"), gallery = read("../components/design/design-gallery.tsx");
  for (const name of ["journal-shell", "library-app"]) {
    assert.ok(css.includes(`@scope (.${name}) {\n:scope {`));
  }
  assert.ok(css.includes(":scope {\n  --bg: var(--su-paper);"));
  assert.ok(css.includes(":scope.gallery--dark"));
  assert.match(gallery, /useSyncExternalStore\(subscribeTheme, currentTheme, initialTheme\)/);
  assert.ok(!gallery.includes('className="theme-button"'));
});

test("workspace root picker is wide enough for long registered root names", () => {
  const view = read("../app/workspace-files.tsx"), css = read("../app/workspace.css");
  assert.match(view, /<FieldSelect aria-label="작업공간"/);
  assert.match(css, /\.file-workspace-heading \[aria-label="작업공간"\]\{width:min\(22rem,calc\(100vw - var\(--su-space-8\)\)\);max-width:100%\}/);
});

test("workspace popovers stay above sticky file headers and sidebar actions align", () => {
  const css = read("../app/workspace.css");
  assert.match(css, /\.ui-document>\[data-radix-popper-content-wrapper\]\{z-index:100!important\}/);
  assert.match(css, /\.toolkit-account-trigger>span\{justify-content:flex-start!important\}/);
});
