import { chatGPTUserFromHeaders, serviceFetch } from '@personal-agent/site-runtime';

export const dynamic = 'force-dynamic';
const operations = new Set([
  'sense_read', 'sense_revise', 'sense_skill_revise', 'corpus_space_list', 'corpus_space_get',
  'corpus_space_search', 'corpus_context_items_revise', 'corpus_context_skill_revise',
  'corpus_file_read', 'corpus_space_create', 'corpus_workspace_resolve',
  'corpus_document_create', 'corpus_document_list', 'corpus_document_read',
  'corpus_document_revise', 'corpus_document_restore',
]);
export async function POST(request: Request, context: { params: Promise<{ operation: string }> }) {
  const user = chatGPTUserFromHeaders(request.headers);
  if (!user) return Response.json({ error: 'authentication_required' }, { status: 401 });
  if (request.headers.get('origin') !== new URL(request.url).origin) {
    return Response.json({ error: 'origin_mismatch' }, { status: 403 });
  }
  const { operation } = await context.params;
  if (!operations.has(operation)) return Response.json({ error: 'operation_not_found' }, { status: 404 });
  const baseUrl = process.env.CONTEXT_SERVICE_URL;
  const token = process.env.CONTEXT_SITE_TOKEN;
  if (!baseUrl || !token) return Response.json({ error: 'context_not_configured' }, { status: 503 });
  try {
    const chunks: Uint8Array[] = []; let size = 0;
    const reader = request.body?.getReader();
    if (!reader) return Response.json({ error: 'invalid_json' }, { status: 400 });
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      size += value.byteLength;
      if (size > 16 * 1024 * 1024) { await reader.cancel(); return Response.json({ error: 'request_too_large' }, { status: 413 }); }
      chunks.push(value);
    }
    const body = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
    const response = await serviceFetch({ baseUrl, token, path: '/site/v1/' + operation,
      init: { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Personal-Agent-Site-User-Id': user.userId }, body } });
    return new Response(response.body, { status: response.status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
  } catch { return Response.json({ error: 'context_unavailable' }, { status: 502 }); }
}
