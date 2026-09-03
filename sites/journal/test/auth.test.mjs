import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import test from 'node:test';

import { chatGPTUserFromHeaders } from '../app/chatgpt-user.ts';

test('requires both stable ChatGPT identity headers', () => {
  assert.equal(
    chatGPTUserFromHeaders(
      new Headers({ 'oai-authenticated-user-id': 'owner-1' }),
    ),
    null,
  );
  assert.equal(
    chatGPTUserFromHeaders(
      new Headers({ 'oai-authenticated-user-email': 'owner@example.test' }),
    ),
    null,
  );
});

test('decodes an optional authenticated display name safely', () => {
  assert.deepEqual(
    chatGPTUserFromHeaders(
      new Headers({
        'oai-authenticated-user-id': 'owner-1',
        'oai-authenticated-user-email': 'owner@example.test',
        'oai-authenticated-user-full-name': '%ED%99%8D%20%EA%B8%B8%EB%8F%99',
        'oai-authenticated-user-full-name-encoding': 'percent-encoded-utf-8',
      }),
    ),
    {
      userId: 'owner-1',
      displayName: '홍 길동',
      email: 'owner@example.test',
      fullName: '홍 길동',
    },
  );

  assert.deepEqual(
    chatGPTUserFromHeaders(
      new Headers({
        'oai-authenticated-user-id': 'owner-1',
        'oai-authenticated-user-email': 'owner@example.test',
        'oai-authenticated-user-full-name': '%E0%A4%A',
        'oai-authenticated-user-full-name-encoding': 'percent-encoded-utf-8',
      }),
    ),
    {
      userId: 'owner-1',
      displayName: 'owner@example.test',
      email: 'owner@example.test',
      fullName: null,
    },
  );
});

test('keeps every Journal page and API handler behind server-side auth', async () => {
  const routeRoot = new URL('../app/api/journal/', import.meta.url);
  const routeFiles = await collectRouteFiles(routeRoot);
  assert.equal(routeFiles.length, 9);

  for (const routeFile of routeFiles) {
    const source = await readFile(routeFile, 'utf8');
    const handlerCount = [
      ...source.matchAll(/export async function (?:GET|POST|PATCH)\b/g),
    ].length;
    const guardCount = [...source.matchAll(/await requireChatGPTApiUser\(\)/g)]
      .length;
    assert.equal(
      guardCount,
      handlerCount,
      `${routeFile.pathname} must authenticate every handler`,
    );
  }

  const page = await readFile(
    new URL('../app/page.tsx', import.meta.url),
    'utf8',
  );
  assert.match(page, /export const dynamic = 'force-dynamic'/);
  assert.match(page, /await requireChatGPTUser\(/);
});

async function collectRouteFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const child = new URL(
      `${entry.name}${entry.isDirectory() ? '/' : ''}`,
      directory,
    );
    if (entry.isDirectory()) files.push(...(await collectRouteFiles(child)));
    if (entry.isFile() && entry.name === 'route.ts') files.push(child);
  }
  return files;
}
