import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
test('management screens can route every declared operation through the Site proxy', () => {
  const route = read('../app/api/context/[operation]/route.ts');
  const allowed = new Set([...route.matchAll(/"((?:corpus|sense)_[a-z_]+)"/g)].map(match => match[1]));
  const pages = ['../app/manage/page.tsx', '../app/manage/sense/page.tsx'].map(read).join('\n');
  const used = new Set([...pages.matchAll(/"((?:corpus|sense)_[a-z_]+)"/g)].map(match => match[1]));
  for (const name of used) assert.ok(allowed.has(name), `Missing Site operation: ${name}`);
  for (const name of ['corpus_registrations_list', 'corpus_connection_detach', 'corpus_workspace_detach'])
    assert.ok(allowed.has(name), `Missing registration operation: ${name}`);
  assert.match(read('../app/manage/page.tsx'), /include_context_skill:\s*true/);
});
