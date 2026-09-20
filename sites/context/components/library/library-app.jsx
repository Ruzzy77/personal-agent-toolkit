"use client";
import Link from "next/link";
import { ownerFetch } from "@/lib/owner-client";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { BookOpen, Flame, Grid2X2, List, Plus, Search, Settings2, Star, Tags, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActionMenu, FieldSelect, IconButton, Menu, UiInput } from "@/app/ui";
import { catalogTags, filterIssues, issueTags } from "@/lib/library-catalog";
import { registerCatalogWebMcpTools } from "./webmcp.js";

const MARKS_STORAGE_KEY = "library:marks:v1";
const EMPTY_MARK = { hyped: false, starred: false, tags: [] };
const displayDate = date => String(date || "").replaceAll("-", ".");

function readMarks() {
  try {
    const value = JSON.parse(window.localStorage.getItem(MARKS_STORAGE_KEY) || "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).flatMap(([id, mark]) => {
      if (!mark || typeof mark !== "object" || Array.isArray(mark)) return [];
      return [[id, { hyped: mark.hyped === true, starred: mark.starred === true,
        tags: Array.isArray(mark.tags) ? [...new Set(mark.tags.filter(tag => typeof tag === "string").map(tag => tag.trim()).filter(Boolean))].slice(0, 8) : [] }]];
    }));
  } catch { return {}; }
}

function TagDialog({ item, mark, onAddTag, onClose, onRemoveTag }) {
  const [value, setValue] = useState("");
  const dialog = useRef(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  return <dialog ref={dialog} className="library-tag-dialog" aria-labelledby="tag-dialog-title" onCancel={onClose} onClick={event => { if (event.target === dialog.current) onClose(); }}>
    <header className="su-toolbar">
      <h2 id="tag-dialog-title">태그 편집</h2>
      <IconButton label="태그 편집 닫기" onClick={onClose}><X size="1em" /></IconButton>
    </header>
    <p className="library-tag-title">{item.title}</p>
    <div className="library-tag-values" aria-live="polite">
      {mark.tags.length ? mark.tags.map(tag => <span key={tag}>{tag}<IconButton label={tag + " 태그 삭제"} onClick={() => onRemoveTag(item.id, tag)}><X size="1em" /></IconButton></span>) : <p>추가한 태그가 없습니다.</p>}
    </div>
    <form onSubmit={event => { event.preventDefault(); if (value.trim()) { onAddTag(item.id, value.trim()); setValue(""); } }}>
      <label htmlFor="library-new-tag">새 태그</label>
      <div className="su-row"><UiInput id="library-new-tag" autoFocus autoComplete="off" maxLength={24} value={value} onChange={event => setValue(event.target.value)} /><Button color="primary" variant="solid" type="submit" disabled={!value.trim() || mark.tags.length >= 8}><Plus size="1em" />추가</Button></div>
    </form>
  </dialog>;
}

function Publication({ item, mark, eager, onToggleMark, onOpenTags }) {
  const source = item.cover || item.sourceCover;
  const tags = issueTags(item, mark);
  return <article className="publication" data-library-issue-id={item.id}>
    <Link className="cover-link publication-cover" href={item.readerHref} aria-label={displayDate(item.date) + ", " + tags.join(", ") + ", " + item.title + " 읽기"}>
      {source ? <img src={source} alt="" loading={eager ? "eager" : "lazy"} /> : <span className="publication-no-cover"><BookOpen size={32} /><span>{item.title}</span></span>}
    </Link>
    <div className="publication-info">
      <div className="publication-heading">
        <h2><Link href={item.readerHref}>{item.title}</Link></h2>
        <div className="publication-actions">
          <IconButton label={mark.starred ? item.title + " 즐겨찾기 해제" : item.title + " 즐겨찾기"} aria-pressed={mark.starred} onClick={() => onToggleMark(item.id, "starred")}><Star size="1em" fill={mark.starred ? "currentColor" : "none"} /></IconButton>
          <ActionMenu label={item.title + " 도구"}>
            <Menu.Item onSelect={() => onToggleMark(item.id, "hyped")}><Flame size="1em" />{mark.hyped ? "Hype 해제" : "Hype 표시"}</Menu.Item>
            <Menu.Item onSelect={() => onOpenTags(item.id)}><Tags size="1em" />태그 편집</Menu.Item>
          </ActionMenu>
        </div>
      </div>
      <div className="publication-meta"><span>{tags.join(" · ")}</span><time dateTime={item.date}>{displayDate(item.date)}</time>{mark.hyped && <Flame size={14} aria-label="Hype 표시됨" />}</div>
    </div>
  </article>;
}

export function App() {
  const [items, setItems] = useState([]);
  const [marks, setMarks] = useState(readMarks);
  const [tagItemId, setTagItemId] = useState(null);
  const [query, setQuery] = useState(""), [tag, setTag] = useState(""), [view, setView] = useState("grid");
  const [loading, setLoading] = useState(true), [error, setError] = useState(""), [retry, setRetry] = useState(0);
  const itemsRef = useRef(items);
  useEffect(() => { itemsRef.current = items; }, [items]);
  useEffect(() => registerCatalogWebMcpTools({ getItems: () => itemsRef.current, showIssue: () => { setQuery(""); setTag(""); } }), []);
  useEffect(() => { try { window.localStorage.setItem(MARKS_STORAGE_KEY, JSON.stringify(marks)); } catch { /* Keep current-view marks when storage is unavailable. */ } }, [marks]);
  useEffect(() => {
    const controller = new AbortController();
    ownerFetch("/api/library/issues?limit=200", { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(); return response.json(); })
      .then(result => {
        setItems((Array.isArray(result.issues) ? result.issues : []).map(issue => ({
          ...issue, cover: issue.coverPath || null, readerHref: issue.canonicalPath,
        })).sort((left, right) => right.publishedAt.localeCompare(left.publishedAt)));
        setLoading(false);
      })
      .catch(error => { if (error.name !== "AbortError") { setError("발간물을 불러오지 못했습니다."); setLoading(false); } });
    return () => controller.abort();
  }, [retry]);

  const tags = useMemo(() => catalogTags(items, marks), [items, marks]);
  const visible = useMemo(() => filterIssues(items, marks, query, tag), [items, marks, query, tag]);
  const activeTagItem = items.find(item => item.id === tagItemId);
  const closeTags = useCallback(() => setTagItemId(null), []);
  const toggleMark = useCallback((id, field) => setMarks(current => {
    const previous = current[id] || EMPTY_MARK;
    return { ...current, [id]: { ...previous, [field]: !previous[field] } };
  }), []);
  const addTag = useCallback((id, nextTag) => setMarks(current => {
    const previous = current[id] || EMPTY_MARK;
    return { ...current, [id]: { ...previous, tags: [...new Set([...previous.tags, nextTag])].slice(0, 8) } };
  }), []);
  const removeTag = useCallback((id, removedTag) => setMarks(current => {
    const previous = current[id] || EMPTY_MARK;
    return { ...current, [id]: { ...previous, tags: previous.tags.filter(value => value !== removedTag) } };
  }), []);

  return <main className="toolkit-page library-workspace" id="main-content">
    <header className="toolkit-page-header">
      <h1>Library</h1>
      <div className="toolkit-page-tools">{!loading && !error && <span className="toolkit-page-count">{items.length}개의 발간물</span>}<Link className="file-project-link" href="/manage/library"><Settings2 size={18} />자료 관리</Link></div>
    </header>
    <div className="library-toolbar">
      <div className="library-search"><UiInput type="search" aria-label="발간물 찾기" placeholder="발간물 찾기" startAdornment={<Search size="1em" />} value={query} onChange={event => setQuery(event.target.value)} /></div>
      <div className="library-tag-filter"><FieldSelect aria-label="태그 필터" value={tag} onChange={option => setTag(option.value)}><option value="">태그</option>{tags.map(value => <option key={value} value={value}>{value}</option>)}</FieldSelect></div>
      {tag && <IconButton label="태그 필터 해제" onClick={() => setTag("")}><X size="1em" /></IconButton>}
      <div className="library-view" role="group" aria-label="보기 방식">
        <IconButton label="표지 보기" aria-pressed={view === "grid"} selected={view === "grid"} onClick={() => setView("grid")}><Grid2X2 size="1em" /></IconButton>
        <IconButton label="목록 보기" aria-pressed={view === "list"} selected={view === "list"} onClick={() => setView("list")}><List size="1em" /></IconButton>
      </div>
    </div>
    {loading ? <p className="toolkit-page-state" role="status">발간물을 불러오는 중…</p> : error ? <div className="toolkit-page-state" role="alert">{error}<Button color="primary" variant="outline" onClick={() => { setLoading(true); setError(""); setRetry(value => value + 1); }}>다시 시도</Button></div> : <>
      {(query || tag) && <p className="library-result-count" role="status">{visible.length}개의 발간물</p>}
      {visible.length ? <section className={"publication-gallery is-" + view} aria-label="발간물">{visible.map((item, index) => <Publication key={item.id} item={item} mark={marks[item.id] || EMPTY_MARK} eager={index < 8} onToggleMark={toggleMark} onOpenTags={setTagItemId} />)}</section> : <div className="toolkit-page-state"><p>{items.length ? "조건에 맞는 발간물이 없습니다." : "아직 발간물이 없습니다."}</p>{(query || tag) && <Button variant="outline" color="primary" onClick={() => { setQuery(""); setTag(""); }}>검색 초기화</Button>}</div>}
    </>}
    {activeTagItem && <TagDialog item={activeTagItem} mark={marks[activeTagItem.id] || EMPTY_MARK} onAddTag={addTag} onClose={closeTags} onRemoveTag={removeTag} />}
  </main>;
}
