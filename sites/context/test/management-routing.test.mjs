import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
test('Workspace has one management entry and four product views', () => {
  assert.match(read('../app/page.tsx'), /href="\/manage">관리<\/a>/);
  const navigation = read('../app/manage/navigation.tsx');
  for (const path of ['/manage', '/manage/sense', '/manage/library', '/manage/design']) assert.ok(navigation.includes(`"${path}"`));
  assert.match(navigation, /aria-current/);
  assert.match(read('../app/manage/corpus-manager.tsx'), /include_context_skill:\s*true/);
});
test('management uses the authenticated, same-origin Workspace proxy without a new credential', () => {
  const route = read('../app/api/management/[operation]/route.ts');
  for (const boundary of ['chatGPTUserFromHeaders', 'origin_mismatch', 'CONTEXT_SITE_TOKEN', 'X-Personal-Agent-Site-User-Id', '/admin/v1/']) assert.ok(route.includes(boundary));
  assert.match(read('../lib/management.ts'), /call\(name, input, "management"\)/);
  assert.ok(!read('../app/api/context/[operation]/route.ts').includes('corpus_document_trash'));
});
