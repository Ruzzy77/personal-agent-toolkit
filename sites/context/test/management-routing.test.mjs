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


test("owner web worker may call public services in the same Cloudflare account", () => {
  const config = read("../wrangler.example.jsonc");
  assert.match(config, /global_fetch_strictly_public/);
});
