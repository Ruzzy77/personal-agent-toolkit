import type { Locator } from './context';
export type SourceKind = 'base' | 'sense' | 'skill' | 'project';
export type Applicability = { activities: string[]; targets: string[]; topics: string[]; paths: string[] };
export type Content = { body: string; name?: string; description?: string; applicability?: Applicability };
export type GuidanceSource = {
  id: string;
  kind: SourceKind;
  title: string;
  reference: string;
  version: string;
  content: Content;
  activation: 'new_session' | 'next_use';
  canonical?: Locator;
  permission?: 'read_only' | 'read_write';
  role?: 'guidance' | 'context' | 'source';
  links?: Array<{ label: string; locator?: Locator }>;
};
export type Entry = {
  source: GuidanceSource;
  draft: Content;
  draftId: string;
  incoming?: GuidanceSource;
  savedAt?: string;
};
export type Workspace = { schemaVersion: 1; entries: Entry[]; selectedId: string | null };
export const emptyWorkspace = (): Workspace => ({ schemaVersion: 1, entries: [], selectedId: null });
export const storageKey = 'guidance-editor:v1';
export const kindNames: Record<SourceKind, string> = {
  base: '기본 지침', sense: 'Sense', skill: '스킬', project: '프로젝트',
};
export const sameContent = (a: Content, b: Content) =>
  a.body === b.body && a.name === b.name && a.description === b.description &&
  (a.applicability === undefined || b.applicability === undefined
    ? a.applicability === b.applicability
    : (['activities','targets','topics','paths'] as const).every(key =>
      JSON.stringify(a.applicability![key]) === JSON.stringify(b.applicability![key])));
export const isDirty = (entry: Entry) => !sameContent(entry.source.content, entry.draft);

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('올바른 자료 객체가 필요합니다.');
  return value as Record<string, unknown>;
}
function string(value: unknown, label: string, allowEmpty = false): string {
  if (typeof value !== 'string' || (!allowEmpty && !value.trim())) throw new Error(label + '을 확인해 주세요.');
  return value;
}
export function parseContent(value: unknown, allowIncomplete = false): Content {
  const input = record(value);
  if (Object.keys(input).some(key => !['body', 'name', 'description', 'applicability'].includes(key))) {
    throw new Error('지원하지 않는 편집 항목이 있습니다.');
  }
  const content: Content = { body: string(input.body, '본문', true) };
  if (input.name !== undefined) content.name = string(input.name, '이름', allowIncomplete);
  if (input.description !== undefined) content.description = string(input.description, '설명', true);
  if (input.applicability !== undefined) {
    const scope = record(input.applicability);
    if (Object.keys(scope).some(k => !['activities','targets','topics','paths'].includes(k))) throw new Error('적용 범위 형식 오류');
    content.applicability = Object.fromEntries(['activities','targets','topics','paths'].map(k => {
      const values = scope[k];
      if (!Array.isArray(values) || !values.every(v => typeof v === 'string')) throw new Error('적용 범위 형식 오류');
      return [k, values];
    })) as Applicability;
  }
  return content;
}
export const contentIssue = (content: Content) =>
  content.name !== undefined && !content.name.trim() ? '이름을 입력한 뒤 반영을 요청해 주세요.' : undefined;
function parseLocator(value: unknown): Locator {
  const locator = record(value);
  const product = string(locator.product, '정본 종류');
  if (product === 'sense') return { product, sectionId: string(locator.sectionId, '섹션'), ...(locator.skill === true ? { skill: true } : {}) };
  const spaceId = string(locator.spaceId, '프로젝트');
  if (product === 'corpus') return { product, spaceId, documentId: string(locator.documentId, '문서') };
  if (product === 'context-item') return { product, spaceId, itemId: string(locator.itemId, '항목') };
  if (product === 'context-skill') return { product, spaceId };
  if (product === 'source') return { product, spaceId, readRef: string(locator.readRef, '출처') };
  throw new Error('정본 종류 오류');
}
export function parseSource(value: unknown): GuidanceSource {
  const input = record(value);
  const kind = string(input.kind, '자료 종류') as SourceKind;
  if (!Object.hasOwn(kindNames, kind)) throw new Error('지원하지 않는 자료 종류입니다.');
  const activation = input.activation;
  if (activation !== 'new_session' && activation !== 'next_use') throw new Error('적용 시점을 확인해 주세요.');
  if (kind === 'base' && activation !== 'new_session') throw new Error('기본 지침은 새 실행에서 적용됩니다.');
  const content = parseContent(input.content);
  if (kind === 'skill' && (content.name === undefined || content.description === undefined)) {
    throw new Error('스킬에는 이름과 설명이 필요합니다.');
  }
  return {
    id: string(input.id, '자료 식별자'), kind, title: string(input.title, '제목'),
    reference: string(input.reference, '원본 위치'), version: string(input.version, '원본 버전'),
    content, activation,
    ...(input.canonical !== undefined ? { canonical: parseLocator(input.canonical) } : {}),
    ...(['guidance','context','source'].includes(String(input.role)) ? { role: input.role as GuidanceSource['role'] } : {}),
    ...(Array.isArray(input.links) ? { links: input.links.map(value => { const link = record(value); return { label: string(link.label, '출처'), ...(link.locator ? { locator: parseLocator(link.locator) } : {}) }; }) } : {}),
    ...(input.permission === 'read_only' || input.permission === 'read_write' ? { permission: input.permission } : {}),
  };
}
export function parseSources(value: unknown): GuidanceSource[] {
  const items = Array.isArray(value) ? value : record(value).sources;
  if (!Array.isArray(items) || items.length === 0) throw new Error('가져올 자료가 없습니다.');
  const sources = items.map(parseSource);
  if (new Set(sources.map(s => s.id)).size !== sources.length) throw new Error('자료 식별자가 중복되었습니다.');
  return sources;
}
function assertIdentity(a: GuidanceSource, b: GuidanceSource) {
  if (a.id !== b.id || a.reference !== b.reference || a.kind !== b.kind || a.activation !== b.activation || JSON.stringify(a.canonical) !== JSON.stringify(b.canonical) || a.permission !== b.permission) {
    throw new Error('같은 식별자에 다른 원본이 연결되어 있습니다.');
  }
}
function sameVersionContent(a: GuidanceSource, b: GuidanceSource) {
  if (a.version === b.version && !sameContent(a.content, b.content)) {
    throw new Error('같은 원본 버전의 내용이 다릅니다. 정본을 다시 읽어 주세요.');
  }
}
export function loadSources(workspace: Workspace, sources: GuidanceSource[]): Workspace {
  const entries = [...workspace.entries];
  for (const source of sources) {
    const index = entries.findIndex(e => e.source.id === source.id);
    if (index < 0) {
      entries.push({ source, draft: { ...source.content }, draftId: crypto.randomUUID() });
      continue;
    }
    const entry = entries[index];
    assertIdentity(entry.source, source);
    sameVersionContent(entry.source, source);
    if (entry.incoming) sameVersionContent(entry.incoming, source);
    if (entry.source.version === source.version) continue;
    entries[index] = isDirty(entry)
      ? { ...entry, incoming: source }
      : { source, draft: { ...source.content }, draftId: crypto.randomUUID() };
  }
  return { ...workspace, entries, selectedId: workspace.selectedId ?? entries[0]?.source.id ?? null };
}
export function requireEntry(workspace: Workspace, id: string): Entry {
  const entry = workspace.entries.find(e => e.source.id === id);
  if (!entry) throw new Error('현재 화면에 없는 자료입니다.');
  return entry;
}
function replaceEntry(workspace: Workspace, entry: Entry): Workspace {
  return { ...workspace, entries: workspace.entries.map(e => e.source.id === entry.source.id ? entry : e) };
}
export function updateDraft(workspace: Workspace, id: string, content: Content, expectedVersion: string, expectedDraftId: string): Workspace {
  const entry = requireEntry(workspace, id);
  if (entry.source.permission === 'read_only') throw new Error('읽기 전용 자료');
  if (entry.draftId !== expectedDraftId) throw new Error('수정안이 바뀌었습니다. 다시 읽어 주세요.');
  if (entry.incoming) throw new Error('원본이 바뀌었습니다. 화면에서 변경 내용을 먼저 비교해 주세요.');
  if (entry.source.version !== expectedVersion) throw new Error('원본 버전이 다릅니다. 정본을 다시 읽어 주세요.');
  if (Object.keys(entry.source.content).sort().join(',') !== Object.keys(content).sort().join(',')) {
    throw new Error('이 자료의 편집 항목을 바꿀 수 없습니다.');
  }
  if (sameContent(entry.draft, content)) return workspace;
  return replaceEntry(workspace, { ...entry, draft: content, draftId: crypto.randomUUID() });
}
export function pendingChanges(workspace: Workspace) {
  return workspace.entries.filter(isDirty).map(entry => ({
    id: entry.source.id, kind: entry.source.kind, title: entry.source.title,
    reference: entry.source.reference, canonical: entry.source.canonical, permission: entry.source.permission, expectedVersion: entry.source.version,
    draftId: entry.draftId, content: entry.draft,
    blocked: Boolean(entry.incoming || contentIssue(entry.draft)), latestVersion: entry.incoming?.version,
    reason: entry.incoming ? '원본 변경을 먼저 비교해 주세요.' : contentIssue(entry.draft),
  }));
}
export function acknowledgeSaved(workspace: Workspace, id: string, expectedVersion: string, draftId: string, source: GuidanceSource): Workspace {
  const entry = requireEntry(workspace, id);
  assertIdentity(entry.source, source);
  if (entry.source.version !== expectedVersion) throw new Error('저장 대상 원본 버전이 다릅니다.');
  if (entry.draftId !== draftId) throw new Error('저장 요청 뒤 수정안이 바뀌었습니다. 새 수정안은 보존했습니다. 저장된 정본을 다시 가져와 비교해 주세요.');
  if (entry.incoming && entry.incoming.version !== source.version) throw new Error('다른 원본 변경이 도착했습니다. 다시 비교해 주세요.');
  if (!sameContent(source.content, entry.draft)) throw new Error('저장된 내용과 현재 수정안이 다릅니다.');
  sameVersionContent(entry.source, source);
  return replaceEntry(workspace, {
    source, draft: { ...source.content }, draftId: crypto.randomUUID(), savedAt: new Date().toISOString(),
  });
}
export function resolveIncoming(workspace: Workspace, id: string, keepDraft: boolean): Workspace {
  const entry = requireEntry(workspace, id);
  if (!entry.incoming) throw new Error('새 원본이 없습니다.');
  const source = entry.incoming;
  return replaceEntry(workspace, {
    source, draft: keepDraft ? { ...entry.draft } : { ...source.content }, draftId: crypto.randomUUID(),
  });
}
export function resetDraft(workspace: Workspace, id: string): Workspace {
  const entry = requireEntry(workspace, id);
  const source = entry.incoming ?? entry.source;
  return replaceEntry(workspace, { source, draft: { ...source.content }, draftId: crypto.randomUUID() });
}
export function restoreWorkspace(raw: string): Workspace {
  const input = record(JSON.parse(raw));
  if (input.schemaVersion !== 1 || !Array.isArray(input.entries)) throw new Error('보관된 수정안을 읽을 수 없습니다.');
  const entries = input.entries.map(value => {
    const e = record(value);
    const source = parseSource(e.source);
    const draft = parseContent(e.draft, true);
    if (Object.keys(source.content).sort().join(',') !== Object.keys(draft).sort().join(',')) throw new Error('보관된 편집 항목이 다릅니다.');
    const entry: Entry = { source, draft, draftId: string(e.draftId, '수정안 식별자') };
    if (e.incoming !== undefined) { entry.incoming = parseSource(e.incoming); assertIdentity(source, entry.incoming); }
    if (typeof e.savedAt === 'string') entry.savedAt = e.savedAt;
    return entry;
  });
  if (new Set(entries.map(e => e.source.id)).size !== entries.length) throw new Error('보관된 자료가 중복되었습니다.');
  const selectedId = typeof input.selectedId === 'string' && entries.some(e => e.source.id === input.selectedId)
    ? input.selectedId : entries[0]?.source.id ?? null;
  return { schemaVersion: 1, entries, selectedId };
}
export type Section = { key: string; title: string; start: number; end: number; text: string };
export function sectionsOf(body: string): Section[] {
  const starts: Array<{ offset: number; title: string }> = [];
  let offset = 0;
  let fence: string | null = null;
  for (const line of body.split('\n')) {
    const marker = line.match(/^\s*(`{3,}|~{3,})/);
    if (marker) { if (!fence) fence = marker[1][0]; else if (marker[1][0] === fence) fence = null; }
    const heading = !fence && line.match(/^##\s+(.+?)\s*#*\s*$/);
    if (heading) starts.push({ offset, title: heading[1] });
    offset += line.length + 1;
  }
  if (!starts.length || starts[0].offset > 0) starts.unshift({ offset: 0, title: body.match(/^#\s+(.+)/)?.[1] ?? '본문' });
  return starts.map((s, index) => ({
    key: 'section-' + index, title: s.title, start: s.offset,
    end: starts[index + 1]?.offset ?? body.length,
    text: body.slice(s.offset, starts[index + 1]?.offset ?? body.length),
  }));
}
