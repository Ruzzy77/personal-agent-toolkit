"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { registerCatalogWebMcpTools } from "./webmcp.js";

const MARKS_STORAGE_KEY = "library:marks:v1";
const EMPTY_MARK = { hyped: false, starred: false, tags: [] };
const COVER_PLACEMENTS = [
  { x: -3, y: 7, r: -0.7 },
  { x: 2, y: -1, r: 0.45 },
  { x: -1, y: 4, r: -0.35 },
  { x: 3, y: 10, r: 0.8 },
  { x: -2, y: 1, r: -0.55 },
  { x: 3, y: 13, r: 0.4 },
  { x: -2, y: 5, r: -0.85 },
  { x: 2, y: -4, r: 0.5 },
  { x: -3, y: 8, r: -0.25 },
  { x: 1, y: 2, r: 0.7 },
  { x: -2, y: 12, r: -0.6 },
  { x: 3, y: 4, r: 0.3 },
  { x: -2, y: -2, r: -0.75 },
  { x: 2, y: 9, r: 0.55 },
];

function coverMaterial(index) {
  if (index === 11 || index % 7 === 0 || index % 13 === 0) return "deckled";
  return "crisp";
}

function coverSource(item) {
  return item.cover || item.sourceCover || null;
}

function displayCollection(item) {
  return item.collection === "digest" ? "요약" : item.koreanLabel;
}

function displayDate(date) {
  return date.replaceAll("-", ".");
}

function mergeOnlineItems(current, online) {
  const currentById = new Map(current.map((item) => [item.id, item]));
  const labels = {
    daily: { label: "DAILY", koreanLabel: "일간" },
    digest: { label: "DIGEST", koreanLabel: "다이제스트" },
    research: { label: "RESEARCH", koreanLabel: "연구" },
  };

  return online
    .map((issue) => {
      const previous = currentById.get(issue.id) || {};
      const collectionLabels = labels[issue.collection] || labels.daily;
      return {
        ...collectionLabels,
        ...previous,
        id: issue.id,
        collection: issue.collection,
        date: issue.date,
        publishedAt: issue.publishedAt,
        title: issue.title,
        cover: issue.coverPath || previous.cover || previous.sourceCover || null,
        readerHref: issue.canonicalPath,
        canonical: true,
        availability: "available",
      };
    })
    .sort((left, right) => right.publishedAt.localeCompare(left.publishedAt));
}

function sanitizeMarks(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};

  return Object.fromEntries(
    Object.entries(value).flatMap(([id, mark]) => {
      if (!mark || typeof mark !== "object" || Array.isArray(mark)) return [];
      const tags = Array.isArray(mark.tags)
        ? [...new Set(mark.tags.filter((tag) => typeof tag === "string").map((tag) => tag.trim()).filter(Boolean))].slice(0, 8)
        : [];
      return [[id, {
        hyped: mark.hyped === true,
        starred: mark.starred === true,
        tags,
      }]];
    }),
  );
}

function readMarks() {
  try {
    return sanitizeMarks(JSON.parse(window.localStorage.getItem(MARKS_STORAGE_KEY) || "{}"));
  } catch {
    return {};
  }
}

function writeMarks(marks) {
  try {
    window.localStorage.setItem(MARKS_STORAGE_KEY, JSON.stringify(marks));
  } catch {
    // Reactions remain usable for the current view when local storage is unavailable.
  }
}

function LibraryHeader({ count }) {
  return (
    <header aria-label={`Library, ${count} indexed`} className="archive-ticket">
      <span aria-hidden="true" className="library-wordmark">LIBRARY</span>
      <span>{count} INDEXED</span>
    </header>
  );
}

function CoverCard({ actionsOpen, eager = false, index, item, mark, onOpenTags, onToggleActions, onToggleMark }) {
  const hasMarks = mark.hyped || mark.starred || mark.tags.length > 0;
  const placement = COVER_PLACEMENTS[index % COVER_PLACEMENTS.length];
  const material = coverMaterial(index);
  const source = coverSource(item);
  const actionsId = `cover-actions-${item.id}`;
  const image = (
    <span className="cover-frame">
      {source && (
        <img
          alt=""
          className="cover-art"
          loading={eager ? "eager" : "lazy"}
          src={source}
        />
      )}
    </span>
  );

  return (
    <article
      className="cover-card"
      data-edge={material}
      data-library-issue-id={item.id}
      data-marked={hasMarks || undefined}
      data-pending={item.availability === "pending_archive" || undefined}
      data-actions-open={actionsOpen || undefined}
      style={{
        "--cover-rotation": `${placement.r}deg`,
        "--cover-shift-x": `${placement.x}px`,
        "--cover-shift-y": `${placement.y}px`,
      }}
    >
      {item.readerHref ? (
        <a
          aria-label={`${displayDate(item.date)}, ${displayCollection(item)}, ${item.title} 읽기`}
          className="cover-link"
          href={item.readerHref}
        >
          {image}
        </a>
      ) : (
        <div
          aria-disabled="true"
          aria-label={`${displayDate(item.date)}, ${item.title}, 보관 전`}
          className="cover-link"
        >
          {image}
        </div>
      )}

      <button
        aria-controls={actionsId}
        aria-expanded={actionsOpen}
        aria-label={actionsOpen ? "Hype, Star, Tag 닫기" : "Hype, Star, Tag 열기"}
        className="cover-action-handle"
        onClick={() => onToggleActions(item.id)}
        type="button"
      >
        <span aria-hidden="true" />
      </button>

      <div
        aria-label={`${item.title} 표시`}
        className="cover-actions"
        hidden={!actionsOpen}
        id={actionsId}
      >
        <button
          aria-label={mark.hyped ? "Hype 취소" : "Hype 표시"}
          aria-pressed={mark.hyped}
          data-active={mark.hyped || undefined}
          onClick={() => onToggleMark(item.id, "hyped")}
          title="Hype"
          type="button"
        >
          <img alt="" aria-hidden="true" src="/icons/library/action-hype.png" />
        </button>
        <button
          aria-label={mark.starred ? "Star 취소" : "Star 표시"}
          aria-pressed={mark.starred}
          data-active={mark.starred || undefined}
          onClick={() => onToggleMark(item.id, "starred")}
          title="Star"
          type="button"
        >
          <img alt="" aria-hidden="true" src="/icons/library/action-star.png" />
        </button>
        <button
          aria-haspopup="dialog"
          aria-label={`Tag 편집, 현재 ${mark.tags.length}개`}
          data-active={mark.tags.length > 0 || undefined}
          onClick={() => onOpenTags(item.id)}
          title="Tag"
          type="button"
        >
          <img alt="" aria-hidden="true" src="/icons/library/action-tag.png" />
        </button>
      </div>
    </article>
  );
}

function CoverFeed({ activeActionsId, items, marks, onOpenTags, onToggleActions, onToggleMark }) {
  return (
    <section aria-label="모든 발간물" className="cover-feed">
      {items.map((item, index) => (
        <CoverCard
          actionsOpen={activeActionsId === item.id}
          eager={index < 28}
          index={index}
          item={item}
          key={item.id}
          mark={marks[item.id] || EMPTY_MARK}
          onOpenTags={onOpenTags}
          onToggleActions={onToggleActions}
          onToggleMark={onToggleMark}
        />
      ))}
    </section>
  );
}

function TagDialog({ item, mark, onAddTag, onClose, onRemoveTag }) {
  const [value, setValue] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
    const closeOnEscape = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const submit = (event) => {
    event.preventDefault();
    const tag = value.trim();
    if (!tag) return;
    onAddTag(item.id, tag);
    setValue("");
  };

  return (
    <dialog
      aria-labelledby="tag-dialog-title"
      aria-modal="true"
      className="tag-dialog-backdrop"
      open
    >
      <section className="tag-dialog">
        <header className="tag-dialog-header">
          <div>
            <p>개인 분류표</p>
            <h2 id="tag-dialog-title">{item.title}</h2>
          </div>
          <button aria-label="Tag 편집 닫기" className="tag-dialog-close" onClick={onClose} type="button">
            닫기
          </button>
        </header>

        <div aria-live="polite" className="tag-list">
          {mark.tags.length === 0 ? (
            <p className="tag-empty">아직 붙인 태그가 없습니다.</p>
          ) : (
            mark.tags.map((tag) => (
              <span className="tag-chip" key={tag}>
                {tag}
                <button aria-label={`${tag} 태그 삭제`} onClick={() => onRemoveTag(item.id, tag)} type="button">
                  ×
                </button>
              </span>
            ))
          )}
        </div>

        <form className="tag-form" onSubmit={submit}>
          <label htmlFor="new-tag">새 태그</label>
          <div>
            <input
              autoComplete="off"
              id="new-tag"
              maxLength={24}
              onChange={(event) => setValue(event.target.value)}
              placeholder="예: 다시 읽기"
              ref={inputRef}
              value={value}
            />
            <button aria-label="태그 추가" disabled={!value.trim()} title="추가" type="submit">
              <img alt="" aria-hidden="true" src="/icons/library/action-add.png" />
            </button>
          </div>
        </form>
      </section>
    </dialog>
  );
}

export function App() {
  const [items, setItems] = useState([]);
  const [marks, setMarks] = useState(() => readMarks());
  const [activeActionsId, setActiveActionsId] = useState(null);
  const [tagItemId, setTagItemId] = useState(null);
  const itemsRef = useRef(items);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  useEffect(() => registerCatalogWebMcpTools({
    getItems: () => itemsRef.current,
  }), []);

  useEffect(() => writeMarks(marks), [marks]);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/library/issues?limit=200", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((result) => {
        setItems((current) => mergeOnlineItems(current, Array.isArray(result.issues) ? result.issues : []));
      })
      .catch((error) => {
        if (error.name !== "AbortError") console.warn("Online Library index is unavailable", error);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!activeActionsId) return undefined;

    const closeFromOutside = (event) => {
      const openCard = document.querySelector('.cover-card[data-actions-open="true"]');
      if (!openCard?.contains(event.target)) setActiveActionsId(null);
    };
    const closeOnEscape = (event) => {
      if (event.key !== "Escape") return;
      const openCard = document.querySelector('.cover-card[data-actions-open="true"]');
      setActiveActionsId(null);
      openCard?.querySelector(".cover-action-handle")?.focus();
    };
    const closeOnScroll = () => setActiveActionsId(null);

    document.addEventListener("pointerdown", closeFromOutside);
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("scroll", closeOnScroll, true);
    return () => {
      document.removeEventListener("pointerdown", closeFromOutside);
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("scroll", closeOnScroll, true);
    };
  }, [activeActionsId]);

  const activeTagItem = useMemo(
    () => items.find((item) => item.id === tagItemId) || null,
    [items, tagItemId],
  );

  const closeTags = useCallback(() => setTagItemId(null), []);

  const toggleActions = useCallback((id) => {
    setActiveActionsId((current) => current === id ? null : id);
  }, []);

  const openTags = useCallback((id) => {
    setActiveActionsId(null);
    setTagItemId(id);
  }, []);

  const toggleMark = useCallback((id, field) => {
    setMarks((current) => {
      const previous = current[id] || EMPTY_MARK;
      return {
        ...current,
        [id]: { ...previous, [field]: !previous[field] },
      };
    });
  }, []);

  const addTag = useCallback((id, nextTag) => {
    setMarks((current) => {
      const previous = current[id] || EMPTY_MARK;
      const tags = [...new Set([...previous.tags, nextTag])].slice(0, 8);
      return { ...current, [id]: { ...previous, tags } };
    });
  }, []);

  const removeTag = useCallback((id, removedTag) => {
    setMarks((current) => {
      const previous = current[id] || EMPTY_MARK;
      return {
        ...current,
        [id]: { ...previous, tags: previous.tags.filter((tag) => tag !== removedTag) },
      };
    });
  }, []);

  return (
    <main className="library-app">
      <LibraryHeader count={items.length} />
      <CoverFeed
        activeActionsId={activeActionsId}
        items={items}
        marks={marks}
        onOpenTags={openTags}
        onToggleActions={toggleActions}
        onToggleMark={toggleMark}
      />
      {activeTagItem && (
        <TagDialog
          item={activeTagItem}
          mark={marks[activeTagItem.id] || EMPTY_MARK}
          onAddTag={addTag}
          onClose={closeTags}
          onRemoveTag={removeTag}
        />
      )}
    </main>
  );
}
