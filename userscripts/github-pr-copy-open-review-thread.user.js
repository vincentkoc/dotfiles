// ==UserScript==
// @name         GitHub PR - Copy Open Review Thread
// @namespace    usermonkey.github.pr.copy.open.review
// @version      1.1.0
// @description  Adds a per-thread copy button for open (unresolved) PR review conversations.
// @match        https://github.com/*/*/pull/*
// @grant        GM_setClipboard
// ==/UserScript==

(function () {
  "use strict";

  const BTN_CLASS = "js-copy-open-thread-btn";

  function norm(s) {
    return (s || "").replace(/\s+/g, " ").trim();
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
    const snippet = norm(snippetNode?.innerText || snippetNode?.textContent);

    return {
      line: line || "",
      snippet: snippet ? snippet.slice(0, 220) : "",
    };
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

  function buildPayload(threadRoot, resolveBtn) {
    const prTitle = getPrTitle();
    const threadTitle = getThreadTitle(threadRoot);
    const fileName = getFileNameFromThread(threadRoot, resolveBtn);
    const { line, snippet } = getLineAndSnippet(threadRoot);
    const comments = collectComments(threadRoot);
    const location = line ? `${fileName}:${line}` : fileName;

    return [
      `PR: ${prTitle}`,
      `Thread: ${threadTitle}`,
      `Location: ${location}`,
      ...(snippet ? [`Snippet: ${snippet}`] : []),
      "Comments:",
      ...(comments.length ? comments.map((c, i) => `${i + 1}. ${c}`) : ["(no comments found)"]),
    ].join("\n");
  }

  function addButtons() {
    const buttons = Array.from(document.querySelectorAll("button")).filter(
      (b) => norm(b.textContent) === "Resolve conversation"
    );

    for (const resolveBtn of buttons) {
      const actionArea = resolveBtn.parentElement;
      if (!actionArea || actionArea.querySelector(`.${BTN_CLASS}`)) continue;

      const threadRoot =
        resolveBtn.closest("[data-review-thread-id]") ||
        resolveBtn.closest(".review-thread-component") ||
        resolveBtn.closest(".js-line-comments-container") ||
        resolveBtn.closest(".js-timeline-item") ||
        resolveBtn.closest("details") ||
        resolveBtn.closest("tr") ||
        resolveBtn.parentElement;

      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = `btn btn-sm ${BTN_CLASS}`;
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
