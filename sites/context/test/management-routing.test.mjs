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


test("Toolkit links into Flow without duplicating its navigation", () => {
  const shell = read("../app/toolkit-shell.tsx"), css = read("../app/workspace.css");
  for (const href of ["/flow", "/flow?screen=library", "/flow?screen=files", "/settings"]) assert.ok(shell.includes(href));
  assert.match(shell, /aria-expanded={menuOpen}/);
  assert.match(shell, /aria-controls="toolkit-menu"/);
  assert.match(css, /\.toolkit-menu\.is-open\{display:flex(?:;|\})/);
  assert.match(css, /\.toolkit-content\{margin-left:0\}/);
  assert.ok(!css.includes(".file-detail{display:flex}"));
  assert.match(css, /\.file-detail\{[^}]*flex-direction:column/);
});

test("Flow is the main destination while files keep their own view", () => {
  assert.match(read("../app/page.tsx"), /redirect\("\/flow"\)/);
  assert.match(read("../app/flow/page.tsx"), /<FlowWorkspace \/>/);
  assert.match(read("../app/files/page.tsx"), /<WorkspaceFiles\/>/);
  const view=read("../components/flow/flow-workspace.tsx");
  assert.match(view, /readFlowWork/);
  assert.match(read("../lib/flow-client.ts"), /flow_work_read/);
  assert.match(read("../lib/flow-client.ts"), /flow_artifact_read/);
  assert.match(view, /ArtifactPreview/);
  assert.match(view, /flowContentForWeb/);
  assert.match(view, /<WorkCanvas /);
  assert.match(view, /<AppHeader /);
  assert.match(view, /<LibraryBrowser /);
  assert.match(view, /flow_library_upsert/);
  assert.match(view, /flow_snapshot_create/);
  assert.match(view, /baseline.current/);
  assert.doesNotMatch(view, /<ContentEditor|<BlankEditor|<ImageEditor|<DiagramEditor|setArtifactSpan|moveArtifact|addBlankArtifact/);
  assert.doesNotMatch(view, /resourceContentArtifact|beginBlankContent|renderArtifactEditor/);
  assert.match(read("../app/toolkit-shell.tsx"), /pathname === "\/flow"\) return children/);
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
  assert.match(view, /<HtmlContentView[^>]*body={draft.body}[^>]*showSourceToggle={false}/);
  assert.doesNotMatch(view, /dirty&&source/);
  assert.match(read("../components/flow/flow-host-file.tsx"), /<HtmlContentView body={body\?\?undefined}/);

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
  const css = read("../styles/products.css");
  for (const name of ["journal-shell", "library-app"]) {
    assert.ok(css.includes(`@scope (.${name}) {\n:scope {`));
  }
  assert.match(css, /@scope \(\.journal-shell\) \{\n:scope \{[^}]*--paper: var\(--su-paper\);/);
  assert.ok(/@scope \(\.library-app\) \{\n:scope \{[^}]*background: var\(--su-paper\);/.test(css), "Library scope uses the shared background token");
  assert.doesNotMatch(css, /@scope \(\.gallery\)/);
  const legacy = read("../app/design/page.tsx");
  assert.match(legacy, /requireOwnerUser/);
  assert.ok(legacy.includes("https://personal-uikit.hiyaq77.workers.dev/"));
  assert.ok(!legacy.includes("DesignGallery"));
});

test("workspace root picker is wide enough for long registered root names", () => {
  const view = read("../app/workspace-files.tsx"), css = read("../app/workspace.css");
  assert.match(view, /<FieldSelect aria-label="작업공간"/);
  assert.match(css, /\.file-workspace-heading \[aria-label="작업공간"\]\{width:min\(22rem,calc\(100vw - var\(--su-space-8\)\)\);max-width:100%\}/);
});

test("workspace popovers stay above sticky file headers and sidebar actions align", () => {
  const css = read("../app/workspace.css");
  assert.match(css, /html\[data-uikit\] \[data-radix-popper-content-wrapper\]\{z-index:100!important\}/);
  assert.match(css, /\.toolkit-account-trigger>span\{justify-content:flex-start!important\}/);
});

test("mobile navigation opens as an opaque panel without mixing with workspace content", () => {
  const css = read("../app/workspace.css");
  assert.match(css, /\.toolkit-sidebar\{position:sticky;inset:auto;top:0;[^}]*background:var\(--su-paper\)/);
  assert.match(css, /\.toolkit-menu\.is-open\{display:flex;position:fixed;inset:57px 0 0;[^}]*background:var\(--su-paper\);overflow:auto\}/);
  assert.match(css, /body:has\(\.toolkit-menu\.is-open\)\{overflow:hidden\}/);
});


test("Flow bounded reads are reachable through the authenticated web proxy",()=>{
 const route=read("../app/api/host/[operation]/route.ts");
 for(const name of ["flow_artifact_read","flow_change_list","flow_change_read"])assert.ok(route.includes('"'+name+'"'));
 assert.match(route,/proxyOwnerRequest/);
});


test("Toolkit brightness has one owner outside document styling",()=>{
 const ui=read("../app/ui.tsx"),root=read("../app/layout.tsx"),theme=read("../app/toolkit-theme.tsx");
 assert.match(ui,/<ToolkitThemeProvider>/);
 assert.match(theme,/<UIKitRoot colorScheme={theme}>/);
 assert.doesNotMatch(root,/<body[^>]*ui-document/);
 for(const path of ["../app/toolkit-shell.tsx","../components/flow/flow-workspace.tsx"]){
  const view=read(path);assert.match(view,/useToolkitTheme/);assert.doesNotMatch(view,/dataset\.theme\s*=/);
 }
 const css=read("../app/globals.css");
 for(const sheet of ["react.css","layout.css","components.css","document.css"])assert.ok(css.includes("@personal-agent/ui-kit/"+sheet));
});

test("Flow settings reuse the shared theme control and name the destination",()=>{
 const view=read("../components/flow/flow-workspace.tsx");
 assert.match(view,/<ThemeSetting value={theme} onChange={setTheme}\/>/);
 assert.doesNotMatch(view,/<SegmentedControl/);
 assert.match(view,/<ButtonLink href="\/settings"/);
 assert.match(view,/자료 관리와 폴더 연결/);
 assert.doesNotMatch(view,/계정과 연결 설정/);
});

test("Flow interaction surfaces use UIKit controls rather than native fallbacks",()=>{
 for(const path of ["../components/flow/flow-workspace.tsx","../components/flow/flow-file-picker.tsx"]){
  assert.doesNotMatch(read(path),/<(?:button|select|details|summary|input|textarea)\b/,path);
 }
});

test("resource toolbars respond to their pane width rather than the whole viewport",()=>{
 const css=read("../components/flow/flow-workspace.css");
 assert.match(css,/\.flow-resource-view\{container-type:inline-size/);
 assert.match(css,/@container\(max-width:560px\)\{\.flow-resource-view-head\{flex-direction:column/);
});
