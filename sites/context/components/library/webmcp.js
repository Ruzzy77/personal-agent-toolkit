const COLLECTIONS = new Set(["daily", "digest", "research"]);
const ISSUE_ID = /^(daily|digest|research):\d{4}-\d{2}-\d{2}(?::(?:[01]\d|2[0-3]))?$/;

function normalized(value) {
  return String(value ?? "").trim().toLocaleLowerCase("ko-KR");
}

export function findLibraryIssues(items, { collection, limit = 5, query = "" } = {}) {
  const boundedLimit = Math.max(1, Math.min(10, Number(limit) || 5));
  const needle = normalized(query);
  return items
    .filter((item) => !collection || item.collection === collection)
    .filter((item) => {
      if (!needle) return true;
      return [item.id, item.title, item.date, item.collection]
        .some((value) => normalized(value).includes(needle));
    })
    .slice(0, boundedLimit);
}

function issueSummary(item) {
  return {
    id: item.id,
    title: item.title,
    date: item.date,
    collection: item.collection,
    path: item.readerHref,
  };
}

function cardForIssue(targetDocument, id) {
  return [...targetDocument.querySelectorAll("[data-library-issue-id]")]
    .find((element) => element.dataset.libraryIssueId === id) ?? null;
}

function revealIssue(targetDocument, targetWindow, id) {
  targetDocument.querySelectorAll("[data-webmcp-match]")
    .forEach((element) => element.removeAttribute("data-webmcp-match"));
  const card = cardForIssue(targetDocument, id);
  if (!card) return;
  card.dataset.webmcpMatch = "true";
  const link = card.querySelector(".cover-link");
  link?.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
  link?.focus({ preventScroll: true });
  targetWindow.setTimeout(() => card.removeAttribute("data-webmcp-match"), 2400);
}

function register(context, tool, controller) {
  try {
    const pending = context.registerTool(tool, { signal: controller.signal });
    Promise.resolve(pending).catch((error) => {
      if (error?.name !== "AbortError") console.warn(`WebMCP tool registration failed: ${tool.name}`, error);
    });
  } catch (error) {
    console.warn(`WebMCP tool registration failed: ${tool.name}`, error);
  }
}

export function registerCatalogWebMcpTools({
  getItems,
  targetDocument = document,
  targetWindow = window,
} = {}) {
  const context = targetDocument?.modelContext;
  if (!context?.registerTool || typeof getItems !== "function") return () => {};

  const controller = new AbortController();

  register(context, {
    name: "library_find_issues",
    title: "Library 발간호 찾기",
    description: "현재 열린 Personal Library 목록에서 제목, 날짜, 식별자 또는 분류로 발간호를 찾고 첫 결과를 화면에 표시합니다. 온라인 원본은 변경하지 않습니다.",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          maxLength: 100,
          description: "제목, 날짜 또는 발간호 식별자의 일부. 비우면 최신 발간호를 찾습니다.",
        },
        collection: {
          type: "string",
          enum: ["daily", "digest", "research"],
          description: "검색할 발간 분류입니다.",
        },
        limit: {
          type: "integer",
          minimum: 1,
          maximum: 10,
          default: 5,
          description: "반환할 발간호 수입니다.",
        },
      },
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
      untrustedContentHint: true,
    },
    execute({ collection, limit, query } = {}) {
      if (collection && !COLLECTIONS.has(collection)) {
        return { status: "invalid_collection", issues: [] };
      }
      const issues = findLibraryIssues(getItems(), { collection, limit, query });
      if (issues[0]) revealIssue(targetDocument, targetWindow, issues[0].id);
      return {
        status: "found",
        count: issues.length,
        issues: issues.map(issueSummary),
      };
    },
  }, controller);

  register(context, {
    name: "library_open_issue",
    title: "Library 발간호 열기",
    description: "현재 Personal Library 목록에 있는 발간호를 식별자로 찾아 같은 브라우저 탭에서 엽니다. 온라인 원본은 변경하지 않습니다.",
    inputSchema: {
      type: "object",
      properties: {
        id: {
          type: "string",
          pattern: "^(daily|digest|research):\\d{4}-\\d{2}-\\d{2}(?::(?:[01]\\d|2[0-3]))?$",
          description: "열 발간호 식별자입니다.",
        },
      },
      required: ["id"],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
    execute({ id } = {}) {
      if (!ISSUE_ID.test(id ?? "")) return { status: "invalid_issue", id };
      const issue = getItems().find((item) => item.id === id && item.readerHref);
      if (!issue) return { status: "not_found", id };
      revealIssue(targetDocument, targetWindow, id);
      targetWindow.setTimeout(() => targetWindow.location.assign(issue.readerHref), 0);
      return { status: "opening", issue: issueSummary(issue) };
    },
  }, controller);

  return () => controller.abort();
}
