'use client';

import { ArrowLeft, Check, ChevronDown, Circle, Columns2, Copy, Ellipsis, Eye, FileUp, History, Info, Menu, Moon, Pencil, RefreshCw, Save, Search, Sun, X } from 'lucide-react';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, useLayoutEffect, type ReactNode, type ButtonHTMLAttributes } from 'react';
import { candidates, groups, readCanonical, saveCanonical, saveGroups, locatorOf, contextCall, type Candidate, type Group } from '../lib/context';
import { registerGuidanceTools } from '../lib/webmcp';
import { InlineDocument } from './inline-document';
import { replaceBodyIfCurrent } from '../lib/inline-markdown';
import {
  acknowledgeSaved, contentIssue, emptyWorkspace, isDirty, loadSources, parseSources, pendingChanges,
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
      if (lines.slice(0, i).every(line => !line.trim()) && heading[1] === '#' && heading[2] === title) continue;
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
function readingTitle(source: GuidanceSource, content: Content) {
  const heading = content.body.trimStart().match(/^#\s+(.+?)\s*#*\s*(?:\n|$)/)?.[1];
  return source.kind === 'skill' && heading ? heading : content.name ?? source.title;
}

function focusReadingTarget(id: string) {
  requestAnimationFrame(() => {
    const target = document.getElementById(id);
    target?.focus({ preventScroll: true });
    (id === 'document-title' ? document.getElementById('main') : target)?.scrollIntoView({ block: 'start', behavior: 'instant' });
  });
}

function IconButton({ label, children, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return <span className="icon-control"><button {...props} className={'icon-button ' + className} aria-label={label}>{children}</button><span className="tooltip" aria-hidden="true">{label}</span></span>;
}

function Modal({ titleId, close, children, className = '' }: { titleId: string; close: () => void; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = ref.current;
    dialog?.showModal();
    return () => { dialog?.close(); if (previous?.isConnected) previous.focus(); };
  }, []);
  return <dialog ref={ref} className={'import-dialog ' + className} aria-labelledby={titleId} onClick={event => {
    if (event.target !== event.currentTarget) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
  }} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
  }} onCancel={event => { event.preventDefault(); close(); }}>{children}</dialog>;
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
  const [editing, setEditing] = useState(false);
  const [activeSection, setActiveSection] = useState('section-0');
  const [dark, setDark] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [copyFallback, setCopyFallback] = useState('');
  const [, setToolSupport] = useState<boolean | null>(null);
  const [catalog, setCatalog] = useState<Group[]>([]);
  const [groupId, setGroupId] = useState('sense');
  const [available, setAvailable] = useState<Candidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [historyResult, setHistoryResult] = useState<{ source: GuidanceSource; baseVersion: string } | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const historySequence = useRef(0);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [navigationTab, setNavigationTab] = useState<'sources' | 'toc' | 'projects'>('sources');
  const [projectQuery, setProjectQuery] = useState('');
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [candidateError, setCandidateError] = useState(false);
  const requestSequence = useRef(0);
  const entry = workspace.entries.find(e => e.source.id === workspace.selectedId);
  const sections = useMemo(() => sectionsOf(entry?.draft.body ?? ''), [entry?.draft.body]);
  const previous = historyResult && historyResult.source.id === entry?.source.id && historyResult.baseVersion === entry?.source.version ? historyResult.source : null;
  const selectedLocator = entry && locatorOf(entry.source);
  const selectedGroupId = selectedLocator && 'spaceId' in selectedLocator ? selectedLocator.spaceId : selectedLocator?.product === 'sense' ? 'sense' : null;
  const browseGroup = catalog.find(g => g.id === groupId);
  const documentTitle = entry ? readingTitle(entry.source, entry.draft) : undefined;
  const titleOnlySection = sections[0]?.text.trim() === '# ' + documentTitle ? sections[0] : null;
  const canEdit = entry && !entry.incoming && entry.source.permission !== 'read_only';
  const changes = pendingChanges(workspace);
  const filtered = workspace.entries.filter(e => [e.source.title, e.draft.name, e.draft.description, e.draft.body].join('\n').toLocaleLowerCase().includes(query.toLocaleLowerCase()));

  const commit = useCallback((next: Workspace) => {
    if (stateRef.current.selectedId !== next.selectedId) { historySequence.current++; setHistoryLoading(false); setHistoryResult(null); }
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
    if (message !== '저장됨' && message !== '복사됨') return;
    const timer = setTimeout(() => setMessage(''), 3000);
    return () => clearTimeout(timer);
  }, [message]);
  useEffect(() => {
    if (!navigationOpen) return;
    const frame = requestAnimationFrame(() => document.querySelector<HTMLInputElement>('.navigation-dialog input[type="search"]')?.focus());
    return () => cancelAnimationFrame(frame);
  }, [navigationOpen, navigationTab]);
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
    if (!ready) return;
    return registerGuidanceTools({
      read: () => stateRef.current,
      write: commit,
      resetEditor: () => setEditing(false),
      report: setMessage,
      open: (id, sectionKey) => {
        commit({ ...stateRef.current, selectedId: id });
        setEditing(false); setCompare(false); setActiveSection(sectionKey ?? 'section-0');
        historySequence.current++; setHistoryResult(null); setHistoryLoading(false); setNavigationOpen(false); setMoreOpen(false); setInfoOpen(false);
        focusReadingTarget(sectionKey ?? 'document-title');
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
      setLoadingCandidates(true); setCandidateError(false);
      candidates(group, query).then(value => { if (active) setAvailable(value); })
        .catch(() => { if (active) { setAvailable([]); setCandidateError(true); } })
        .finally(() => { if (active) setLoadingCandidates(false); });
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
    if (!saveGroups(pending).length) return;
    setBusy(true); setMessage('');
    let failed = false;
    const versions = new Map<string, string>();
    const blockedSpaces = new Set<string>();
    for (const group of saveGroups(pending)) {
      const loc = locatorOf(group[0].source);
      const spaceId = loc?.product === 'context-item' ? loc.spaceId : undefined;
      const chain = spaceId ? `${spaceId}:${group[0].source.version}` : undefined;
      if (spaceId && blockedSpaces.has(spaceId)) {
        for (const e of group) {
          try { commit(loadSources(stateRef.current, [await readCanonical(locatorOf(e.source)!)])); }
          catch { /* Keep every outstanding draft when refresh is unavailable. */ }
        }
        continue;
      }
      try {
        const saved = await saveCanonical(group, chain ? versions.get(chain) : undefined);
        if (chain) versions.set(chain, saved[0].version);
        for (let i = 0; i < group.length; i++) {
          const submitted = group[i];
          try { commit(acknowledgeSaved(stateRef.current, submitted.source.id, submitted.source.version, submitted.draftId, saved[i])); }
          catch { commit(loadSources(stateRef.current, [saved[i]])); failed = true; }
        }
      } catch {
        failed = true;
        if (spaceId) blockedSpaces.add(spaceId);
        for (const e of group) {
          try { commit(loadSources(stateRef.current, [await readCanonical(locatorOf(e.source)!)])); }
          catch { /* Retain the existing draft when the canonical store is unavailable. */ }
        }
      }
    }
    setBusy(false); setMessage(failed ? '미확인 수정안 · 비교 필요' : '저장됨');
  }
  function dismissHistory() {
    historySequence.current++; setHistoryResult(null); setHistoryLoading(false);
  }
  async function previousVersion() {
    const current = stateRef.current.entries.find(e => e.source.id === stateRef.current.selectedId);
    const locator = current && locatorOf(current.source);
    if (locator?.product !== 'corpus' || !current) return;
    const sequence = ++historySequence.current;
    setHistoryResult(null); setHistoryLoading(true); setMessage('');
    try {
      const source = await readCanonical(locator, 'previous');
      const latest = stateRef.current.entries.find(e => e.source.id === stateRef.current.selectedId);
      if (sequence !== historySequence.current || latest?.source.id !== current.source.id || latest.source.version !== current.source.version) return;
      setHistoryResult({ source, baseVersion: current.source.version }); setCompare(true); setEditing(false);
    } catch { if (sequence === historySequence.current) setMessage('직전 저장본 조회 실패'); }
    finally { if (sequence === historySequence.current) setHistoryLoading(false); }
  }
  async function restorePrevious() {
    const current = stateRef.current.entries.find(e => e.source.id === stateRef.current.selectedId);
    const locator = current && locatorOf(current.source);
    if (locator?.product !== 'corpus' || !previous || !current || historyLoading || isDirty(current) || current.incoming || previous.id !== current.source.id || historyResult?.baseVersion !== current.source.version) return;
    const sequence = historySequence.current;
    setBusy(true);
    try {
      await contextCall('corpus_document_restore', { space_id: locator.spaceId, document_id: locator.documentId, expected_version: Number(current.source.version) });
      commit(loadSources(stateRef.current, [await readCanonical(locator)]));
      if (sequence === historySequence.current && stateRef.current.selectedId === current.source.id) { dismissHistory(); setCompare(false); }
    } catch { setMessage('복원 실패 · 정본 확인 필요'); }
    finally { setBusy(false); }
  }
  function selectSource(id: string) {
    commit({ ...stateRef.current, selectedId: id });
    setEditing(false); setCompare(false); dismissHistory(); setActiveSection('section-0');
    setNavigationOpen(false);
    focusReadingTarget('document-title');
  }
  function navigateSection(key: string) {
    setEditing(false); setCompare(false); dismissHistory(); setActiveSection(key);
    setNavigationOpen(false);
    focusReadingTarget(key);
  }
  function importSources(value: unknown) {
    const sources = parseSources(value);
    commit(loadSources(stateRef.current, sources));
    setImportOpen(false); setImportText(''); setEditing(false);
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
  function openNavigation(tab: 'sources' | 'toc' = 'sources') {
    if (selectedGroupId) setGroupId(selectedGroupId);
    setNavigationTab(tab); setNavigationOpen(true);
  }
  function switchView(mode: 'read' | 'edit' | 'compare') {
    setEditing(mode === 'edit'); setCompare(mode === 'compare'); dismissHistory();
    requestAnimationFrame(() => {
      const target = mode === 'edit' ? document.querySelector<HTMLElement>('.document-editor input, .document-editor textarea') : document.getElementById('document-title');
      target?.focus({ preventScroll: true });
      document.getElementById('main')?.scrollIntoView({ block: 'start', behavior: 'instant' });
    });
  }
  const projects = catalog.filter(g => g.title.toLocaleLowerCase().includes(projectQuery.toLocaleLowerCase()));

  return <>
    <a className="skip" href="#main">본문으로 건너뛰기</a>
    <header className="workspace-bar">
      <div className="workspace-home"><IconButton label="자료와 목차 열기" onClick={() => openNavigation()} aria-haspopup="dialog" aria-expanded={navigationOpen}><Menu aria-hidden="true" /></IconButton><span className="wordmark">Workspace</span></div>
      <div className="document-actions">
        {entry && <>
          {editing && <IconButton label="본문으로 돌아가기" onClick={() => switchView('read')}><Eye aria-hidden="true" /></IconButton>}
          <IconButton label={compare ? '비교 닫기' : '변경 비교'} aria-pressed={compare} onClick={() => switchView(compare ? 'read' : 'compare')}><Columns2 aria-hidden="true" /></IconButton>
        </>}
        {changes.length > 0 && <IconButton label="모든 수정안 저장" disabled={busy || !saveGroups(workspace.entries.filter(e => isDirty(e) && !contentIssue(e.draft))).length} onClick={() => void save()}><Save aria-hidden="true" /></IconButton>}
        <IconButton label="더 보기" aria-haspopup="dialog" aria-expanded={moreOpen} onClick={() => setMoreOpen(true)}><Ellipsis aria-hidden="true" /></IconButton>
      </div>
    </header>
    <div className="layout">
      <main id="main" tabIndex={-1}>
        <div className="status-area" role="status" aria-live="polite">{message}</div>
        {storageError && <div className="notice"><p>{storageError}</p><button onClick={downloadRecovery}>보관본 내려받기</button></div>}
        {!entry ? <div className="welcome"><h1>Sense · Corpus</h1><button onClick={() => openNavigation()}>자료 열기</button></div> : <>
          <header className={'document-header' + (editing || compare ? ' compact' : '')} id={titleOnlySection?.key} data-guidance-section={titleOnlySection ? true : undefined} tabIndex={titleOnlySection ? -1 : undefined} aria-labelledby="document-title">
            <h1 id="document-title" tabIndex={-1} className={selectedLocator?.product === 'context-item' ? 'visually-hidden' : undefined}>{selectedLocator?.product === 'context-item' ? 'Context 항목' : documentTitle}</h1>
            {!editing && !compare && entry.draft.description && <p className="lead">{entry.draft.description}</p>}
            <p className="document-state" role="status">{entry.incoming ? '원본 변경 · 비교 필요' : isDirty(entry) ? '미저장 수정안' : entry.source.permission === 'read_only' ? '읽기 전용' : null}</p>
          </header>
          {entry.incoming && <div className="notice">
            <h2>원본 변경</h2>
            <details><summary><ChevronDown className="summary-chevron" aria-hidden="true" />최신 원본 읽기</summary>{entry.incoming.content.name && <h3>{entry.incoming.content.name}</h3>}{entry.incoming.content.description && <p>{entry.incoming.content.description}</p>}<div className="prose"><Markdown text={entry.incoming.content.body} /></div></details>
            <div className="actions"><button onClick={() => act(() => { commit(resolveIncoming(stateRef.current, entry.source.id, true)); setEditing(false); setCompare(true); })}>수정안 유지</button><button onClick={() => act(() => { commit(resolveIncoming(stateRef.current, entry.source.id, false)); setEditing(false); setCompare(false); })}>최신 원본 사용</button></div>
          </div>}
          <article className={'article' + (compare ? ' comparison' : '')}>
            {compare ? <>
              <div className="compare-summary">{previous && <span>직전 저장본 · {previous.version}</span>}{previous && <button disabled={busy || historyLoading || isDirty(entry) || Boolean(entry.incoming)} onClick={() => void restorePrevious()}>이 버전 복원</button>}{isDirty(entry) && <button onClick={() => act(() => { commit(resetDraft(stateRef.current, entry.source.id)); setEditing(false); setMessage('이 자료의 수정안을 원본으로 되돌렸습니다.'); })}>수정안 되돌리기</button>}</div>
              <div className="compare-grid">{[{ title: previous ? '직전 저장본' : '원본', content: previous?.content ?? entry.source.content }, { title: previous ? '현재' : '수정안', content: entry.draft }].map(column => <section key={column.title}><h2 className="compare-label">{column.title}</h2>{column.content.name && <h3>{column.content.name}</h3>}{column.content.description && <p>{column.content.description}</p>}{column.content.applicability && Object.values(column.content.applicability).some(values => values.length) && <dl>{Object.entries(column.content.applicability).map(([key, values]) => <Fragment key={key}><dt>{{activities:'활동',targets:'대상',topics:'분야',paths:'경로'}[key as 'activities'|'targets'|'topics'|'paths']}</dt><dd>{values.join(', ') || '—'}</dd></Fragment>)}</dl>}<div className="prose"><Markdown text={column.content.body} title={column.content.name} /></div></section>)}</div>
            </> : <>
              {editing ? <section className="document-editor" aria-label="문서 편집">
                <fieldset disabled={!canEdit}>
                  {entry.draft.name !== undefined && <label>이름<input aria-invalid={Boolean(contentIssue(entry.draft))} aria-describedby={contentIssue(entry.draft) ? 'name-issue' : undefined} value={entry.draft.name} onChange={e => act(() => editContent({ ...entry.draft, name: e.target.value }))} /></label>}
                  {contentIssue(entry.draft) && <p id="name-issue" role="status">{contentIssue(entry.draft)}</p>}
                  {entry.draft.description !== undefined && <label>적용 설명<textarea rows={3} value={entry.draft.description} onChange={e => act(() => editContent({ ...entry.draft, description: e.target.value }))} /></label>}
                  <label htmlFor="document-text">본문</label>
                  <textarea id="document-text" spellCheck={false} value={entry.draft.body} onChange={e => act(() => editContent({ ...entry.draft, body: e.target.value }))} />
                  {entry.draft.applicability && <details className="metadata-editor"><summary><ChevronDown className="summary-chevron" aria-hidden="true" />적용 범위</summary>
                    {(['activities','targets','topics','paths'] as const).map((key, index) => <label key={key}>{['활동','대상','분야','프로젝트 내 경로'][index]}<input value={entry.draft.applicability![key].join(', ')} onChange={e => act(() => editContent({ ...entry.draft, applicability: { ...entry.draft.applicability!, [key]: e.target.value.split(',').map(v => v.trim()).filter(Boolean) } }))} /></label>)}
                  </details>}
                </fieldset>
              </section> : <InlineDocument key={entry.source.id} body={entry.draft.body} title={documentTitle} editable={Boolean(canEdit)} onChange={(body, expected) => {
                try {
                  const current = stateRef.current.entries.find(e => e.source.id === entry.source.id);
                  if (!current || current.source.id !== stateRef.current.selectedId) return false;
                  const nextBody = replaceBodyIfCurrent(current.draft.body, expected, body);
                  commit(updateDraft(stateRef.current, current.source.id, { ...current.draft, body: nextBody }, current.source.version, current.draftId));
                  setMessage(''); return true;
                } catch (error) { setMessage(error instanceof Error ? error.message : '수정안을 확인해 주세요.'); return false; }
              }} />}
            </>}
          </article>
        </>}
      </main>
    </div>
    {navigationOpen && <Modal titleId="navigation-title" className="navigation-dialog" close={() => setNavigationOpen(false)}>
      <div className="navigation-header">
        <div className="dialog-head"><h2 id="navigation-title">Workspace</h2><IconButton label="탐색 닫기" onClick={() => setNavigationOpen(false)}><X aria-hidden="true" /></IconButton></div>
        <div className="navigation-tabs"><button aria-pressed={navigationTab !== 'toc'} onClick={() => setNavigationTab('sources')}>자료</button><button aria-pressed={navigationTab === 'toc'} disabled={!entry} onClick={() => setNavigationTab('toc')}>목차</button></div>
        {navigationTab === 'projects' ? <div className="navigation-search">
          <button className="project-back" aria-label="현재 자료로 돌아가기" onClick={() => setNavigationTab('sources')}><ArrowLeft aria-hidden="true" />자료 범위</button>
          <label className="search-field"><Search aria-hidden="true" /><input type="search" aria-label="Sense·프로젝트 찾기" placeholder="Sense·프로젝트 찾기" autoFocus value={projectQuery} onChange={e => setProjectQuery(e.target.value)} /></label>
        </div> : navigationTab === 'sources' ? <div className="navigation-search">
          <button className="project-picker" aria-label={`자료 범위 변경: ${browseGroup?.title ?? 'Sense'}`} onClick={() => { setNavigationTab('projects'); setProjectQuery(''); }}><span>{browseGroup?.title ?? 'Sense'}</span><ChevronDown aria-hidden="true" /></button>
          <label className="search-field"><Search aria-hidden="true" /><input type="search" aria-label={`${browseGroup?.title ?? 'Sense'} 자료 검색`} placeholder="자료 검색" value={query} onChange={e => setQuery(e.target.value)} autoFocus /></label>
        </div> : null}
      </div>
      <div className="navigation-content">
      {navigationTab === 'projects' ? <>
        <nav className="candidate-list" aria-label="프로젝트">{projects.map(g => <button key={g.id} aria-current={g.id === groupId ? 'true' : undefined} onClick={() => { setGroupId(g.id); setQuery(''); setAvailable([]); setNavigationTab('sources'); }}><span className="candidate-title">{g.title}</span>{g.id === groupId && <Check aria-hidden="true" />}</button>)}</nav>
        {!projects.length && <p className="empty-result">결과 없음</p>}
      </> : navigationTab === 'toc' ? <nav className="toc-list" aria-label="현재 문서 목차">{sections.map(section => <button key={section.key} aria-current={activeSection === section.key ? 'location' : undefined} onClick={() => navigateSection(section.key)}>{section.title === '본문' ? documentTitle : section.title}</button>)}</nav> : <>
        <nav className="candidate-list" aria-label="자료" aria-busy={loadingCandidates}>
          {available.map(c => <button key={c.id} aria-current={c.id === workspace.selectedId ? 'page' : undefined} onClick={() => void openCandidate(c)}><span className={'candidate-title' + (c.excerpt ? ' excerpt' : '')}>{c.title}</span><span className="candidate-kind">{c.excerpt ? '발췌' : c.locator.product === 'source' ? '원자료' : c.locator.product === 'context-skill' || (c.locator.product === 'sense' && c.locator.skill) ? '스킬' : c.subtitle === '지침' || c.subtitle === 'Context' ? c.subtitle : ''}</span></button>)}
        </nav>
        {!available.length && <p className="empty-result" role="status">{loadingCandidates ? '불러오는 중' : candidateError ? '자료 조회 실패' : '결과 없음'}</p>}
        {filtered.length > 0 && <section className="open-documents"><h3>열린 자료</h3><nav className="candidate-list" aria-label="열린 자료">{filtered.map(e => <button key={e.source.id} aria-current={e.source.id === workspace.selectedId ? 'page' : undefined} onClick={() => selectSource(e.source.id)}><span className="candidate-title">{readingTitle(e.source, e.draft)}</span>{isDirty(e) && <span className="draft-dot"><Circle aria-hidden="true" /><span className="visually-hidden">미저장 수정안</span></span>}</button>)}</nav></section>}
      </>}
      {navigationTab === 'sources' && <div className="navigation-tools"><button onClick={() => { setNavigationOpen(false); setImportOpen(true); }}><FileUp aria-hidden="true" />자료 가져오기</button></div>}
      </div>
    </Modal>}
    {moreOpen && <Modal titleId="more-title" className="options-dialog" close={() => setMoreOpen(false)}>
      <div className="dialog-head"><h2 id="more-title">더 보기</h2><IconButton label="더 보기 닫기" onClick={() => setMoreOpen(false)}><X aria-hidden="true" /></IconButton></div>
      <div className="option-list">
        <a href="/manage">자료 이동·휴지통 관리</a>
        {entry && <>
          <button disabled={!canEdit} onClick={() => { setMoreOpen(false); switchView('edit'); }}><Pencil aria-hidden="true" />마크다운 편집</button>
          <button disabled={busy || !entry.source.canonical} onClick={() => { setMoreOpen(false); void refresh(); }}><RefreshCw aria-hidden="true" />다시 불러오기</button>
          {entry.source.canonical?.product === 'corpus' && <button disabled={busy || historyLoading} onClick={() => { setMoreOpen(false); void previousVersion(); }}><History aria-hidden="true" />직전 저장본 비교</button>}
          <button onClick={() => { setMoreOpen(false); setInfoOpen(true); }}><Info aria-hidden="true" />원본 정보</button>
        </>}
        {changes.length > 0 && <button onClick={() => { setMoreOpen(false); void copy(JSON.stringify({ changes }, null, 2)); }}><Copy aria-hidden="true" />모든 수정안 복사</button>}
        <button onClick={() => { setDark(!dark); setMoreOpen(false); }}>{dark ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}{dark ? '밝은 화면' : '어두운 화면'}</button>
      </div>
    </Modal>}
    {infoOpen && entry && <Modal titleId="info-title" close={() => setInfoOpen(false)}>
      <div className="dialog-head"><h2 id="info-title">원본 정보</h2><IconButton label="원본 정보 닫기" onClick={() => setInfoOpen(false)}><X aria-hidden="true" /></IconButton></div>
      <dl><dt>위치</dt><dd>{entry.source.reference}</dd><dt>버전</dt><dd>{entry.source.version}</dd>
        {entry.savedAt && <><dt>저장</dt><dd>{new Date(entry.savedAt).toLocaleString('ko-KR')}</dd></>}
        {entry.source.activation === 'new_session' && <><dt>실행 적용</dt><dd>새 실행에서 확인 필요</dd></>}
        {entry.source.links?.map((link, index) => <Fragment key={index}><dt>출처</dt><dd>{link.locator ? <button onClick={() => { setInfoOpen(false); void openCandidate({ id: link.label, title: link.label, locator: link.locator! }); }}>{link.label}</button> : link.label}</dd></Fragment>)}
      </dl>
    </Modal>}
    {importOpen && <Modal titleId="import-title" close={() => setImportOpen(false)}>
      <div className="dialog-head"><h2 id="import-title">자료 가져오기</h2><button onClick={() => setImportOpen(false)} aria-label="가져오기 닫기" className="icon-button"><X aria-hidden="true" /></button></div>

      <label htmlFor="import-text">지침 자료</label><textarea id="import-text" autoFocus rows={9} value={importText} onChange={e => setImportText(e.target.value)}  />
      <div className="actions"><button className="primary" disabled={!importText.trim()} onClick={() => act(() => importSources(JSON.parse(importText)))}>가져오기</button><label className="file-button">파일 선택<input type="file" accept=".json,application/json" onChange={async e => {
        const file = e.target.files?.[0]; if (!file) return;
        try { importSources(JSON.parse(await file.text())); } catch (error) { setMessage(error instanceof Error ? error.message : '파일을 읽지 못했습니다.'); }
      }} /></label></div>
      {message && <p role="alert">{message}</p>}
    </Modal>}
    {copyFallback && <Modal titleId="copy-title" close={() => setCopyFallback('')}><div className="dialog-head"><h2 id="copy-title">수정안 복사</h2><button className="icon-button" aria-label="복사 창 닫기" onClick={() => setCopyFallback('')}><X aria-hidden="true" /></button></div><textarea aria-label="복사할 수정안" readOnly rows={12} value={copyFallback} onFocus={e => e.target.select()} autoFocus /></Modal>}
  </>;
}
