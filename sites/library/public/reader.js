(() => {
  const root = document.documentElement;
  const sizeKey = "library-reader-size";
  const highlightKey = "library-reader-highlights-v1";
  const sizeNames = ["작게", "기본", "크게", "아주 크게"];
  const pageKey = window.location.pathname;
  const maxHighlightsPerPage = 200;
  const publicationLabels = {
    daily: "Daily",
    digest: "Research Digest",
    research: "Research",
  };
  const monthLabels = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

  function read(key) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function write(key, value) {
    try {
      window.localStorage.setItem(key, value);
      return true;
    } catch {
      // 저장할 수 없어도 현재 화면의 설정은 유지됩니다.
      return false;
    }
  }

  function formatIssueDate(date) {
    const match = String(date ?? "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return String(date ?? "");
    return `${match[3]} ${monthLabels[Number(match[2]) - 1]} ${match[1]}`;
  }

  function canonicalIssueMeta(masthead, header) {
    const { libraryIssueId: issueId, libraryCollection: collection, libraryDate: date } = root.dataset;
    if (!issueId || !publicationLabels[collection] || !date) return null;

    const sourceMeta = masthead?.textContent
      || Array.from(header.querySelectorAll(".kicker, .eyebrow, .series"))
        .map((element) => element.textContent)
        .join(" ");
    const sequence = collection === "digest"
      ? sourceMeta.match(/Research\s+Digest\s*·\s*(\d{1,3})/i)?.[1]
      : null;
    const timeSlot = issueId.match(/:(\d{2})$/)?.[1] ?? null;
    const details = [publicationLabels[collection]];
    if (sequence) details.push(sequence);
    if (timeSlot) details.push(`${timeSlot}:00`);

    const canonical = document.createElement("div");
    canonical.className = "masthead reader-issue-meta";

    const publication = document.createElement("span");
    publication.className = "reader-publication";
    publication.textContent = details.join(" · ");

    const publishedDate = document.createElement("time");
    publishedDate.className = "reader-date";
    publishedDate.dateTime = date;
    publishedDate.textContent = formatIssueDate(date);

    canonical.append(publication, publishedDate);
    return canonical;
  }

  function composeReader() {
    const main = document.querySelector("main");
    if (!main || main.dataset.readerComposed === "true") return;

    const returnNav = main.querySelector(":scope > [data-library-return]")
      || document.querySelector("[data-library-return]");
    if (returnNav) {
      returnNav.setAttribute("aria-label", "Library 홈");
      const link = returnNav.querySelector("a");
      if (link) {
        link.setAttribute("aria-label", "Library 홈");
        const logo = document.createElement("span");
        logo.className = "library-wordmark";
        logo.textContent = "LIBRARY";
        logo.setAttribute("aria-hidden", "true");
        link.replaceChildren(logo);
      }
      document.body.insertBefore(returnNav, document.body.firstChild);
    }

    const directChildren = Array.from(main.children);
    const masthead = directChildren.find((element) =>
      element.matches(".masthead, .issue-line, .issue-bar, .folio"),
    );
    const header = directChildren.find((element) =>
      element.matches("header, .brief-header, .issue-header")
      && element.querySelector("h1"),
    );

    if (!header) {
      main.classList.add("reader-document");
      main.dataset.readerComposed = "true";
      document.body.classList.add("saegin-reader");
      return;
    }

    const opening = document.createElement("div");
    opening.className = "reader-opening";

    const meta = document.createElement("aside");
    meta.className = "reader-meta";
    meta.setAttribute("aria-label", "발간 정보");

    const canonicalMeta = canonicalIssueMeta(masthead, header);
    if (canonicalMeta) {
      meta.append(canonicalMeta);
    } else {
      if (masthead) {
        masthead.classList.add("masthead");
        meta.append(masthead);
      }
      Array.from(header.querySelectorAll("time, .kicker, .eyebrow, .series, .date"))
        .forEach((element) => meta.append(element));
    }

    const title = header.querySelector("h1");
    const lead = header.querySelector(".lead, .standfirst");
    title?.removeAttribute("style");
    if (lead) {
      lead.classList.add("lead");
      lead.removeAttribute("style");
    }
    header.replaceChildren(...[title, lead].filter(Boolean));

    header.classList.add("reader-header");
    opening.append(meta, header);

    const content = document.createElement("div");
    content.className = "reader-content";
    Array.from(main.children)
      .filter((element) => element !== masthead && element !== header && element !== returnNav)
      .forEach((element) => content.append(element));

    content
      .querySelectorAll([
        ".boundary",
        ".callout",
        ".check",
        ".closing",
        ".conclusion",
        ".equation",
        ".finding",
        ".judgment",
        ".maxim",
        ".measure",
        ".moment",
        ".note",
        ".split-note",
        ".state-line",
        ".status",
        ".turn",
      ].join(","))
      .forEach((element) => element.classList.add("reader-callout"));

    content
      .querySelectorAll(".key, .reader-key-sentence")
      .forEach((element) => element.classList.add("reader-key-sentence"));

    content
      .querySelectorAll(".web-edition-nav, nav[aria-label='발간호 탐색']")
      .forEach((element) => element.remove());

    if (!main.id) main.id = "reader-top";
    const endNav = document.createElement("nav");
    endNav.className = "reader-end-nav";
    endNav.setAttribute("aria-label", "맨 위로 이동");

    const backToTop = document.createElement("a");
    backToTop.href = `#${main.id}`;
    backToTop.textContent = "↑ 맨 위로";
    endNav.append(backToTop);
    content.append(endNav);

    main.replaceChildren(opening, content);
    main
      .querySelectorAll(".metric, .num")
      .forEach((element) => element.classList.add("num"));
    main.classList.add("reader-document");
    main.dataset.readerComposed = "true";
    document.body.classList.add("saegin-reader");
  }

  function fitReaderTitle() {
    const title = document.querySelector(".reader-header h1");
    if (!title) return;

    title.style.setProperty("font-size", "var(--text-display)", "important");
    const available = title.clientWidth;
    const required = title.scrollWidth;
    if (!available || !required || required <= available) return;

    const naturalSize = Number.parseFloat(window.getComputedStyle(title).fontSize);
    if (!Number.isFinite(naturalSize)) return;
    const fittedSize = Math.max(12, naturalSize * (available / required) * 0.985);
    title.style.setProperty("font-size", `${fittedSize.toFixed(2)}px`, "important");
  }

  function addReaderTools() {
    let size = Number.parseInt(read(sizeKey) ?? "1", 10);
    if (!Number.isInteger(size) || size < 0 || size > 3) size = 1;

    root.dataset.readerSize = String(size);

    const tools = document.createElement("aside");
    tools.className = "reader-tools";
    tools.setAttribute("aria-label", "읽기 설정");
    tools.innerHTML = `
      <button type="button" data-reader-smaller aria-label="글자 작게" title="글자 작게">
        <img src="/icons/library/reader-text-smaller.png" alt="" aria-hidden="true" draggable="false">
      </button>
      <button type="button" data-reader-larger aria-label="글자 크게" title="글자 크게">
        <img src="/icons/library/reader-text-larger.png" alt="" aria-hidden="true" draggable="false">
      </button>
      <output class="reader-sr-only" aria-live="polite"></output>
    `;

    const smaller = tools.querySelector("[data-reader-smaller]");
    const larger = tools.querySelector("[data-reader-larger]");
    const status = tools.querySelector("output");

    function updateSize(nextSize, announce = true) {
      size = Math.max(0, Math.min(3, nextSize));
      root.dataset.readerSize = String(size);
      smaller.disabled = size === 0;
      larger.disabled = size === 3;
      write(sizeKey, String(size));
      if (announce) status.textContent = `글자 크기 ${sizeNames[size]}`;
      window.requestAnimationFrame(fitReaderTitle);
    }

    smaller.addEventListener("click", () => updateSize(size - 1));
    larger.addEventListener("click", () => updateSize(size + 1));

    updateSize(size, false);
    document.body.append(tools);
  }

  function addReaderHighlights() {
    const content = document.querySelector(".reader-content");
    if (!content) return;

    const blockedSelector = [
      "a",
      "button",
      "nav",
      "svg",
      ".reader-sr-only",
      "[aria-hidden='true']",
      "mark[data-reader-highlight-id]",
    ].join(",");

    function readStore() {
      try {
        const parsed = JSON.parse(read(highlightKey) ?? "{}");
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
      } catch {
        return {};
      }
    }

    function readPageHighlights() {
      const items = readStore()[pageKey];
      if (!Array.isArray(items)) return [];
      return items
        .filter((item) =>
          item
          && typeof item.id === "string"
          && /^[a-zA-Z0-9-]{1,80}$/.test(item.id)
          && Number.isInteger(item.start)
          && Number.isInteger(item.end)
          && item.start >= 0
          && item.end > item.start,
        )
        .slice(0, maxHighlightsPerPage);
    }

    function savePageHighlights(items) {
      const store = readStore();
      if (items.length) store[pageKey] = items.slice(0, maxHighlightsPerPage);
      else delete store[pageKey];
      return write(highlightKey, JSON.stringify(store));
    }

    function textNodes() {
      const nodes = [];
      const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) nodes.push(walker.currentNode);
      return nodes;
    }

    function canWrap(node) {
      const parent = node.parentElement;
      return Boolean(parent && !parent.closest(blockedSelector));
    }

    function applyHighlight(item) {
      const targets = [];
      let cursor = 0;

      for (const node of textNodes()) {
        const length = node.nodeValue?.length ?? 0;
        const nodeStart = cursor;
        const nodeEnd = cursor + length;
        cursor = nodeEnd;

        if (!length || !canWrap(node)) continue;
        const start = Math.max(item.start, nodeStart);
        const end = Math.min(item.end, nodeEnd);
        if (end <= start) continue;
        targets.push({ node, start: start - nodeStart, end: end - nodeStart });
      }

      const fragments = [];
      for (const target of targets.reverse()) {
        const range = document.createRange();
        range.setStart(target.node, target.start);
        range.setEnd(target.node, target.end);
        const mark = document.createElement("mark");
        mark.className = "reader-highlight";
        mark.dataset.readerHighlightId = item.id;
        mark.title = "클릭하여 형광펜 지우기";
        range.surroundContents(mark);
        fragments.unshift(mark);
      }

      if (fragments.length) {
        fragments[0].tabIndex = 0;
        fragments[0].setAttribute("role", "button");
        fragments[0].setAttribute("aria-label", "형광펜 지우기");
      }
      return fragments.length > 0;
    }

    function unwrap(mark) {
      const parent = mark.parentNode;
      if (!parent) return;
      mark.replaceWith(...mark.childNodes);
      parent.normalize();
    }

    const status = document.createElement("output");
    status.className = "reader-sr-only";
    status.setAttribute("aria-live", "polite");
    document.body.append(status);

    let highlights = readPageHighlights();
    const sourceText = content.textContent ?? "";
    highlights = highlights.filter((item) => {
      const quote = typeof item.quote === "string" ? item.quote.trim() : "";
      const current = sourceText.slice(item.start, item.end).trim();
      if (quote && !current.startsWith(quote)) return false;
      return applyHighlight(item);
    });
    savePageHighlights(highlights);

    function removeHighlight(id) {
      content
        .querySelectorAll(`mark[data-reader-highlight-id="${id}"]`)
        .forEach(unwrap);
      highlights = highlights.filter((item) => item.id !== id);
      savePageHighlights(highlights);
      status.textContent = "형광펜 표시를 지웠습니다.";
    }

    content.addEventListener("click", (event) => {
      const mark = event.target.closest?.("mark[data-reader-highlight-id]");
      if (!mark || !content.contains(mark)) return;
      event.preventDefault();
      event.stopPropagation();
      removeHighlight(mark.dataset.readerHighlightId);
    });

    content.addEventListener("keydown", (event) => {
      const mark = event.target.closest?.("mark[data-reader-highlight-id]");
      if (!mark || !new Set(["Enter", " ", "Delete", "Backspace"]).has(event.key)) return;
      event.preventDefault();
      removeHighlight(mark.dataset.readerHighlightId);
    });

    const action = document.createElement("button");
    action.type = "button";
    action.className = "reader-highlight-action";
    action.hidden = true;
    action.setAttribute("aria-label", "형광펜 표시");
    action.title = "형광펜 표시";
    document.body.append(action);

    let pendingRange = null;

    function hideAction() {
      action.hidden = true;
      pendingRange = null;
    }

    function intersectsBlockedElement(range) {
      return Array.from(content.querySelectorAll(blockedSelector)).some((element) => {
        try {
          return range.intersectsNode(element);
        } catch {
          return false;
        }
      });
    }

    function showAction() {
      const selection = window.getSelection();
      if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
        hideAction();
        return;
      }

      const range = selection.getRangeAt(0);
      const selectedText = range.toString().trim();
      if (
        !selectedText
        || selectedText.length > 5000
        || !content.contains(range.startContainer)
        || !content.contains(range.endContainer)
        || intersectsBlockedElement(range)
      ) {
        hideAction();
        return;
      }

      const rect = range.getBoundingClientRect();
      if (!rect.width && !rect.height) {
        hideAction();
        return;
      }

      pendingRange = range.cloneRange();
      action.hidden = false;
      const width = 42;
      const gap = 8;
      const left = Math.max(gap, Math.min(window.innerWidth - width - gap, rect.left + (rect.width / 2) - (width / 2)));
      const top = rect.top >= width + (gap * 2) ? rect.top - width - gap : rect.bottom + gap;
      action.style.left = `${Math.round(left)}px`;
      action.style.top = `${Math.round(top)}px`;
    }

    function rangeOffset(range, boundary) {
      const prefix = document.createRange();
      prefix.selectNodeContents(content);
      if (boundary === "start") prefix.setEnd(range.startContainer, range.startOffset);
      else prefix.setEnd(range.endContainer, range.endOffset);
      return prefix.toString().length;
    }

    action.addEventListener("pointerdown", (event) => event.preventDefault());
    action.addEventListener("click", () => {
      if (!pendingRange || highlights.length >= maxHighlightsPerPage) {
        hideAction();
        return;
      }

      const item = {
        id: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`,
        start: rangeOffset(pendingRange, "start"),
        end: rangeOffset(pendingRange, "end"),
        quote: pendingRange.toString().trim().slice(0, 200),
      };

      if (applyHighlight(item)) {
        highlights = [...highlights, item].sort((left, right) => left.start - right.start);
        const saved = savePageHighlights(highlights);
        status.textContent = saved ? "형광펜으로 표시했습니다." : "현재 화면에 형광펜으로 표시했습니다.";
      }

      window.getSelection()?.removeAllRanges();
      hideAction();
    });

    document.addEventListener("pointerup", (event) => {
      if (action.contains(event.target)) return;
      window.setTimeout(showAction, 0);
    });
    content.addEventListener("keyup", () => window.setTimeout(showAction, 0));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") hideAction();
    });
    window.addEventListener("scroll", hideAction, { passive: true });
    window.addEventListener("resize", hideAction, { passive: true });
  }

  function start() {
    composeReader();
    addReaderTools();
    addReaderHighlights();
    window.requestAnimationFrame(fitReaderTitle);
    document.fonts?.ready.then(fitReaderTitle);
    window.addEventListener("resize", fitReaderTitle, { passive: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
