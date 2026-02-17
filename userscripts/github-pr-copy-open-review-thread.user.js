// ==UserScript==
// @name         GitHub PR - Copy Open Review Thread
// @namespace    usermonkey.github.pr.copy.open.review
// @version      1.3.0
// @description  Adds a per-thread copy button for open (unresolved) PR review conversations.
// @match        https://github.com/*/*/pull/*
// @grant        GM_setClipboard
// ==/UserScript==

(function () {
  "use strict";

  const BTN_CLASS = "js-copy-open-thread-btn";
  const COPY_ALL_BTN_CLASS = "js-copy-open-threads-btn";

  function norm(s) {
    return (s || "").replace(/\s+/g, " ").trim();
  }

  function normCode(s) {
    return (s || "").replace(/\r\n?/g, "\n").trim();
  }

  function escapeCodeFence(s) {
    return s.replace(/```/g, "``\\`");
  }

  function toAlphaPrefix(index) {
    let n = index + 1;
    let out = "";
    while (n > 0) {
      const rem = (n - 1) % 26;
      out = String.fromCharCode(97 + rem) + out;
      n = Math.floor((n - 1) / 26);
    }
    return `${out}/`;
  }

  function isDateLike(s) {
    return /^(?:[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})$/.test(norm(s));
  }

  function findPathishText(root) {
    if (!root) return "";
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const pathRe = /\b(?:[\w.-]+\/)+[\w.-]+\.[A-Za-z0-9]+\b/;
    while (walker.nextNode()) {
      const txt = norm(walker.currentNode.nodeValue);
      if (!txt) continue;
      const m = txt.match(pathRe);
      if (m?.[0]) return m[0];
    }
    return "";
  }

  function getPrTitle() {
    const el =
      document.querySelector(".js-issue-title") ||
      document.querySelector("[data-test-selector='issue-title']") ||
      document.querySelector("h1");
    return norm(el?.textContent) || "(unknown PR title)";
  }

  function getFileNameFromThread(threadRoot, fallbackEl) {
    const threadId =
      threadRoot?.getAttribute("data-review-thread-id") ||
      threadRoot?.dataset?.reviewThreadId ||
      "";

    const fileRoot =
      threadRoot?.closest(".file") ||
      fallbackEl?.closest(".file") ||
      threadRoot?.closest("[data-tagsearch-path]") ||
      threadRoot?.closest("[data-path]") ||
      (threadId
        ? document.querySelector(`[data-review-thread-id="${threadId}"]`)?.closest("[data-path]")
        : null);

    if (fileRoot) {
      const byDataPath = norm(fileRoot.getAttribute("data-path"));
      if (byDataPath) return byDataPath;
      const byTagPath = norm(fileRoot.getAttribute("data-tagsearch-path"));
      if (byTagPath) return byTagPath;

      const fileEl =
        fileRoot.querySelector(".file-header [title]") ||
        fileRoot.querySelector(".file-info a[title]") ||
        fileRoot.querySelector(".file-info a.Link--primary") ||
        fileRoot.querySelector(".file-header a");
      const byTitle = norm(fileEl?.getAttribute("title"));
      const byText = norm(fileEl?.textContent);
      if (byTitle) return byTitle;
      if (byText) return byText;
    }

    const localPath = findPathishText(threadRoot);
    if (localPath) return localPath;

    return "(unknown file)";
  }

  function getThreadTitle(threadRoot) {
    const firstBody =
      threadRoot.querySelector(".comment-body, .js-comment-body, .markdown-body");
    const preview = norm(firstBody?.innerText || firstBody?.textContent);
    if (preview) return preview.slice(0, 120);

    const anchor =
      threadRoot.querySelector("a[href*='discussion_r']") ||
      threadRoot.querySelector("a[href*='#r']") ||
      threadRoot.querySelector("summary");
    const anchorText = norm(anchor?.textContent);
    if (anchorText && !isDateLike(anchorText)) return anchorText;

    return "(untitled thread)";
  }

  function getLineAndSnippet(threadRoot) {
    const lineNode =
      threadRoot.querySelector("[data-line-number]") ||
      threadRoot.closest("tr")?.querySelector("[data-line-number]");
    const line = norm(lineNode?.getAttribute("data-line-number"));

    const snippetNode =
      threadRoot.querySelector(".blob-code-inner") ||
      threadRoot.querySelector(".js-file-line") ||
      threadRoot.closest("tr")?.querySelector(".blob-code-inner, .js-file-line");
    const snippet = normCode(snippetNode?.innerText || snippetNode?.textContent);

    return {
      line: line || "",
      snippet: snippet ? snippet.slice(0, 600) : "",
    };
  }

  function getCodePreview(threadRoot) {
    const lineNode =
      threadRoot.querySelector("[data-line-number]") ||
      threadRoot.closest("tr")?.querySelector("[data-line-number]");
    const anchorRow = lineNode?.closest("tr");
    if (!anchorRow) return "";

    const table = anchorRow.closest("table");
    if (!table) return "";
    const rows = Array.from(table.querySelectorAll("tbody tr"));
    const idx = rows.indexOf(anchorRow);
    if (idx < 0) return "";

    const start = Math.max(0, idx - 3);
    const end = Math.min(rows.length - 1, idx + 3);
    const out = [];

    for (let i = start; i <= end; i += 1) {
      const row = rows[i];
      const numEl = row.querySelector("[data-line-number]");
      const num = norm(numEl?.getAttribute("data-line-number"));
      const codeEl = row.querySelector(".blob-code-inner, .js-file-line");
      if (!num || !codeEl) continue;

      const text = normCode(codeEl.innerText || codeEl.textContent);
      const cls = row.className || "";
      let sign = " ";
      if (/addition/.test(cls) || codeEl.classList.contains("blob-code-addition")) sign = "+";
      if (/deletion/.test(cls) || codeEl.classList.contains("blob-code-deletion")) sign = "-";

      out.push(`${num} ${sign} ${text}`);
    }

    return out.join("\n");
  }

  function collectComments(threadRoot) {
    const bodies = Array.from(
      threadRoot.querySelectorAll(".comment-body, .js-comment-body, .markdown-body")
    ).filter((el) => norm(el.innerText || el.textContent));

    const seen = new Set();
    const out = [];

    for (const body of bodies) {
      const text = norm(body.innerText || body.textContent);
      if (!text || seen.has(text)) continue;
      seen.add(text);

      const container =
        body.closest(".timeline-comment, [data-comment-id], .js-comment-container") ||
        body.parentElement;
      const author =
        norm(
          container?.querySelector(".author, a.author, [data-hovercard-type='user']")?.textContent
        ) || "unknown";
      out.push(`@${author}: ${text}`);
    }

    return out;
  }

  function copyText(text) {
    if (typeof GM_setClipboard === "function") {
      GM_setClipboard(text, "text");
      return Promise.resolve();
    }
    if (navigator.clipboard?.writeText) {
      return navigator.clipboard.writeText(text);
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    document.execCommand("copy");
    ta.remove();
    return Promise.resolve();
  }

  function getThreadRoot(resolveBtn) {
    return (
      resolveBtn.closest("[data-review-thread-id]") ||
      resolveBtn.closest(".review-thread-component") ||
      resolveBtn.closest(".js-line-comments-container") ||
      resolveBtn.closest(".js-timeline-item") ||
      resolveBtn.closest("details") ||
      resolveBtn.closest("tr") ||
      resolveBtn.parentElement
    );
  }

  function getResolveButtons() {
    return Array.from(document.querySelectorAll("button")).filter(
      (b) => norm(b.textContent) === "Resolve conversation"
    );
  }

  function buildPayload(threadRoot, resolveBtn, prefix = "") {
    const prTitle = getPrTitle();
    const threadTitle = getThreadTitle(threadRoot);
    const fileName = getFileNameFromThread(threadRoot, resolveBtn);
    const { line, snippet } = getLineAndSnippet(threadRoot);
    const codePreview = getCodePreview(threadRoot);
    const comments = collectComments(threadRoot);
    const location = line ? `${fileName}:${line}` : fileName;
    const cleanSnippet = codePreview
      ? escapeCodeFence(codePreview)
      : snippet
        ? escapeCodeFence(snippet)
        : "";

    return [
      `${prefix ? `${prefix} ` : ""}PR: ${prTitle}`,
      `Thread: ${threadTitle}`,
      `Location: ${location}`,
      "Code:",
      "```",
      ...(cleanSnippet ? [cleanSnippet] : ["(no code snippet found)"]),
      "```",
      "Comments:",
      ...(comments.length ? comments.map((c, i) => `${i + 1}. ${c}`) : ["(no comments found)"]),
    ].join("\n");
  }

  async function copyAllOpenThreads() {
    const blocks = getResolveButtons()
      .map((resolveBtn, i) => {
        const threadRoot = getThreadRoot(resolveBtn);
        return buildPayload(threadRoot, resolveBtn, toAlphaPrefix(i));
      })
      .filter(Boolean);

    await copyText(blocks.length ? blocks.join("\n\n") : "(no open threads found)");
  }

  function addButtons() {
    const buttons = getResolveButtons();

    for (const resolveBtn of buttons) {
      const actionArea = resolveBtn.parentElement;
      if (!actionArea || actionArea.querySelector(`.${BTN_CLASS}`)) continue;

      const threadRoot = getThreadRoot(resolveBtn);

      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = resolveBtn.className;
      copyBtn.classList.add(BTN_CLASS);
      copyBtn.style.marginRight = "8px";
      copyBtn.textContent = "Copy thread";

      copyBtn.addEventListener("click", async () => {
        const payload = buildPayload(threadRoot, resolveBtn);
        await copyText(payload);
        const old = copyBtn.textContent;
        copyBtn.textContent = "Copied";
        setTimeout(() => {
          copyBtn.textContent = old;
        }, 1200);
      });

      actionArea.insertBefore(copyBtn, resolveBtn);
      if (!document.querySelector(`.${COPY_ALL_BTN_CLASS}`)) {
        const copyAllBtn = document.createElement("button");
        copyAllBtn.type = "button";
        copyAllBtn.className = resolveBtn.className;
        copyAllBtn.classList.add(COPY_ALL_BTN_CLASS);
        copyAllBtn.style.marginRight = "8px";
        copyAllBtn.textContent = "Copy all open threads";
        copyAllBtn.addEventListener("click", async () => {
          const old = copyAllBtn.textContent;
          await copyAllOpenThreads();
          copyAllBtn.textContent = "Copied all";
          setTimeout(() => {
            copyAllBtn.textContent = old;
          }, 1200);
        });
        actionArea.insertBefore(copyAllBtn, copyBtn);
      }
    }
  }

  function boot() {
    addButtons();

    const mo = new MutationObserver(() => addButtons());
    mo.observe(document.body, { childList: true, subtree: true });
  }

  document.addEventListener("turbo:load", addButtons);
  window.addEventListener("load", boot);
})();
