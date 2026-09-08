'use client';

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { candidates, groups, readCanonical, saveCanonical, saveGroups, locatorOf, contextCall, type Candidate, type Group } from '../lib/context';
import { registerGuidanceTools } from '../lib/webmcp';
import {
  acknowledgeSaved, contentIssue, emptyWorkspace, isDirty, kindNames, loadSources, parseSources, pendingChanges,
  resetDraft, resolveIncoming, restoreWorkspace, sectionsOf, storageKey, updateDraft,
  type Content, type Workspace, type GuidanceSource,
} from '../lib/guidance';

function Inline({ text }: { text: string }) {
  return <>{text.split(/(\*\*[^*]+\*\*|\x60[^\x60]+\x60|\[[^\]]+\]\([^)]+\))/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.charCodeAt(0) === 96) return <code key={i}>{part.slice(1, -1)}</code>;
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link && /^(https?:\/\/|#)/i.test(link[2])) return <a key={i} href={link[2]} target={link[2].startsWith('#') ? undefined : '_blank'} rel="noreferrer">{link[1]}</a>;
    return <Fragment key={i}>{part}</Fragment>;
  })}</>;
}
function Markdown({ text, title }: { text: string; title?: string }) {
  const lines = text.split('\n'); const blocks = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) continue;
    const fence = line.match(/^\s*(\x60{3,}|~{3,})/);
    if (fence) {
      const code = [];
      while (++i < lines.length && !lines[i].trim().startsWith(fence[1])) code.push(lines[i]);
      blocks.push(<pre key={i}><code>{code.join('\n')}</code></pre>); continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      if (i === 0 && heading[1] === '#' && heading[2] === title) continue;
      const label = <Inline text={heading[2]} />;
      blocks.push(heading[1].length <= 2 ? <h2 key={i}>{label}</h2> : <h3 key={i}>{label}</h3>); continue;
    }
    if (/^\s*[-*]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
      const ordered = /^\s*\d/.test(line); const list = [];
      do { list.push(lines[i].replace(/^\s*(?:[-*]|\d+[.)])\s+/, '')); }
      while (++i < lines.length && (ordered ? /^\s*\d+[.)]\s+/.test(lines[i]) : /^\s*[-*]\s+/.test(lines[i])));
      i--;
      const items = list.map((item, j) => <li key={j}><Inline text={item} /></li>);
      blocks.push(ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>); continue;
    }
    if (line.includes('|') && /^\s*\|?\s*:?-{3}/.test(lines[i + 1] ?? '')) {
      const cells = (value: string) => value.trim().replace(/^\||\|$/g, '').split('|').map(v => v.trim());
      const headers = cells(line); const rows: string[][] = []; i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
      i--;
      blocks.push(<div className="table-wrap" key={i}><table><thead><tr>{headers.map((h, j) => <th key={j}><Inline text={h} /></th>)}</tr></thead><tbody>{rows.map((row, j) => <tr key={j}>{row.map((c, k) => <td key={k}><Inline text={c} /></td>)}</tr>)}</tbody></table></div>); continue;
    }
    if (/^\s*>/.test(line)) { blocks.push(<blockquote key={i}><Inline text={line.replace(/^\s*>\s?/, '')} /></blockquote>); continue; }
    if (/^\s*(?:---+|\*\*\*+)\s*$/.test(line)) { blocks.push(<hr key={i} />); continue; }
    const paragraph = [line];
    while (i + 1 < lines.length && lines[i + 1].trim() && !/^(#{1,6}\s|\s*[-*>]\s|\s*\d+[.)]\s|\s*[\x60~]{3})/.test(lines[i + 1])) paragraph.push(lines[++i]);
    blocks.push(<p key={i}><Inline text={paragraph.join('\n')} /></p>);
  }
  return <>{blocks}</>;
}
type Editing = { start: number; end: number; title: string };

function Modal({ titleId, close, children }: { titleId: string; close: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = ref.current;
    dialog?.showModal();
    return () => { dialog?.close(); previous?.focus(); };
  }, []);
  return <dialog ref={ref} className="import-dialog" aria-labelledby={titleId} onCancel={event => { event.preventDefault(); close(); }}>{children}</dialog>;
}

export default function Home() {
  const [workspace, setWorkspace] = useState<Workspace>(emptyWorkspace);
  const stateRef = useRef(workspace);
  const [ready, setReady] = useState(false);
  const [query, setQuery] = useState('');
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [message, setMessage] = useState('');
  const [storageError, setStorageError] = useState('');
  const recoveryRef = useRef<string | null>(null);
  const [compare, setCompare] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [activeSection, setActiveSection] = useState('section-0');
  const [dark, setDark] = useState(false);
  const [copyFallback, setCopyFallback] = useState('');
  const [, setToolSupport] = useState<boolean | null>(null);
  const [catalog, setCatalog] = useState<Group[]>([]);
  const [groupId, setGroupId] = useState('sense');
  const [available, setAvailable] = useState<Candidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [previous, setPrevious] = useState<GuidanceSource | null>(null);
  const requestSequence = useRef(0);
  const compact = useRef<HTMLDetailsElement>(null);
  const entry = workspace.entries.find(e => e.source.id === workspace.selectedId);
  const sections = useMemo(() => sectionsOf(entry?.draft.body ?? ''), [entry?.draft.body]);
  const changes = pendingChanges(workspace);
  const filtered = workspace.entries.filter(e => [e.source.title, e.draft.name, e.draft.description, e.draft.body].join('\n').toLocaleLowerCase().includes(query.toLocaleLowerCase()));

  const commit = useCallback((next: Workspace) => {
    stateRef.current = next; setWorkspace(next);
    try { sessionStorage.setItem(storageKey, JSON.stringify(next)); setStorageError(''); }
    catch { setStorageError('수정안 보관 실패'); }
  }, []);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      try {
        const raw = sessionStorage.getItem(storageKey);
        if (raw) {
          recoveryRef.current = raw;
          const restored = restoreWorkspace(raw);
          stateRef.current = restored; setWorkspace(restored);
          recoveryRef.current = null;
        }
      } catch {
        setStorageError('수정안 복원 실패');
      }
      setDark(matchMedia('(prefers-color-scheme: dark)').matches); setReady(true);
    });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (stateRef.current.entries.some(isDirty)) event.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, []);
  useEffect(() => {
    if (ready) document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  }, [dark, ready]);
  useEffect(() => {
    if (editing || compare || !entry) return;
    const observer = new IntersectionObserver(records => {
      const visible = records.filter(r => r.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible[0]) setActiveSection(visible[0].target.id);
    }, { rootMargin: '-70px 0px -55% 0px' });
    document.querySelectorAll('[data-guidance-section]').forEach(el => observer.observe(el));
    return () => observer.disconnect();
  }, [entry, compare, editing]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && compact.current?.open) {
        compact.current.open = false; compact.current.querySelector('summary')?.focus();
      }
    };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, []);

  useEffect(() => {
    if (!ready) return;
    return registerGuidanceTools({
      read: () => stateRef.current,
      write: commit,
      resetEditor: () => setEditing(null),
      report: setMessage,
      open: (id, sectionKey) => {
        commit({ ...stateRef.current, selectedId: id });
        setEditing(null); setCompare(false); setActiveSection(sectionKey ?? 'section-0');
        if (compact.current) compact.current.open = false;
        requestAnimationFrame(() => document.getElementById(sectionKey ?? 'document-title')?.focus());
      },
    }, setToolSupport);
  }, [ready, commit]);

  useEffect(() => {
    if (!ready) return;
    let active = true;
    groups().then(value => { if (active) setCatalog(value); }).catch(() => { if (active) setMessage('정본 연결 실패'); });
    return () => { active = false; };
  }, [ready]);
  useEffect(() => {
    const group = catalog.find(g => g.id === groupId); if (!group) return;
    let active = true;
    const timer = setTimeout(() => {
      candidates(group, query).then(value => { if (active) setAvailable(value); })
        .catch(() => { if (active) setMessage('자료 조회 실패'); });
    }, query ? 250 : 0);
    return () => { active = false; clearTimeout(timer); };
  }, [catalog, groupId, query]);
  async function openCandidate(candidate: Candidate) {
    const sequence = ++requestSequence.current;
    setBusy(true); setMessage('');
    try {
      const source = await readCanonical(candidate.locator);
      commit(loadSources(stateRef.current, [source]));
      if (sequence === requestSequence.current) selectSource(source.id);
    } catch { setMessage('자료 조회 실패'); }
    finally { if (sequence === requestSequence.current) setBusy(false); }
  }
  async function refresh() {
    const current = stateRef.current.entries.find(e => e.source.id === stateRef.current.selectedId);
    const locator = current && locatorOf(current.source);
    if (!locator) return;
    setBusy(true);
    try { commit(loadSources(stateRef.current, [await readCanonical(locator)])); setMessage(''); }
    catch { setMessage('정본 조회 실패'); }
    finally { setBusy(false); }
  }
  async function save() {
    const pending = stateRef.current.entries.filter(e => isDirty(e) && !contentIssue(e.draft));
    setBusy(true); setMessage('');
    let failed = false;
    for (const group of saveGroups(pending)) {
      try {
        const saved = await saveCanonical(group);
        for (let i = 0; i < group.length; i++) {
          const submitted = group[i];
          try { commit(acknowledgeSaved(stateRef.current, submitted.source.id, submitted.source.version, submitted.draftId, saved[i])); }
          catch { commit(loadSources(stateRef.current, [saved[i]])); failed = true; }
        }
      } catch {
        failed = true;
        for (const e of group) {
          try { commit(loadSources(stateRef.current, [await readCanonical(locatorOf(e.source)!)])); }
          catch { /* Retain the existing draft when the canonical store is unavailable. */ }
        }
      }
    }
    setBusy(false); setMessage(failed ? '미확인 수정안 · 비교 필요' : '');
  }
  async function previousVersion() {
    const locator = entry && locatorOf(entry.source);
    if (locator?.product !== 'corpus') return;
    try { setPrevious(await readCanonical(locator, 'previous')); setCompare(true); setEditing(null); }
    catch { setMessage('직전 저장본 없음'); }
  }
  async function restorePrevious() {
    const locator = entry && locatorOf(entry.source);
    if (locator?.product !== 'corpus' || !previous || !entry || isDirty(entry)) return;
    setBusy(true);
    try {
      await contextCall('corpus_document_restore', { space_id: locator.spaceId, document_id: locator.documentId, expected_version: Number(entry.source.version) });
      commit(loadSources(stateRef.current, [await readCanonical(locator)])); setPrevious(null); setCompare(false);
    } catch { setMessage('복원 실패 · 정본 확인 필요'); }
    finally { setBusy(false); }
  }
  function selectSource(id: string) {
    commit({ ...stateRef.current, selectedId: id });
    setEditing(null); setCompare(false); setPrevious(null); setActiveSection('section-0');
    if (compact.current) compact.current.open = false;
    requestAnimationFrame(() => document.getElementById('document-title')?.focus());
  }
  function navigateSection(key: string) {
    setEditing(null); setCompare(false); setActiveSection(key);
    if (compact.current) compact.current.open = false;
    requestAnimationFrame(() => document.getElementById(key)?.focus());
  }
  function importSources(value: unknown) {
    const sources = parseSources(value);
    commit(loadSources(stateRef.current, sources));
    setImportOpen(false); setImportText(''); setEditing(null);
    setMessage('');
  }
  function act(action: () => void) {
    try { action(); } catch (error) { setMessage(error instanceof Error ? error.message : '다시 시도해 주세요.'); }
  }
  function editContent(content: Content) {
    const current = stateRef.current.entries.find(e => e.source.id === stateRef.current.selectedId);
    if (!current) return;
    commit(updateDraft(stateRef.current, current.source.id, content, current.source.version, current.draftId));
  }
  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); setMessage('복사됨'); }
    catch { setCopyFallback(text); setMessage(''); }
  }
  function downloadRecovery() {
    const blob = new Blob([recoveryRef.current ?? JSON.stringify(stateRef.current)], { type: 'application/json' });
    const url = URL.createObjectURL(blob); const a = document.createElement('a');
    a.href = url; a.download = 'guidance-draft-recovery.json'; a.click(); URL.revokeObjectURL(url);
  }
  const navigation = (prefix: string) => <>
    <label className="visually-hidden" htmlFor={prefix + '-group'}>자료 모음</label>
    <select id={prefix + '-group'} value={groupId} onChange={e => { setGroupId(e.target.value); setQuery(''); setAvailable([]); }}>
      {catalog.length ? catalog.map(g => <option key={g.id} value={g.id}>{g.title}</option>) : <option value="sense">Sense</option>}
    </select>
    <label className="visually-hidden" htmlFor={prefix + '-search'}>자료 검색</label>
    <input id={prefix + '-search'} className="search" type="search" placeholder="검색" value={query} onChange={e => setQuery(e.target.value)} />
    <nav className="source-list" aria-label="자료">
      {available.map(c => <button key={c.id} aria-current={c.id === workspace.selectedId ? 'page' : undefined} onClick={() => void openCandidate(c)}>
        <span>{c.title}</span><span className="source-kind">{c.subtitle === '스킬' || c.subtitle === '지침' || c.subtitle === 'Context' ? c.subtitle : ''}</span>
      </button>)}
      {query && !available.length && <p className="muted">결과 없음</p>}
    </nav>
    {filtered.length > 0 && <details className="tab-documents"><summary>열린 자료</summary><nav className="source-list" aria-label="열린 자료">
      {filtered.map(e => <button key={e.source.id} aria-current={e.source.id === workspace.selectedId ? 'page' : undefined} onClick={() => selectSource(e.source.id)}>
        <span>{e.source.title}</span>{isDirty(e) && <span className="draft-dot" aria-label="미저장 수정안">•</span>}
      </button>)}
    </nav></details>}
    {entry && <nav className="section-list" aria-label="현재 문서 목차">{sections.map(s => <a key={s.key} href={'#' + s.key} aria-current={activeSection === s.key ? 'location' : undefined} onClick={() => navigateSection(s.key)}>{s.title}</a>)}</nav>}
  </>;

  return <>
    <a className="skip" href="#main">본문으로 건너뛰기</a>
    <header className="compact-nav">
      <details ref={compact}><summary><span className="wordmark">Workspace</span><span className="compact-current">{entry ? sections.find(s => s.key === activeSection)?.title ?? entry.source.title : 'Sense · Corpus'}</span><span className="chevron" aria-hidden="true" /></summary>
        <div className="compact-panel">{navigation('compact')}</div>
      </details>
    </header>
    <div className="layout">
      <aside className="rail">
        <a className="brand" href="#main">Workspace</a>
        {navigation('rail')}
        <div className="rail-tools"><button aria-label="자료 가져오기" onClick={() => setImportOpen(true)}>↥</button><button aria-label={dark ? '밝은 화면' : '어두운 화면'} aria-pressed={dark} onClick={() => setDark(!dark)}>◐</button></div>
      </aside>
      <main id="main" tabIndex={-1}>
        <div className="top-actions"><button aria-label="자료 가져오기" onClick={() => setImportOpen(true)}>↥</button><button aria-label={dark ? '밝은 화면' : '어두운 화면'} aria-pressed={dark} onClick={() => setDark(!dark)}>◐</button></div>
        <div className="status-area" role="status" aria-live="polite">{message}</div>
        {storageError && <div className="notice"><p>{storageError}</p><button onClick={downloadRecovery}>보관본 내려받기</button></div>}
        {!entry ? <div className="welcome"><h1>Sense · Corpus</h1></div> : <>
          <header className="document-header">
            <p className="eyebrow">{entry.source.kind === 'base' ? '기본 지침' : entry.source.role === 'guidance' ? '프로젝트 지침' : entry.source.role === 'context' ? 'Context' : kindNames[entry.source.kind]}</p>
            <h1 id="document-title" tabIndex={-1}>{entry.draft.name ?? entry.source.title}</h1>
            {entry.draft.description && <p className="lead">{entry.draft.description}</p>}
            <div className="document-state">
              <span>{entry.incoming ? '원본 변경 · 비교 필요' : isDirty(entry) ? '미저장 수정안' : entry.savedAt ? '저장됨' : entry.source.permission === 'read_only' ? '읽기 전용' : '정본'}</span>
              {entry.source.activation === 'new_session' && <span>실행 적용 미확인</span>}
            </div>
          </header>
          <div className="document-toolbar">
            <div className="view-switch"><button aria-pressed={!compare} onClick={() => { setCompare(false); setPrevious(null); setEditing(null); }}>읽기</button><button aria-pressed={compare} onClick={() => { setCompare(true); setPrevious(null); setEditing(null); }}>변경 비교</button></div>
            <div className="toolbar-icons">
              <button aria-label="정본 다시 읽기" disabled={busy || !entry.source.canonical} onClick={() => void refresh()}>↻</button>
              {entry.source.canonical?.product === 'corpus' && <button aria-label="직전 저장본 비교" disabled={busy} onClick={() => void previousVersion()}>◷</button>}
              <button aria-label="수정안 복사" disabled={!changes.length} onClick={() => void copy(JSON.stringify({ changes }, null, 2))}>⧉</button>
              <button aria-label="정본에 저장" disabled={busy || !saveGroups(workspace.entries.filter(isDirty)).length} onClick={() => void save()}>✓</button>
            </div>
          </div>
          {entry.incoming && <div className="notice">
            <h2>원본 변경</h2>
            <details><summary>최신 원본 읽기</summary>{entry.incoming.content.name && <h3>{entry.incoming.content.name}</h3>}{entry.incoming.content.description && <p>{entry.incoming.content.description}</p>}<div className="prose"><Markdown text={entry.incoming.content.body} /></div></details>
            <div className="actions"><button onClick={() => act(() => { commit(resolveIncoming(stateRef.current, entry.source.id, true)); setEditing(null); setCompare(true); })}>수정안 유지</button><button onClick={() => act(() => { commit(resolveIncoming(stateRef.current, entry.source.id, false)); setEditing(null); setCompare(false); })}>최신 원본 사용</button></div>
          </div>}
          <article className={'article' + (compare ? ' comparison' : '')}>
            {compare ? <>
              <div className="compare-summary">{previous && <span>직전 저장본 · {previous.version}</span>}{previous && <button disabled={busy || isDirty(entry)} onClick={() => void restorePrevious()}>이 버전 복원</button>}{isDirty(entry) && <button onClick={() => act(() => { commit(resetDraft(stateRef.current, entry.source.id)); setEditing(null); setMessage('이 자료의 수정안을 원본으로 되돌렸습니다.'); })}>수정안 되돌리기</button>}</div>
              <div className="compare-grid">{[{ title: previous ? '직전 저장본' : '원본', content: previous?.content ?? entry.source.content }, { title: previous ? '현재' : '수정안', content: entry.draft }].map(column => <section key={column.title}><h2 className="compare-label">{column.title}</h2>{column.content.name && <h3>{column.content.name}</h3>}{column.content.description && <p>{column.content.description}</p>}{column.content.applicability && <dl>{Object.entries(column.content.applicability).map(([key, values]) => <Fragment key={key}><dt>{{activities:'활동',targets:'대상',topics:'분야',paths:'경로'}[key as 'activities'|'targets'|'topics'|'paths']}</dt><dd>{values.join(', ') || '—'}</dd></Fragment>)}</dl>}<div className="prose"><Markdown text={column.content.body} /></div></section>)}</div>
            </> : <>
              {(entry.draft.name !== undefined || entry.draft.description !== undefined) && <details className="metadata-editor"><summary>문서 정보</summary>
                {entry.draft.name !== undefined && <label>이름<input aria-invalid={Boolean(contentIssue(entry.draft))} aria-describedby={contentIssue(entry.draft) ? 'name-issue' : undefined} disabled={Boolean(entry.incoming) || entry.source.permission === 'read_only'} value={entry.draft.name} onChange={e => act(() => editContent({ ...entry.draft, name: e.target.value }))} /></label>}
                {contentIssue(entry.draft) && <p id="name-issue" role="status">{contentIssue(entry.draft)}</p>}
                {entry.draft.description !== undefined && <label>적용 설명<textarea aria-label="적용 설명" disabled={Boolean(entry.incoming) || entry.source.permission === 'read_only'} rows={3} value={entry.draft.description} onChange={e => act(() => editContent({ ...entry.draft, description: e.target.value }))} /></label>}
              </details>}
              {entry.draft.applicability && <details className="metadata-editor"><summary>적용 범위</summary>
                {(['activities','targets','topics','paths'] as const).map((key, index) => <label key={key}>{['활동','대상','분야','프로젝트 내 경로'][index]}<input
                  disabled={Boolean(entry.incoming)} value={entry.draft.applicability![key].join(', ')}
                  onChange={e => act(() => editContent({ ...entry.draft, applicability: { ...entry.draft.applicability!, [key]: e.target.value.split(',').map(v => v.trim()).filter(Boolean) } }))} /></label>)}
              </details>}
              {editing ? <section className="section-editor">
                <div className="section-editor-head"><h2>{editing.title}</h2><button onClick={() => setEditing(null)}>읽기로 돌아가기</button></div>
                <label className="visually-hidden" htmlFor="section-text">섹션 본문</label>
                <textarea id="section-text" autoFocus spellCheck={false} value={entry.draft.body.slice(editing.start, editing.end)} onChange={e => act(() => {
                  const value = e.target.value;
                  editContent({ ...entry.draft, body: entry.draft.body.slice(0, editing.start) + value + entry.draft.body.slice(editing.end) });
                  setEditing({ ...editing, end: editing.start + value.length });
                })} />

              </section> : sections.map(section => <section className="reading-section" id={section.key} key={section.key} data-guidance-section tabIndex={-1}>
                <div className="prose"><Markdown text={section.text} title={entry.draft.name ?? entry.source.title} /></div>
                <button className="edit-section" disabled={Boolean(entry.incoming) || entry.source.permission === 'read_only'} onClick={() => setEditing({ start: section.start, end: section.end, title: section.title })} aria-label={section.title + ' 편집'}>✎</button>
              </section>)}
            </>}
          </article>
          <footer className="document-footer"><details><summary>원본 정보</summary><dl><dt>위치</dt><dd>{entry.source.reference}</dd><dt>버전</dt><dd>{entry.source.version}</dd>{entry.source.links?.map((link, index) => <Fragment key={index}><dt>출처</dt><dd>{link.locator ? <button onClick={() => void openCandidate({ id: link.label, title: link.label, locator: link.locator! })}>{link.label}</button> : link.label}</dd></Fragment>)}</dl></details></footer>
        </>}
      </main>
    </div>
    {importOpen && <Modal titleId="import-title" close={() => setImportOpen(false)}>
      <div className="dialog-head"><h2 id="import-title">자료 가져오기</h2><button onClick={() => setImportOpen(false)} aria-label="가져오기 닫기">닫기</button></div>

      <label htmlFor="import-text">지침 자료</label><textarea id="import-text" autoFocus rows={9} value={importText} onChange={e => setImportText(e.target.value)}  />
      <div className="actions"><button className="primary" disabled={!importText.trim()} onClick={() => act(() => importSources(JSON.parse(importText)))}>가져오기</button><label className="file-button">파일 선택<input type="file" accept=".json,application/json" onChange={async e => {
        const file = e.target.files?.[0]; if (!file) return;
        try { importSources(JSON.parse(await file.text())); } catch (error) { setMessage(error instanceof Error ? error.message : '파일을 읽지 못했습니다.'); }
      }} /></label></div>
      {message && <p role="alert">{message}</p>}
    </Modal>}
    {copyFallback && <Modal titleId="copy-title" close={() => setCopyFallback('')}><div className="dialog-head"><h2 id="copy-title">수정안 복사</h2><button onClick={() => setCopyFallback('')}>닫기</button></div><textarea aria-label="복사할 수정안" readOnly rows={12} value={copyFallback} onFocus={e => e.target.select()} autoFocus /></Modal>}
  </>;
}
