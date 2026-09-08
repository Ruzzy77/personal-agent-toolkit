import { sameContent, type Content, type GuidanceSource, type Entry } from './guidance';

type Row = Record<string, unknown>;
const row = (value: unknown): Row => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('자료 형식 오류');
  return value as Row;
};
const list = (value: unknown): Row[] => Array.isArray(value) ? value.map(row) : [];
const str = (value: unknown) => typeof value === 'string' ? value : '';
export class ContextFailure extends Error {
  constructor(public code: string, public status: number) { super(code); }
}
export async function contextCall(name: string, input: unknown): Promise<Row> {
  const response = await fetch('/api/context/' + name, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input), cache: 'no-store',
  });
  const data = row(await response.json());
  if (!response.ok || data.ok === false) {
    throw new ContextFailure(typeof data.error === 'string' ? data.error : str(row(data.error).code), response.status);
  }
  return row(data.result);
}
export type Locator =
  | { product: 'sense'; sectionId: string; skill?: boolean }
  | { product: 'corpus'; spaceId: string; documentId: string }
  | { product: 'context-item'; spaceId: string; itemId: string }
  | { product: 'context-skill'; spaceId: string }
  | { product: 'source'; spaceId: string; readRef: string };
export type Candidate = { id: string; title: string; subtitle?: string; locator: Locator; readonly?: boolean };
export type Group = { id: string; title: string; product: 'sense' | 'corpus' };
const titles: Record<string, string> = {
  'questions-and-choices': '질문과 선택', 'scope-and-checking': '업무 범위',
  'evidence-and-judgment': '자료와 해석', 'explanation-and-output': '설명과 산출물 구성',
  'conversation-and-writing': '대화와 글', 'what-to-keep': '기억 체계',
  'visual-production': '시각 설계와 제작', 'research-exploration': '연구 탐색', 'research-review': '연구 검토',
};
export async function groups(): Promise<Group[]> {
  const result: Group[] = [{ id: 'sense', title: 'Sense', product: 'sense' }];
  let offset = 0;
  do {
    const data = await contextCall('corpus_space_list', { offset, limit: 100 });
    result.push(...list(data.spaces).map(s => ({ id: str(s.space_id), title: str(s.display_name), product: 'corpus' as const })));
    if (!data.has_more) break;
    offset = Number(data.next_offset);
  } while (true);
  return result;
}
const itemCandidate = (spaceId: string, item: Row): Candidate => ({
  id: `context-item:${spaceId}:${item.item_id}`, title: str(item.body_text).split('\n')[0].replace(/^#+\s*/, '').slice(0, 90) || str(item.item_id),
  subtitle: str(item.kind), locator: { product: 'context-item', spaceId, itemId: str(item.item_id) },
});
const docCandidate = (spaceId: string, doc: Row): Candidate => ({
  id: `corpus:${spaceId}:${doc.document_id}`, title: str(doc.title), subtitle: doc.kind === 'guidance' ? '지침' : 'Context',
  locator: { product: 'corpus', spaceId, documentId: str(doc.document_id) },
});
export async function candidates(group: Group, query = ''): Promise<Candidate[]> {
  if (group.product === 'sense') {
    const data = await contextCall('sense_read', { view: 'index', include_skill: false });
    return list(data.sections).filter(s => s.sensitivity === 'ordinary').flatMap(s => {
      const sectionId = str(s.id), title = titles[sectionId] ?? sectionId;
      return [{ id: `sense:${sectionId}`, title, subtitle: str(s.purpose), locator: { product: 'sense' as const, sectionId } },
        ...(s.skill ? [{ id: `sense-skill:${sectionId}`, title: str(row(s.skill).name), subtitle: str(row(s.skill).description), locator: { product: 'sense' as const, sectionId, skill: true } }] : [])];
    }).filter(c => !query || `${c.title}\n${c.subtitle}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  }
  const result: Candidate[] = [];
  if (query) {
    let offset = 0;
    do {
      const data = await contextCall('corpus_space_search', { space_id: group.id, query, search_scope: 'all', context_offset: offset, limit: 100 });
      const context = row(data.context);
      result.push(...list(context.items).map(item => item.result_type === 'document'
        ? docCandidate(group.id, { ...item, document_id: item.id })
        : itemCandidate(group.id, { item_id: item.id, body_text: item.snippet, kind: item.kind })));
      if (!offset) result.push(...list(data.candidates).map(item => ({
        id: `source:${group.id}:${item.unit_id}`, title: str(item.relative_path) || str(item.title) || str(item.unit_id),
        subtitle: str(item.snippet), locator: { product: 'source' as const, spaceId: group.id, readRef: str(item.read_ref) }, readonly: true,
      })));
      if (!context.has_more) break;
      offset = Number(context.next_offset);
    } while (true);
    return result;
  }
  let offset = 0;
  do {
    const data = await contextCall('corpus_space_get', { space_id: group.id, include_context_skill: false, context_offset: offset, document_limit: 200 });
    const space = row(data.space), context = space.context ? row(space.context) : null;
    if (!offset) {
      const documents = row(space.documents);
      result.push(...list(documents.items).map(d => docCandidate(group.id, d)));
      let docOffset = documents.next_offset;
      while (docOffset !== null) {
        const next = await contextCall('corpus_document_list', { space_id: group.id, offset: docOffset, limit: 200 });
        result.push(...list(next.items).map(d => docCandidate(group.id, d))); docOffset = next.next_offset;
      }
      if (context?.skill) result.push({ id: `context-skill:${group.id}`, title: str(row(context.skill).name), subtitle: '스킬', locator: { product: 'context-skill', spaceId: group.id } });
    }
    if (!context) break;
    result.push(...list(context.items).map(item => itemCandidate(group.id, item)));
    if (!context.has_more) break;
    offset = Number(context.next_offset);
  } while (true);
  return result;
}
export function locatorOf(source: GuidanceSource): Locator | undefined {
  return source.canonical as Locator | undefined;
}
async function currentItem(spaceId: string, itemId: string) {
  let offset = 0;
  do {
    const result = await contextCall('corpus_space_get', { space_id: spaceId, include_context_skill: false, context_offset: offset, document_limit: 1 });
    const context = row(row(result.space).context);
    const item = list(context.items).find(item => item.item_id === itemId);
    if (item) return { context, item };
    if (!context.has_more) throw new Error('항목을 찾을 수 없습니다.');
    offset = Number(context.next_offset);
  } while (true);
}
export async function readCanonical(locator: Locator, snapshot: 'current' | 'previous' = 'current'): Promise<GuidanceSource> {
  let role: GuidanceSource['role']; let links: GuidanceSource['links'];
  let id: string, title: string, version: string, content: Content, kind: GuidanceSource['kind'] = 'project';
  if (locator.product === 'sense') {
    const data = await contextCall('sense_read', { view: 'sections', section_ids: [locator.sectionId], include_skill: Boolean(locator.skill) });
    const section = list(data.sections)[0];
    if (!section) throw new Error('섹션을 찾을 수 없습니다.');
    id = `${locator.skill ? 'sense-skill' : 'sense'}:${locator.sectionId}`;
    if (locator.skill) {
      const skill = row(section.skill); kind = 'skill'; title = str(skill.name); version = str(skill.version);
      content = { name: title, description: str(skill.description), body: str(skill.instructions) };
    } else { kind = 'sense'; title = titles[locator.sectionId] ?? locator.sectionId; version = str(section.section_sha256); content = { body: str(section.text) }; }
  } else if (locator.product === 'corpus') {
    let data = await contextCall('corpus_document_read', { space_id: locator.spaceId, document_id: locator.documentId, snapshot, max_chars: 200000 });
    const doc = row(data.document); let body = str(doc.body_markdown);
    while (data.has_more) {
      data = await contextCall('corpus_document_read', { space_id: locator.spaceId, document_id: locator.documentId, snapshot, expected_version: doc.version, start_char: data.next_start_char, max_chars: 200000 });
      body += str(row(data.document).body_markdown);
    }
    id = `corpus:${locator.spaceId}:${locator.documentId}`; title = str(doc.title); version = String(doc.version);
    kind = locator.spaceId === 'base-instructions' && locator.documentId === 'base-instructions' ? 'base' : 'project';
    role = doc.kind === 'guidance' ? 'guidance' : 'context';
    links = list(doc.source_refs).map(ref => ({ label: str(ref.document_id) || '접근 불가', ...(ref.read_ref ? { locator: { product: 'source' as const, spaceId: locator.spaceId, readRef: str(ref.read_ref) } } : {}) }));
    content = { name: title, body, applicability: row(doc.applicability) as Content['applicability'] };
  } else if (locator.product === 'context-skill') {
    const data = await contextCall('corpus_space_get', { space_id: locator.spaceId, include_context_skill: true, context_limit: 1, document_limit: 1 });
    const skill = row(row(row(data.space).context).skill);
    id = `context-skill:${locator.spaceId}`; title = str(skill.name); version = str(skill.version); kind = 'skill';
    content = { name: title, description: str(skill.description), body: str(skill.instructions) };
  } else if (locator.product === 'context-item') {
    const { context, item } = await currentItem(locator.spaceId, locator.itemId);
    id = `context-item:${locator.spaceId}:${locator.itemId}`; title = str(item.body_text).split('\n')[0].slice(0, 90); version = String(context.version); content = { body: str(item.body_text) };
  } else {
    let data = await contextCall('corpus_file_read', { space_id: locator.spaceId, read_ref: locator.readRef, source_view: 'text', max_chars: 30000 });
    let body = str(data.untrusted_content);
    while (data.has_more) { data = await contextCall('corpus_file_read', { space_id: locator.spaceId, read_ref: locator.readRef, source_view: 'text', start_char: data.next_start_char, max_chars: 30000 }); body += str(data.untrusted_content); }
    const source = data.source ? row(data.source) : {};
    id = `source:${locator.spaceId}:${locator.readRef}`; title = str(source.relative_path) || 'Source'; version = str(source.revision_id) || locator.readRef; role = 'source';
    content = { body };
  }
  return { id, kind, title, version, content, reference: id, canonical: locator, ...(role ? { role } : {}), ...(links ? { links } : {}),
    permission: locator.product === 'source' ? 'read_only' : 'read_write', activation: kind === 'base' ? 'new_session' : 'next_use' };
}
export async function saveCanonical(entries: Entry[]): Promise<GuidanceSource[]> {
  if (!entries.length) return [];
  const first = locatorOf(entries[0].source);
  if (!first || first.product === 'source') throw new Error('읽기 전용 자료');
  for (const e of entries) {
    const loc = locatorOf(e.source); if (!loc) throw new Error('정본 연결 없음');
    const current = await readCanonical(loc);
    if (current.version !== e.source.version || current.id !== e.source.id || current.reference !== e.source.reference || !sameContent(current.content, e.source.content)) throw new ContextFailure('version_conflict', 409);
  }
  if (first.product === 'sense' && !first.skill) {
    const ids = entries.map(e => (locatorOf(e.source) as Extract<Locator, { product: 'sense' }>).sectionId);
    const data = await contextCall('sense_read', { view: 'sections', section_ids: ids, include_skill: false });
    const sections = list(data.sections);
    await contextCall('sense_revise', { changes: entries.map((e, index) => {
      const section = sections.find(s => s.id === ids[index])!;
      return { section_id: ids[index], previous_section_sha256: e.source.version,
        new_section: { id: section.id, purpose: section.purpose, text: e.draft.body, origins: section.origins, sensitivity: section.sensitivity } };
    }) });
  } else if (first.product === 'context-item') {
    const revisions = await Promise.all(entries.map(async e => {
      const loc = locatorOf(e.source) as Extract<Locator, { product: 'context-item' }>;
      const { item } = await currentItem(loc.spaceId, loc.itemId);
      return { item_id: loc.itemId, kind: item.kind, body_text: e.draft.body, status: row(item.attributes).status };
    }));
    await contextCall('corpus_context_items_revise', { space_id: first.spaceId, expected_version: Number(entries[0].source.version), revisions });
  } else {
    const e = entries[0], draft = e.draft;
    if (first.product === 'sense') await contextCall('sense_skill_revise', { section_id: first.sectionId, expected_version: e.source.version, new_skill: { name: draft.name, description: draft.description, instructions: draft.body } });
    if (first.product === 'context-skill') await contextCall('corpus_context_skill_revise', { space_id: first.spaceId, expected_version: e.source.version, new_skill: { name: draft.name, description: draft.description, instructions: draft.body } });
    if (first.product === 'corpus') {
      const data = await contextCall('corpus_document_read', { space_id: first.spaceId, document_id: first.documentId, max_chars: 1 });
      const doc = row(data.document);
      const approval = doc.guidance_approval ? row(doc.guidance_approval) : null;
      if (list(doc.source_refs).some(ref => ref.unavailable_reason)) throw new ContextFailure('source_connection_unavailable', 409);
      await contextCall('corpus_document_revise', { space_id: first.spaceId, document_id: first.documentId, expected_version: Number(e.source.version),
        title: draft.name, body_markdown: draft.body, kind: doc.kind, applicability: draft.applicability,
        guidance_approval: approval ? { explicit_user_approval: approval.explicit_user_approval, basis: approval.basis } : null,
        migration_provenance: doc.migration_provenance,
        source_refs: list(doc.source_refs).map(ref => Object.fromEntries(['connection_id','document_id','revision_id','projection_id','unit_id','link_role'].map(k => [k, ref[k]]))) });
    }
  }
  return Promise.all(entries.map(e => readCanonical(locatorOf(e.source)!)));
}
export function saveGroups(entries: Entry[]): Entry[][] {
  const grouped = new Map<string, Entry[]>();
  for (const e of entries) {
    const loc = locatorOf(e.source); if (!loc || loc.product === 'source' || e.incoming) continue;
    const key = loc.product === 'sense' && !loc.skill ? 'sense-sections' : loc.product === 'context-item' ? `items:${loc.spaceId}:${e.source.version}` : e.source.id;
    grouped.set(key, [...(grouped.get(key) ?? []), e]);
  }
  return [...grouped.values()].flatMap(group => {
    const max = locatorOf(group[0].source)?.product === 'sense' ? 12 : 20;
    return Array.from({ length: Math.ceil(group.length / max) }, (_, i) => group.slice(i * max, (i + 1) * max));
  });
}
