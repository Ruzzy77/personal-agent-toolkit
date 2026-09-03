(() => {
  const script = document.currentScript;
  const issueId = script?.dataset.libraryIssueId;
  if (!issueId) return;

  const AUTOSAVE_DELAY = 1_200;
  const MIN_SAVE_INTERVAL = 3_000;
  const RETRY_DELAY = 5_000;
  const draftKey = `library-editor-draft-v2:${issueId}`;

  function start() {
    const title = document.querySelector("main h1");
    const lead = document.querySelector(".reader-header .lead, .reader-header .standfirst");
    const article = document.querySelector("main article");
    if (!title || !article) return;

    const status = document.createElement("output");
    status.className = "library-save-status";
    status.setAttribute("aria-live", "polite");
    status.hidden = true;
    document.body.append(status);

    let lastSaved = null;
    let externalBaseline = null;
    let saveTimer = null;
    let statusTimer = null;
    let saving = false;
    let dirty = false;
    let saveAfterCurrent = false;
    let lastSaveStartedAt = 0;

    function registerWebMcpTool(context, controller, tool) {
      try {
        const pending = context.registerTool(tool, { signal: controller.signal });
        Promise.resolve(pending).catch((registrationError) => {
          if (registrationError?.name !== "AbortError") {
            console.warn(`WebMCP tool registration failed: ${tool.name}`, registrationError);
          }
        });
      } catch (registrationError) {
        console.warn(`WebMCP tool registration failed: ${tool.name}`, registrationError);
      }
    }

    function setStatus(state, message, { dismiss = 0 } = {}) {
      window.clearTimeout(statusTimer);
      status.dataset.state = state;
      status.textContent = message;
      status.hidden = !message;
      document.body.classList.toggle("library-save-failed", state === "error");
      if (dismiss > 0) {
        statusTimer = window.setTimeout(() => {
          if (status.dataset.state === state) {
            status.hidden = true;
            status.textContent = "";
            delete status.dataset.state;
          }
        }, dismiss);
      }
    }

    function cleanArticleHtml() {
      const clone = article.cloneNode(true);
      clone.removeAttribute("contenteditable");
      clone.removeAttribute("spellcheck");
      clone.removeAttribute("aria-label");
      clone.removeAttribute("aria-multiline");
      clone.querySelectorAll("mark[data-reader-highlight-id]").forEach((mark) => {
        mark.replaceWith(...mark.childNodes);
      });
      clone.querySelectorAll("[contenteditable]").forEach((element) => {
        element.removeAttribute("contenteditable");
      });
      return clone.innerHTML;
    }

    function readPage() {
      return {
        title: title.textContent.trim(),
        lead_text: lead?.textContent.trim() ?? null,
        article_html: cleanArticleHtml(),
      };
    }

    function samePage(left, right) {
      return Boolean(
        left
        && right
        && left.title === right.title
        && left.lead_text === right.lead_text
        && left.article_html === right.article_html
      );
    }

    function applyPage(next) {
      if (!next || typeof next !== "object") return;
      if (typeof next.title === "string") title.textContent = next.title;
      if (lead && typeof next.lead_text === "string") lead.textContent = next.lead_text;
      if (typeof next.article_html === "string") article.innerHTML = next.article_html;
      lockInteractiveContent();
    }

    function readStoredDraft() {
      try {
        return JSON.parse(window.localStorage.getItem(draftKey) ?? "null");
      } catch {
        return null;
      }
    }

    function storeDraft(draft = readPage()) {
      try {
        window.localStorage.setItem(draftKey, JSON.stringify({
          base: lastSaved,
          draft,
          stored_at: new Date().toISOString(),
        }));
        return true;
      } catch {
        return false;
      }
    }

    function clearStoredDraft() {
      try {
        window.localStorage.removeItem(draftKey);
      } catch {
        // 브라우저 저장소를 사용할 수 없어도 온라인 저장은 계속합니다.
      }
    }

    function restoreStoredDraft() {
      const stored = readStoredDraft();
      if (!stored?.base || !stored?.draft) return;
      const current = readPage();
      if (samePage(stored.draft, current)) {
        clearStoredDraft();
        return;
      }
      if (!samePage(stored.base, current)) {
        clearStoredDraft();
        return;
      }
      applyPage(stored.draft);
      dirty = true;
      setStatus("recovered", "저장하지 못한 수정 복구 중");
      scheduleSave(0);
    }

    function lockInteractiveContent() {
      article
        .querySelectorAll("a, button, nav, img, picture, video, audio, iframe")
        .forEach((element) => element.setAttribute("contenteditable", "false"));
    }

    function makeAlwaysEditable() {
      document.body.classList.add("library-owner-editable");
      title.setAttribute("contenteditable", "plaintext-only");
      title.setAttribute("spellcheck", "true");
      title.setAttribute("aria-label", "발간호 제목 편집");
      title.setAttribute("aria-multiline", "false");
      if (lead) {
        lead.setAttribute("contenteditable", "plaintext-only");
        lead.setAttribute("spellcheck", "true");
        lead.setAttribute("aria-label", "도입문 편집");
        lead.setAttribute("aria-multiline", "true");
      }
      article.setAttribute("contenteditable", "true");
      article.setAttribute("spellcheck", "true");
      article.setAttribute("aria-label", "발간호 본문 편집");
      article.setAttribute("aria-multiline", "true");
      lockInteractiveContent();
    }

    function assertSafeDraftHtml(value) {
      if (typeof value !== "string" || value.length > 2_000_000) {
        throw new Error("invalid_article_html");
      }
      if (/\son[a-z]+\s*=/i.test(value)) throw new Error("event_handler_not_allowed");
      if (/\bjavascript\s*:/i.test(value)) throw new Error("javascript_url_not_allowed");
      if (/<(?:script|style|iframe|object|embed|link|meta|base|form)\b/i.test(value)) {
        throw new Error("embedded_content_not_allowed");
      }
    }

    function scheduleSave(delay = AUTOSAVE_DELAY) {
      if (!dirty || externalBaseline) return;
      window.clearTimeout(saveTimer);
      if (saving) {
        saveAfterCurrent = true;
        return;
      }
      const minimumWait = Math.max(0, lastSaveStartedAt + MIN_SAVE_INTERVAL - Date.now());
      saveTimer = window.setTimeout(() => {
        saveTimer = null;
        saveEditing().catch(() => {});
      }, Math.max(delay, minimumWait));
    }

    function changed() {
      dirty = true;
      title.removeAttribute("aria-invalid");
      if (externalBaseline) {
        setStatus("review", "수정안 검토 중");
        return;
      }
      scheduleSave();
    }

    async function saveEditing({ force = false } = {}) {
      if (externalBaseline && !force) return { status: "reviewing", id: issueId };
      if (saving) {
        saveAfterCurrent = true;
        return { status: "saving", id: issueId };
      }

      window.clearTimeout(saveTimer);
      saveTimer = null;
      const next = readPage();
      if (!next.title) {
        dirty = true;
        storeDraft(next);
        title.setAttribute("aria-invalid", "true");
        setStatus("error", "제목이 필요합니다.");
        if (force) throw new Error("title_required");
        return { status: "title_required", id: issueId };
      }

      title.removeAttribute("aria-invalid");
      if (samePage(next, lastSaved)) {
        dirty = false;
        externalBaseline = null;
        clearStoredDraft();
        setStatus("saved", "저장됨", { dismiss: 900 });
        return { status: "unchanged", id: issueId };
      }

      saving = true;
      dirty = false;
      saveAfterCurrent = false;
      lastSaveStartedAt = Date.now();
      storeDraft(next);
      setStatus("saving", "저장 중");

      let result;
      let failure = null;
      try {
        const payload = {
          title: next.title,
          article_html: next.article_html,
        };
        if (lead) payload.lead_text = next.lead_text;
        const response = await fetch(`/api/library/issues/${encodeURIComponent(issueId)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
        result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        lastSaved = next;
        externalBaseline = null;
        if (samePage(readPage(), lastSaved)) {
          clearStoredDraft();
          setStatus("saved", "저장됨", { dismiss: 900 });
        }
      } catch (saveError) {
        failure = saveError;
        dirty = true;
        storeDraft();
        setStatus(
          "error",
          externalBaseline ? "저장하지 못했습니다." : "저장하지 못했습니다. 다시 시도합니다.",
        );
      } finally {
        saving = false;
      }

      if (saveAfterCurrent || !samePage(readPage(), lastSaved)) {
        dirty = true;
        saveAfterCurrent = false;
        scheduleSave(failure ? RETRY_DELAY : AUTOSAVE_DELAY);
      }

      if (failure) throw failure;
      return {
        status: result.status,
        id: issueId,
        title: result.issue?.title ?? next.title,
        updated_at: result.issue?.updatedAt ?? null,
      };
    }

    function applyDraft({ title: nextTitle, lead_text: nextLeadText, article_html: nextArticleHtml } = {}) {
      if (saving) throw new Error("save_in_progress");
      const changedFields = [];
      if (nextTitle !== undefined) {
        const value = String(nextTitle).trim();
        if (!value || value.length > 300) throw new Error("invalid_title");
        changedFields.push("title");
      }
      if (nextLeadText !== undefined) {
        if (!lead) throw new Error("lead_not_found");
        if (typeof nextLeadText !== "string" || nextLeadText.length > 3000) {
          throw new Error("invalid_lead_text");
        }
        changedFields.push("lead_text");
      }
      if (nextArticleHtml !== undefined) {
        assertSafeDraftHtml(nextArticleHtml);
        changedFields.push("article_html");
      }
      if (changedFields.length === 0) throw new Error("draft_fields_required");

      window.clearTimeout(saveTimer);
      saveTimer = null;
      if (!externalBaseline) externalBaseline = readPage();
      if (nextTitle !== undefined) title.textContent = String(nextTitle).trim();
      if (nextLeadText !== undefined) lead.textContent = nextLeadText.trim();
      if (nextArticleHtml !== undefined) article.innerHTML = nextArticleHtml;
      lockInteractiveContent();
      dirty = true;
      setStatus("review", "수정안 검토 중");
      article.scrollIntoView({ behavior: "smooth", block: "start" });
      return { status: "draft_applied", id: issueId, changed: changedFields, saved: false };
    }

    function discardDraft() {
      if (!externalBaseline || saving) return { status: "no_draft", id: issueId };
      const baseline = externalBaseline;
      externalBaseline = null;
      applyPage(baseline);
      dirty = !samePage(readPage(), lastSaved);
      setStatus("discarded", "수정안을 되돌렸습니다.", { dismiss: 900 });
      if (dirty) scheduleSave();
      return { status: "draft_discarded", id: issueId };
    }

    function registerWebMcpTools() {
      const context = document.modelContext;
      if (!context?.registerTool) return;
      const controller = new AbortController();

      registerWebMcpTool(context, controller, {
        name: "library_apply_draft",
        title: "현재 Library 글 수정안 반영",
        description: "현재 열린 Personal Library 발간호의 제목, 도입문 또는 본문 수정안을 편집 화면에 반영합니다. 온라인 원본에는 저장하지 않아 사용자가 먼저 검토할 수 있습니다.",
        inputSchema: {
          type: "object",
          properties: {
            title: {
              type: "string",
              minLength: 1,
              maxLength: 300,
              description: "화면에 반영할 발간호 제목입니다.",
            },
            lead_text: {
              type: "string",
              maxLength: 3000,
              description: "화면에 반영할 도입문 일반 텍스트입니다.",
            },
            article_html: {
              type: "string",
              maxLength: 2000000,
              description: "화면에 반영할 article 내부의 안전한 HTML입니다.",
            },
          },
          minProperties: 1,
          additionalProperties: false,
        },
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
        execute(input) {
          return applyDraft(input);
        },
      });

      registerWebMcpTool(context, controller, {
        name: "library_save_issue",
        title: "현재 Library 글 저장",
        description: "현재 화면에서 검토한 Personal Library 수정안을 온라인 정본에 저장합니다. 저장하지 않은 수정안이 있을 때 사용합니다.",
        inputSchema: {
          type: "object",
          properties: {},
          additionalProperties: false,
        },
        annotations: {
          readOnlyHint: false,
          destructiveHint: true,
          idempotentHint: false,
          openWorldHint: false,
        },
        execute() {
          return saveEditing({ force: true });
        },
      });

      registerWebMcpTool(context, controller, {
        name: "library_discard_draft",
        title: "현재 Library 수정안 취소",
        description: "현재 화면에 반영했지만 아직 저장하지 않은 Personal Library 수정안을 버리고 원래 내용으로 되돌립니다.",
        inputSchema: {
          type: "object",
          properties: {},
          additionalProperties: false,
        },
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
        execute() {
          return discardDraft();
        },
      });

      window.addEventListener("pagehide", () => controller.abort(), { once: true });
    }

    makeAlwaysEditable();
    lastSaved = readPage();
    restoreStoredDraft();

    title.addEventListener("input", changed);
    lead?.addEventListener("input", changed);
    article.addEventListener("input", changed);
    title.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      (lead || article).focus();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Tab") document.body.classList.add("library-keyboard-focus");
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveEditing({ force: true }).catch(() => {});
      }
    });
    document.addEventListener("pointerdown", () => {
      document.body.classList.remove("library-keyboard-focus");
    }, { capture: true });
    window.addEventListener("online", () => {
      if (dirty && !externalBaseline) scheduleSave(0);
    });
    window.addEventListener("pagehide", () => {
      if (dirty && !externalBaseline) storeDraft();
    });
    registerWebMcpTools();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
