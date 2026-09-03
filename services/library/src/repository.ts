import type {
  LibraryCollection,
  LibraryIssue,
  LibraryIssueSummary,
  LibraryMutationResult,
} from "./types";
import { LibraryError } from "./errors";

interface DocumentRow {
  id: string;
  collection: LibraryCollection;
  date: string;
  published_at: string;
  title: string;
  references_json: string;
  canonical_path: string;
  text_content: string;
  source_html: string;
  cover_path: string | null;
  version: number;
  updated_at: string;
}

type SummaryRow = Pick<
  DocumentRow,
  | "id"
  | "collection"
  | "date"
  | "published_at"
  | "title"
  | "canonical_path"
  | "cover_path"
  | "version"
  | "updated_at"
>;

export interface CreateIssueInput {
  id: string;
  collection?: LibraryCollection;
  date?: string;
  publishedAt?: string;
  title?: string;
  references?: string[];
  canonicalPath?: string;
  sourceHtml: string;
  coverPath?: string;
}

export interface UpdateIssueInput {
  sourceHtml: string;
  expectedVersion: number;
  coverPath?: string;
  references?: string[];
}

export interface UpdateIssueFragmentsInput {
  title: string;
  leadText?: string;
  articleHtml: string;
  expectedVersion: number;
}

export interface ImportIssueInput extends IssueContent {
  updatedAt: string;
}

type IssueContent = Omit<LibraryIssue, "updatedAt" | "version">;

const COLLECTIONS = new Set<LibraryCollection>(["daily", "digest", "research"]);
const ISSUE_ID = /^(daily|digest|research):(\d{4}-\d{2}-\d{2})(?::([01]\d|2[0-3]))?$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const MAX_SOURCE_HTML = 2_000_000;
const LIBRARY_TEMPLATE = "saegin-reader-v1";
const PUBLICATION_LABELS: Record<LibraryCollection, string> = {
  daily: "Daily",
  digest: "Research Digest",
  research: "Research",
};
const META_CLASS_NAMES = new Set(["masthead", "issue-line", "issue-bar", "folio"]);
const PUBLICATION_CLASS_NAMES = new Set(["kicker", "eyebrow", "series"]);
const ARTICLE_CLASS_ALIASES = new Map<string, string>([
  ["boundary", "reader-callout"],
  ["callout", "reader-callout"],
  ["check", "reader-callout"],
  ["closing", "reader-callout"],
  ["conclusion", "reader-callout"],
  ["equation", "reader-callout"],
  ["finding", "reader-callout"],
  ["judgment", "reader-callout"],
  ["key", "reader-key-sentence"],
  ["maxim", "reader-callout"],
  ["measure", "reader-callout"],
  ["metric", "num"],
  ["moment", "reader-callout"],
  ["note", "reader-callout"],
  ["split-note", "reader-callout"],
  ["state-line", "reader-callout"],
  ["status", "reader-callout"],
  ["turn", "reader-callout"],
  ["sources", "references"],
]);
const ARTICLE_CLASSES = new Set<string>([
  "diagram-scroll",
  "footnote",
  "num",
  "reader-callout",
  "reader-key-sentence",
  "reader-quote",
  "references",
  "source",
  "source-label",
  "sr-only",
  "table-scroll",
  "visual-scroll",
]);
function prepared(db: D1Database, sql: string, values: unknown[] = []) {
  return db.prepare(sql).bind(...values);
}

function decodeEntities(value: string): string {
  return value
    .replaceAll("&nbsp;", " ")
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCodePoint(Number.parseInt(code, 16)));
}

function textFromHtml(value: string): string {
  return decodeEntities(
    value
      .replace(/<script\b[\s\S]*?<\/script>/gi, "")
      .replace(/<style\b[\s\S]*?<\/style>/gi, "")
      .replace(/<nav\b[\s\S]*?<\/nav>/gi, "")
      .replace(/<img\b[^>]*\balt="([^"]*)"[^>]*>/gi, "\n[삽화: $1]\n")
      .replace(/<\/(?:p|h1|h2|h3|figure|header|article|li|blockquote)>/gi, "\n\n")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<[^>]+>/g, ""),
  )
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function readableText(sourceHtml: string): string {
  const header = sourceHtml.match(/<header\b[\s\S]*?<\/header>/i)?.[0] ?? "";
  const article = sourceHtml.match(/<article\b[\s\S]*?<\/article>/i)?.[0] ?? "";
  return textFromHtml(`${header}\n${article}`);
}

function titleFromHtml(sourceHtml: string, fallback = "제목 없음"): string {
  const heading = sourceHtml.match(/<h1\b[^>]*>([\s\S]*?)<\/h1>/i)?.[1];
  return heading ? textFromHtml(heading) || fallback : fallback;
}

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function elementWithClass(
  sourceHtml: string,
  classNames: ReadonlySet<string>,
): string {
  const openings = /<([a-z][a-z0-9:-]*)\b([^>]*)>/gi;
  for (const match of sourceHtml.matchAll(openings)) {
    const classValue = (match[2] ?? "").match(/\bclass=(['"])([^'"]*)\1/i)?.[2];
    const classes = classValue?.split(/\s+/).filter(Boolean) ?? [];
    if (!classes.some((name) => classNames.has(name))) continue;
    const close = new RegExp(`<\\/${match[1]}>`, "i");
    const tail = sourceHtml.slice((match.index ?? 0) + match[0].length);
    const closeMatch = tail.match(close);
    if (!closeMatch) return "";
    const end = (match.index ?? 0) + match[0].length + (closeMatch.index ?? 0) + closeMatch[0].length;
    return sourceHtml.slice(match.index ?? 0, end);
  }
  return "";
}

function textInsideFirstTag(sourceHtml: string, tagName: string): string {
  const match = sourceHtml.match(new RegExp(`<${tagName}\\b[^>]*>([\\s\\S]*?)<\\/${tagName}>`, "i"));
  return match?.[1] ? textFromHtml(match[1]) : "";
}

function leadFromHtml(sourceHtml: string): string {
  return textFromHtml(elementWithClass(sourceHtml, new Set(["lead", "standfirst"])));
}

function articleFromHtml(sourceHtml: string): string {
  return sourceHtml.match(/<article\b[^>]*>([\s\S]*?)<\/article>/i)?.[1] ?? "";
}

function normalizeArticleClasses(articleHtml: string): string {
  return articleHtml.replace(/\sclass=(['"])([^'"]*)\1/gi, (
    _matched: string,
    quote: string,
    value: string,
  ) => {
    const classes = value
      .split(/\s+/)
      .filter(Boolean)
      .map((name) => ARTICLE_CLASS_ALIASES.get(name) ?? name)
      .filter((name) => ARTICLE_CLASSES.has(name));
    const unique = [...new Set(classes)];
    return unique.length ? ` class=${quote}${unique.join(" ")}${quote}` : "";
  });
}

function normalizeArticleHtml(articleHtml: string): string {
  const cleaned = cleanArticleHtml(articleHtml)
    .replace(/\sstyle=(['"])[\s\S]*?\1/gi, "");
  if (/\sstyle\s*=/i.test(cleaned)) throw new Error("article_style_not_allowed");
  return normalizeArticleClasses(cleaned).trim();
}

function issueMetaFromHtml(
  sourceHtml: string,
  collection: LibraryCollection,
): string {
  const meta = elementWithClass(sourceHtml, META_CLASS_NAMES);
  const publicationCandidate = textInsideFirstTag(meta, "span")
    || textFromHtml(elementWithClass(sourceHtml, PUBLICATION_CLASS_NAMES));
  const sequence = collection === "digest"
    ? publicationCandidate.match(/Research\s+Digest\s*·\s*(\d{1,3})/i)?.[1]
    : null;
  return sequence
    ? `${PUBLICATION_LABELS[collection]} · ${sequence}`
    : PUBLICATION_LABELS[collection];
}

function issueTimeSlot(id: string): string | null {
  return id.match(/:(\d{2})$/)?.[1] ?? null;
}

function publicationLabel(
  sourceHtml: string,
  { id, collection }: Pick<LibraryIssue, "id" | "collection">,
): string {
  const base = issueMetaFromHtml(sourceHtml, collection);
  const timeSlot = issueTimeSlot(id);
  return timeSlot ? `${base} · ${timeSlot}:00` : base;
}

function dateLabel(date: string): string {
  const match = String(date ?? "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return String(date ?? "");
  const months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  return `${match[3]} ${months[Number(match[2]) - 1] ?? ""} ${match[1]}`;
}

function usesLibraryTemplate(sourceHtml: string): boolean {
  return new RegExp(`\\bdata-library-template=["']${LIBRARY_TEMPLATE}["']`, "i").test(sourceHtml);
}

function canonicalizeIssueSource(
  sourceHtml: string,
  { id, collection, date }: Pick<LibraryIssue, "id" | "collection" | "date">,
): string {
  const source = validateSourceHtml(sourceHtml);
  const title = titleFromHtml(source);
  const lead = leadFromHtml(source);
  if (!lead) throw new Error("lead_not_found");
  const articleHtml = normalizeArticleHtml(articleFromHtml(source));
  if (!articleHtml) throw new Error("article_not_found");
  const publication = publicationLabel(source, { id, collection });

  return `<!doctype html>
<html lang="ko" data-library-template="${LIBRARY_TEMPLATE}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <meta name="robots" content="noindex, nofollow, noarchive">
  <meta name="referrer" content="no-referrer">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; font-src 'self'">
  <title>${escapeHtml(`${title} · ${PUBLICATION_LABELS[collection]}`)}</title>
  <link rel="stylesheet" href="/reader.css">
  <script src="/reader.js" defer></script>
</head>
<body>
  <main>
    <nav aria-label="Library 홈" data-library-return>
      <a aria-label="Library 홈" href="/#/">LIBRARY</a>
    </nav>
    <div class="masthead">
      <span class="reader-publication">${escapeHtml(publication)}</span>
      <time class="reader-date" datetime="${escapeHtml(date)}">${escapeHtml(dateLabel(date))}</time>
    </div>
    <header>
      <h1>${escapeHtml(title)}</h1>
      <p class="lead">${escapeHtml(lead)}</p>
    </header>
    <article>${articleHtml}</article>
  </main>
</body>
</html>`;
}

function assertSafeMarkup(value: string): void {
  if (/\son[a-z]+\s*=/i.test(value)) throw new Error("event_handler_not_allowed");
  if (/\bjavascript\s*:/i.test(value)) throw new Error("javascript_url_not_allowed");
  if (/<(?:iframe|object|embed)\b/i.test(value)) throw new Error("embedded_content_not_allowed");
  const scripts = [...value.matchAll(/<script\b[^>]*>[\s\S]*?<\/script>/gi)];
  if (scripts.some(([script]) => !/^<script\s+src=["']\/reader\.js["']\s+defer><\/script>$/i.test(script.trim()))) {
    throw new Error("script_not_allowed");
  }
}

export function validateSourceHtml(value: unknown): string {
  if (typeof value !== "string" || value.length < 1 || value.length > MAX_SOURCE_HTML) {
    throw new Error("invalid_source_size");
  }
  if (!/<!doctype html>/i.test(value) || !/<h1\b/i.test(value) || !/<article\b/i.test(value)) {
    throw new Error("incomplete_source_html");
  }
  assertSafeMarkup(value);
  return value;
}

function parseReferences(value: string): string[] {
  try {
    const parsed = JSON.parse(value || "[]");
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
  } catch {
    return [];
  }
}

function rowToIssue(row: DocumentRow | null): LibraryIssue | null {
  if (!row) return null;
  return {
    id: row.id,
    collection: row.collection,
    date: row.date,
    publishedAt: row.published_at,
    title: row.title,
    references: parseReferences(row.references_json),
    canonicalPath: row.canonical_path,
    text: row.text_content,
    sourceHtml: row.source_html,
    coverPath: row.cover_path ?? null,
    version: row.version,
    updatedAt: row.updated_at,
  };
}

function summaryFromRow(row: SummaryRow): LibraryIssueSummary {
  return {
    id: row.id,
    collection: row.collection,
    date: row.date,
    publishedAt: row.published_at,
    title: row.title,
    canonicalPath: row.canonical_path,
    coverPath: row.cover_path ?? null,
    version: row.version,
    updatedAt: row.updated_at,
  };
}

export function canonicalPathFor(
  collection: LibraryCollection,
  date: string,
  timeSlot: string | null = null,
): string {
  const basePath = collection === "research"
    ? `/editions/research/brief/issues/${date}`
    : `/editions/${collection}/issues/${date}`;
  return timeSlot ? `${basePath}/${timeSlot}` : basePath;
}

export function normalizeCanonicalPath(pathname: string): string {
  if (pathname.endsWith(".html")) return pathname.slice(0, -5);
  return pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
}

export async function listIssues(
  db: D1Database,
  {
    collection = null,
    limit = 100,
  }: { collection?: LibraryCollection | null; limit?: number } = {},
): Promise<LibraryIssueSummary[]> {
  const boundedLimit = Math.max(1, Math.min(200, Number(limit) || 100));
  const statement = collection
    ? prepared(db, `SELECT id, collection, date, published_at, title,
        canonical_path, cover_path, version, updated_at
        FROM documents WHERE collection = ? ORDER BY published_at DESC LIMIT ?`, [collection, boundedLimit])
    : prepared(db, `SELECT id, collection, date, published_at, title,
        canonical_path, cover_path, version, updated_at
        FROM documents ORDER BY published_at DESC LIMIT ?`, [boundedLimit]);
  const result = await statement.all<SummaryRow>();
  return (result.results ?? []).map(summaryFromRow);
}

export async function readIssue(
  db: D1Database,
  id: string,
): Promise<LibraryIssue | null> {
  const row = await prepared(db, "SELECT * FROM documents WHERE id = ?", [id])
    .first<DocumentRow>();
  return rowToIssue(row);
}

export async function readIssueByPath(
  db: D1Database,
  pathname: string,
): Promise<LibraryIssue | null> {
  const row = await prepared(db, "SELECT * FROM documents WHERE canonical_path = ?", [normalizeCanonicalPath(pathname)])
    .first<DocumentRow>();
  return rowToIssue(row);
}

function validateIssueInput(input: CreateIssueInput): IssueContent {
  const match = typeof input.id === "string" ? ISSUE_ID.exec(input.id) : null;
  if (!match) throw new Error("invalid_issue_id");
  const idCollection = match[1] as LibraryCollection;
  const idDate = match[2] as string;
  const idTimeSlot = match[3] ?? null;
  const collection = input.collection ?? idCollection;
  const date = input.date ?? idDate;
  if (!COLLECTIONS.has(collection) || collection !== idCollection || !DATE.test(date) || date !== idDate) {
    throw new Error("invalid_issue_identity");
  }
  const canonicalPath = input.canonicalPath ?? canonicalPathFor(collection, date, idTimeSlot);
  if (canonicalPath !== canonicalPathFor(collection, date, idTimeSlot)) throw new Error("invalid_canonical_path");
  const publishedAt = typeof input.publishedAt === "string" && input.publishedAt ? input.publishedAt : new Date().toISOString();
  const sourceHtml = canonicalizeIssueSource(input.sourceHtml, {
    id: input.id,
    collection,
    date,
  });
  const references = validateReferences(input.references ?? []);
  const title = titleFromHtml(sourceHtml, input.title || "제목 없음");
  return {
    id: input.id,
    collection,
    date,
    publishedAt,
    title,
    references,
    canonicalPath,
    text: readableText(sourceHtml),
    sourceHtml,
    coverPath: typeof input.coverPath === "string" && input.coverPath ? input.coverPath : null,
  };
}

export async function createIssue(
  db: D1Database,
  input: CreateIssueInput,
): Promise<LibraryMutationResult> {
  const issue = validateIssueInput(input);
  const existing = await prepared(db, "SELECT id FROM documents WHERE id = ?", [issue.id])
    .first<{ id: string }>();
  if (existing) {
    throw new LibraryError("already_exists", "the Library issue already exists", 409, {
      id: issue.id,
    });
  }
  const updatedAt = new Date().toISOString();
  await prepared(db, `INSERT INTO documents (
      id, collection, date, published_at, title, references_json,
      canonical_path, text_content, source_html, cover_path, version, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)`, [
    issue.id,
    issue.collection,
    issue.date,
    issue.publishedAt,
    issue.title,
    JSON.stringify(issue.references),
    issue.canonicalPath,
    issue.text,
    issue.sourceHtml,
    issue.coverPath,
    updatedAt,
  ]).run();
  return { status: "created", issue: { ...issue, version: 1, updatedAt } };
}

export async function importIssue(
  db: D1Database,
  input: ImportIssueInput,
): Promise<LibraryMutationResult> {
  const match = ISSUE_ID.exec(input.id);
  const timeSlot = match?.[3] ?? null;
  if (
    !match
    || input.collection !== match[1]
    || input.date !== match[2]
    || input.canonicalPath
      !== canonicalPathFor(input.collection, input.date, timeSlot)
  ) {
    throw new LibraryError(
      "invalid_issue_identity",
      "imported issue identity is inconsistent",
    );
  }
  const issue: IssueContent = {
    ...input,
    title: input.title.trim(),
    references: validateReferences(input.references),
    sourceHtml: validateSourceHtml(input.sourceHtml),
    coverPath: input.coverPath || null,
  };
  if (!issue.title || !input.updatedAt) {
    throw new LibraryError("invalid_import", "imported issue fields are invalid");
  }

  const existing = await readIssue(db, issue.id);
  if (existing) {
    const unchanged = existing.collection === issue.collection
      && existing.date === issue.date
      && existing.publishedAt === issue.publishedAt
      && existing.title === issue.title
      && JSON.stringify(existing.references) === JSON.stringify(issue.references)
      && existing.canonicalPath === issue.canonicalPath
      && existing.text === issue.text
      && existing.sourceHtml === issue.sourceHtml
      && existing.coverPath === issue.coverPath
      && existing.updatedAt === input.updatedAt;
    if (unchanged) {
      return { status: "unchanged", issue: existing };
    }
    throw new LibraryError(
      "import_conflict",
      "an existing Library issue differs from the import",
      409,
      { id: issue.id },
    );
  }

  await prepared(db, `INSERT INTO documents (
      id, collection, date, published_at, title, references_json,
      canonical_path, text_content, source_html, cover_path, version, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)`, [
    issue.id,
    issue.collection,
    issue.date,
    issue.publishedAt,
    issue.title,
    JSON.stringify(issue.references),
    issue.canonicalPath,
    issue.text,
    issue.sourceHtml,
    issue.coverPath,
    input.updatedAt,
  ]).run();
  return {
    status: "created",
    issue: { ...issue, version: 1, updatedAt: input.updatedAt },
  };
}

function validateReferences(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 100) throw new Error("invalid_references");
  if (value.some((item) => typeof item !== "string" || item.length > 1000)) {
    throw new Error("invalid_references");
  }
  return [...value] as string[];
}

export async function updateIssueSource(
  db: D1Database,
  id: string,
  input: UpdateIssueInput,
): Promise<LibraryMutationResult> {
  const current = await readIssue(db, id);
  if (!current) {
    throw new LibraryError("not_found", "the Library issue was not found", 404, { id });
  }
  if (current.version !== input.expectedVersion) {
    throw new LibraryError(
      "version_conflict",
      "the Library issue changed after it was read",
      409,
      { id, expectedVersion: input.expectedVersion, currentVersion: current.version },
    );
  }
  const candidateSource = validateSourceHtml(input.sourceHtml);
  const nextSource = usesLibraryTemplate(current.sourceHtml)
    ? canonicalizeIssueSource(candidateSource, current)
    : candidateSource;
  const title = titleFromHtml(nextSource, current.title);
  const text = readableText(nextSource);
  const nextCover = typeof input.coverPath === "string" && input.coverPath
    ? input.coverPath
    : current.coverPath;
  const nextReferences = input.references === undefined
    ? current.references
    : validateReferences(input.references);
  if (
    nextSource === current.sourceHtml
    && nextCover === current.coverPath
    && JSON.stringify(nextReferences) === JSON.stringify(current.references)
  ) {
    return { status: "unchanged", issue: current };
  }
  const updatedAt = new Date().toISOString();
  const nextVersion = current.version + 1;
  const result = await prepared(db, `UPDATE documents
      SET title = ?, text_content = ?, source_html = ?, cover_path = ?, references_json = ?,
          version = ?, updated_at = ?
      WHERE id = ? AND version = ?`, [
    title,
    text,
    nextSource,
    nextCover,
    JSON.stringify(nextReferences),
    nextVersion,
    updatedAt,
    id,
    current.version,
  ]).run();
  if (result.meta.changes !== 1) {
    const latest = await readIssue(db, id);
    throw new LibraryError(
      "version_conflict",
      "the Library issue changed while it was being saved",
      409,
      { id, expectedVersion: current.version, currentVersion: latest?.version },
    );
  }
  return {
    status: "updated",
    issue: {
      ...current,
      title,
      text,
      sourceHtml: nextSource,
      coverPath: nextCover,
      references: nextReferences,
      version: nextVersion,
      updatedAt,
    },
  };
}

function cleanArticleHtml(value: unknown): string {
  if (typeof value !== "string" || value.length > MAX_SOURCE_HTML) throw new Error("invalid_article_html");
  assertSafeMarkup(value);
  if (/<(?:script|style|iframe|object|embed|link|meta|base|form)\b/i.test(value)) {
    throw new Error("article_markup_not_allowed");
  }
  return value;
}

function replaceLeadText(sourceHtml: string, leadText: unknown): string {
  const nextLead = String(leadText ?? "").trim();
  if (nextLead.length > 3000) throw new Error("invalid_lead_text");
  let found = false;
  const pattern = /(<(p|div)\b[^>]*\bclass=(['"])([^'"]*)\3[^>]*>)[\s\S]*?(<\/\2>)/gi;
  const nextSource = sourceHtml.replace(pattern, (
    matched: string,
    opening: string,
    _tag: string,
    _quote: string,
    classes: string,
    closing: string,
  ) => {
    if (found || !classes.split(/\s+/).some((name) => name === "lead" || name === "standfirst")) return matched;
    found = true;
    return `${opening}${escapeHtml(nextLead)}${closing}`;
  });
  if (!found) throw new Error("lead_not_found");
  return nextSource;
}

export async function updateIssueFragments(
  db: D1Database,
  id: string,
  { title, leadText, articleHtml, expectedVersion }: UpdateIssueFragmentsInput,
): Promise<LibraryMutationResult> {
  const current = await readIssue(db, id);
  if (!current) {
    throw new LibraryError("not_found", "the Library issue was not found", 404, { id });
  }
  const nextTitle = String(title ?? "").trim();
  if (!nextTitle || nextTitle.length > 300) throw new Error("invalid_title");
  const nextArticle = cleanArticleHtml(articleHtml);
  let sourceHtml = current.sourceHtml
    .replace(/(<h1\b[^>]*>)[\s\S]*?(<\/h1>)/i, `$1${escapeHtml(nextTitle)}$2`)
    .replace(/(<article\b[^>]*>)[\s\S]*?(<\/article>)/i, `$1${nextArticle}$2`)
    .replace(/(<title\b[^>]*>)[\s\S]*?(<\/title>)/i, `$1${escapeHtml(`${nextTitle} · ${current.date}`)}$2`);
  if (typeof leadText === "string") sourceHtml = replaceLeadText(sourceHtml, leadText);
  sourceHtml = validateSourceHtml(sourceHtml);
  return updateIssueSource(db, id, { sourceHtml, expectedVersion });
}

export function isCollection(value: unknown): value is LibraryCollection {
  return typeof value === "string" && COLLECTIONS.has(value as LibraryCollection);
}
