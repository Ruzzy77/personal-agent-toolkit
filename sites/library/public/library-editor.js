(() => {
  const script = document.currentScript;
  const issueId = script?.dataset.libraryIssueId;
  let currentVersion = Number(script?.dataset.libraryVersion);
  if (!issueId || !Number.isInteger(currentVersion) || currentVersion < 1) return;

  const AUTOSAVE_DELAY = 1_200;
  const MIN_SAVE_INTERVAL = 3_000;
  const RETRY_DELAY = 5_000;
  const legacyDraftKey = `library-editor-draft-v2:${issueId}`;
  const draftPrefix = `library-editor-draft-v3:${encodeURIComponent(issueId)}:`;
  let draftKey = `${draftPrefix}${crypto.randomUUID()}`;

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
    let externalReviewRequired = false;
    let reviewRequired = false;
    let versionConflict = false;
    let storageFailed = false;
    let recoveryRecords = [];
    let saveTimer = null;
    let statusTimer = null;
    let saving = false;
    let dirty = false;
    let saveAfterCurrent = false;
    let lastSaveStartedAt = 0;

    const recoveryPanel = document.createElement("section");
    recoveryPanel.className = "library-draft-recovery";
    recoveryPanel.setAttribute("aria-label", "미저장 초안");
    recoveryPanel.hidden = true;
    const recoveryTitle = document.createElement("h2");
    recoveryTitle.textContent = "미저장 초안";
    const recoveryMessage = document.createElement("p");
    recoveryMessage.setAttribute("aria-live", "polite");
    const recoveryActions = document.createElement("div");
    recoveryActions.className = "library-draft-actions";
    const recoveryList = document.createElement("div");
    recoveryPanel.append(recoveryTitle, recoveryMessage, recoveryActions, recoveryList);
    article.closest("main").before(recoveryPanel);

    function actionButton(label, action, parent = recoveryActions) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", action);
      parent.append(button);
      return button;
    }

    const saveButton = actionButton("현재 수정 저장", () => saveEditing({ force: true }).catch(() => {}));
    const cancelButton = actionButton("현재 수정 취소", () => {
      if (window.confirm("현재 화면의 미저장 수정을 취소할까요? 보관된 다른 초안과 온라인 글은 바꾸지 않습니다.")) {
        discardDraft({ includeOwnerEdits: true });
      }
    });
    const reloadButton = actionButton("최신 글 불러오기", () => {
      if ((!dirty && !isReviewing()) || storeDraft()) window.location.reload();
      else setStatus("error", "초안을 보관하지 못했습니다. 새로고침 전에 원문을 복사해 주세요.");
    });
    const copyButton = actionButton("현재 초안 복사", () => copyDraft(readPage(), currentDraftSource));
    const currentDraftSource = draftSource("현재 초안 원문", recoveryPanel);

    function isReviewing() {
      return Boolean(externalBaseline || reviewRequired);
    }

    function draftText(page) {
      return `${page.title}\n\n${page.lead_text ?? ""}\n\n${page.article_html}`;
    }

    function draftSource(label, parent) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = label;
      const textarea = document.createElement("textarea");
      textarea.readOnly = true;
      textarea.rows = 8;
      textarea.setAttribute("aria-label", label);
      details.append(summary, textarea);
      parent.append(details);
      return textarea;
    }

    async function copyDraft(page, textarea) {
      textarea.value = draftText(page);
      try {
        await navigator.clipboard.writeText(textarea.value);
        setStatus("copied", "초안을 복사했습니다.", { dismiss: 1800 });
      } catch {
        textarea.parentElement.open = true;
        textarea.focus();
        textarea.select();
        setStatus("error", "복사하지 못했습니다. 선택된 원문을 직접 복사해 주세요.");
      }
    }

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

    function validPage(page) {
      return page && typeof page.title === "string"
        && (page.lead_text === null || typeof page.lead_text === "string")
        && typeof page.article_html === "string";
    }

    function readStoredDrafts() {
      try {
        const records = [];
        for (let index = 0; index < window.localStorage.length; index += 1) {
          const key = window.localStorage.key(index);
          if (key === draftKey || !(key === legacyDraftKey || key?.startsWith(draftPrefix))) continue;
          const raw = window.localStorage.getItem(key);
          try {
            const stored = JSON.parse(raw);
            if (validPage(stored?.base) && validPage(stored?.draft)) records.push({ key, raw, stored });
          } catch {
            // 다른 기록이나 읽을 수 없는 초안을 자동으로 삭제하지 않습니다.
          }
        }
        return records.sort((left, right) => String(right.stored.stored_at).localeCompare(String(left.stored.stored_at)));
      } catch {
        return [];
      }
    }

    function storeDraft(draft = readPage()) {
      const nextKey = `${draftPrefix}${crypto.randomUUID()}`;
      try {
        window.localStorage.setItem(nextKey, JSON.stringify({
          base: lastSaved,
          base_version: currentVersion,
          draft,
          review_required: isReviewing() || versionConflict,
          preview_base: externalBaseline,
          preview_review_required: externalReviewRequired,
          stored_at: new Date().toISOString(),
        }));
        const previousKey = draftKey;
        draftKey = nextKey;
        try {
          window.localStorage.removeItem(previousKey);
        } catch {
          // 새 초안은 보관됐으므로 이전 사본의 정리 실패가 복구를 막지 않습니다.
        }
        storageFailed = false;
        return true;
      } catch {
        storageFailed = true;
        return false;
      }
    }

    function clearStoredDraft() {
      try {
        window.localStorage.removeItem(draftKey);
        storageFailed = false;
      } catch {
        storageFailed = true;
      }
    }

    function restoreStoredDraft(record) {
      if (saving || dirty || isReviewing() || versionConflict) return;
      window.clearTimeout(saveTimer);
      saveTimer = null;
      applyPage(record.stored.draft);
      externalBaseline = validPage(record.stored.preview_base) ? record.stored.preview_base : null;
      externalReviewRequired = true;
      reviewRequired = true;
      dirty = !samePage(readPage(), lastSaved);
      storeDraft();
      updateRecoveryPanel();
      setStatus(storageFailed ? "error" : "review", storageFailed
        ? "복원한 초안을 이 브라우저에 보관하지 못했습니다. 원래 초안은 목록에 남아 있습니다."
        : "초안을 복원했습니다. 검토 후 저장해 주세요.");
    }

    function discardStoredDraft(record) {
      if (record.key === legacyDraftKey) return;
      if (!window.confirm("선택한 보관 초안을 이 브라우저에서 삭제할까요? 현재 화면과 온라인 글은 바꾸지 않습니다.")) return;
      try {
        if (window.localStorage.getItem(record.key) !== record.raw) {
          setStatus("error", "이 초안이 바뀌었습니다. 목록에서 다시 확인해 주세요.");
        } else {
          window.localStorage.removeItem(record.key);
        }
      } catch {
        setStatus("error", "보관된 초안을 삭제하지 못했습니다.");
      }
      updateRecoveryPanel(true);
    }

    function updateRecoveryPanel(refreshList = false) {
      if (refreshList) {
        recoveryRecords = readStoredDrafts();
        recoveryList.replaceChildren();
        for (const record of recoveryRecords) {
          const row = document.createElement("div");
          row.className = "library-stored-draft";
          const heading = document.createElement("h3");
          heading.textContent = record.stored.draft.title || "제목 없는 초안";
          const metadata = document.createElement("p");
          const storedAt = new Date(record.stored.stored_at);
          metadata.className = "library-draft-metadata";
          metadata.textContent = Number.isFinite(storedAt.getTime())
            ? `보관 시각: ${storedAt.toLocaleString("ko-KR")}`
            : "보관 시각을 확인할 수 없습니다.";
          const source = draftSource("보관된 초안 원문", row);
          source.value = draftText(record.stored.draft);
          const actions = document.createElement("div");
          actions.className = "library-draft-actions";
          actionButton("이 초안 복원", () => restoreStoredDraft(record), actions).dataset.restoreDraft = "true";
          actionButton("초안 복사", () => copyDraft(record.stored.draft, source), actions);
          if (record.key === legacyDraftKey) {
            const notice = document.createElement("p");
            notice.className = "library-draft-metadata";
            notice.textContent = "이전 편집기의 초안입니다. 다른 창의 수정을 보호하기 위해 여기서는 삭제하지 않습니다.";
            row.append(notice);
          } else {
            actionButton("보관 초안 삭제", () => discardStoredDraft(record), actions);
          }
          row.prepend(heading, metadata);
          row.append(actions);
          recoveryList.append(row);
        }
      }
      const active = dirty || isReviewing();
      recoveryPanel.hidden = !recoveryRecords.length && !isReviewing() && !versionConflict && !storageFailed;
      recoveryMessage.textContent = versionConflict
        ? "다른 편집이 먼저 저장되었습니다. 초안을 보관한 뒤 최신 글을 불러와 확인해 주세요."
        : storageFailed
          ? "이 브라우저에 현재 초안을 보관하지 못했습니다. 화면을 닫기 전에 원문을 복사해 주세요."
          : isReviewing()
            ? "현재 수정은 검토 중이며 자동 저장하지 않습니다. 보관된 다른 초안은 그대로 남아 있습니다."
            : "보관된 초안이 있습니다. 복원해도 바로 저장하지 않으며 원래 초안은 목록에 남습니다.";
      recoveryActions.hidden = !active && !versionConflict && !storageFailed;
      saveButton.disabled = saving || versionConflict || !active;
      cancelButton.disabled = saving || !active;
      reloadButton.hidden = !versionConflict;
      reloadButton.disabled = saving;
      copyButton.disabled = !active;
      currentDraftSource.parentElement.hidden = !active;
      if (active) currentDraftSource.value = draftText(readPage());
      recoveryList.querySelectorAll("[data-restore-draft]").forEach((button) => {
        button.disabled = saving || active || versionConflict;
      });
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
      if (!dirty || isReviewing() || versionConflict) return;
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
      storeDraft();
      updateRecoveryPanel();
      if (storageFailed) setStatus("error", "이 브라우저에 초안을 보관하지 못했습니다.");
      if (versionConflict) return;
      if (isReviewing()) {
        if (storageFailed) return;
        setStatus("review", "수정안 검토 중");
        return;
      }
      scheduleSave();
    }

    async function saveEditing({ force = false } = {}) {
      if (versionConflict) {
        if (force) throw new Error("version_conflict");
        return { status: "version_conflict", id: issueId };
      }
      if (isReviewing() && !force) return { status: "reviewing", id: issueId };
      if (saving) {
        saveAfterCurrent = true;
        return { status: "saving", id: issueId };
      }

      window.clearTimeout(saveTimer);
      saveTimer = null;
      const next = readPage();
      const submittedForReview = isReviewing();
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
        reviewRequired = false;
        clearStoredDraft();
        updateRecoveryPanel();
        setStatus("saved", "저장됨", { dismiss: 900 });
        return { status: "unchanged", id: issueId };
      }

      saving = true;
      dirty = false;
      saveAfterCurrent = false;
      lastSaveStartedAt = Date.now();
      storeDraft(next);
      updateRecoveryPanel();
      setStatus("saving", "저장 중");

      let result;
      let failure = null;
      try {
        const payload = {
          title: next.title,
          article_html: next.article_html,
          expected_version: currentVersion,
        };
        if (lead) payload.lead_text = next.lead_text;
        const response = await fetch(`/api/library/issues/${encodeURIComponent(issueId)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
        result = await response.json();
        if (!response.ok) {
          const requestError = new Error(result.error || `HTTP ${response.status}`);
          requestError.code = result.error;
          throw requestError;
        }
        currentVersion = Number(result.issue?.version);
        document.documentElement.dataset.libraryVersion = String(currentVersion);
        script.dataset.libraryVersion = String(currentVersion);
        lastSaved = next;
        externalBaseline = null;
        externalReviewRequired = false;
        reviewRequired = submittedForReview && !samePage(readPage(), lastSaved);
        dirty = !samePage(readPage(), lastSaved);
        if (samePage(readPage(), lastSaved)) {
          clearStoredDraft();
          setStatus("saved", "저장됨", { dismiss: 900 });
        } else {
          dirty = true;
          storeDraft();
          if (reviewRequired) setStatus("review", "요청한 내용은 저장했습니다. 이후 수정은 검토 후 다시 저장해 주세요.");
        }
      } catch (saveError) {
        failure = saveError;
        dirty = true;
        if (saveError?.code === "version_conflict") versionConflict = true;
        storeDraft();
        setStatus(
          "error",
          storageFailed
            ? "저장과 초안 보관에 실패했습니다. 화면을 닫기 전에 원문을 복사해 주세요."
            : saveError?.code === "version_conflict"
            ? "다른 편집이 먼저 저장되었습니다. 새로고침해 확인해 주세요."
            : isReviewing()
              ? "저장하지 못했습니다."
              : "저장하지 못했습니다. 다시 시도합니다.",
        );
      } finally {
        saving = false;
        updateRecoveryPanel();
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
        version: result.issue?.version ?? currentVersion,
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
      if (!externalBaseline) {
        externalBaseline = readPage();
        externalReviewRequired = reviewRequired;
      }
      if (nextTitle !== undefined) title.textContent = String(nextTitle).trim();
      if (nextLeadText !== undefined) lead.textContent = nextLeadText.trim();
      if (nextArticleHtml !== undefined) article.innerHTML = nextArticleHtml;
      lockInteractiveContent();
      dirty = true;
      storeDraft();
      updateRecoveryPanel();
      setStatus(storageFailed ? "error" : "review", storageFailed
        ? "수정안은 화면에 반영했지만 이 브라우저에 보관하지 못했습니다."
        : "수정안 검토 중");
      article.scrollIntoView({ behavior: "smooth", block: "start" });
      return { status: "draft_applied", id: issueId, changed: changedFields, saved: false };
    }

    function discardDraft({ includeOwnerEdits = false } = {}) {
      if ((!isReviewing() && !includeOwnerEdits) || saving) return { status: "no_draft", id: issueId };
      const baseline = externalBaseline || lastSaved;
      reviewRequired = externalBaseline ? externalReviewRequired : false;
      externalBaseline = null;
      externalReviewRequired = false;
      applyPage(baseline);
      dirty = !samePage(readPage(), lastSaved);
      if (dirty || reviewRequired) storeDraft();
      else clearStoredDraft();
      updateRecoveryPanel();
      setStatus(storageFailed ? "error" : "discarded", storageFailed
        ? "화면은 되돌렸지만 브라우저의 초안 기록을 갱신하지 못했습니다."
        : "수정안을 되돌렸습니다.", { dismiss: storageFailed ? 0 : 900 });
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
    updateRecoveryPanel(true);

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
      if (dirty && !isReviewing() && !versionConflict) scheduleSave(0);
    });
    window.addEventListener("pagehide", () => {
      if (dirty || isReviewing()) storeDraft();
    });
    window.addEventListener("storage", (event) => {
      if (event.key === null || event.key === legacyDraftKey || event.key?.startsWith(draftPrefix)) updateRecoveryPanel(true);
    });
    registerWebMcpTools();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
