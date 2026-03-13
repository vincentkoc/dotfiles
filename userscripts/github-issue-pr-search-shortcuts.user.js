// ==UserScript==
// @name         GitHub Issue/PR - Search Shortcuts
// @namespace    usermonkey.github.issue.pr.search.shortcuts
// @version      1.0.0
// @description  Adds repo-scoped GitHub search shortcuts for comment authors and issue/PR authors.
// @match        https://github.com/*/*/issues/*
// @match        https://github.com/*/*/pull/*
// @grant        none
// ==/UserScript==

(function () {
  "use strict";

  const STYLE_ID = "gh-search-shortcuts-style";
  const COMMENT_BUTTON_CLASS = "js-gh-comment-search-shortcut";
  const AUTHOR_BUTTON_CLASS = "js-gh-author-open-items-shortcut";
  const ISSUE_PATH_RE = /^\/[^/]+\/[^/]+\/(?:issues|pull)\/\d+(?:\/|$)/;

  function norm(s) {
    return (s || "").replace(/\s+/g, " ").trim();
  }

  function isIssueOrPrPage() {
    return ISSUE_PATH_RE.test(window.location.pathname);
  }

  function getRepoPath() {
    const parts = window.location.pathname.split("/").filter(Boolean);
    if (parts.length < 2) return "";
    return `/${parts[0]}/${parts[1]}`;
  }

  function buildSearchUrl(qualifier, user) {
    const repoPath = getRepoPath();
    if (!repoPath || !user) return "";

    const url = new URL(`${window.location.origin}${repoPath}/issues`);
    url.searchParams.set("q", `state:open ${qualifier}:${user}`);
    return url.toString();
  }

  function findCommentAuthor(container) {
    if (!container) return "";

    const selectors = [
      ".timeline-comment-header a.author",
      ".timeline-comment-header [data-hovercard-type='user']",
      ".timeline-comment-header strong a",
      "[data-testid='comment-header'] a.author",
    ];

    for (const selector of selectors) {
      const text = norm(container.querySelector(selector)?.textContent);
      if (text) return text.replace(/^@/, "");
    }

    return "";
  }

  function makeButton(label, href, className) {
    const a = document.createElement("a");
    a.className = `Button Button--small ${className}`;
    a.href = href;
    a.target = "_blank";
    a.rel = "noreferrer";
    a.textContent = label;
    return a;
  }

  function getCommentToolbar(container) {
    return (
      container.querySelector(".timeline-comment-actions") ||
      container.querySelector(".timeline-comment-header div:last-child") ||
      null
    );
  }

  function injectCommentButtons() {
    const comments = Array.from(document.querySelectorAll(".timeline-comment"));
    for (const comment of comments) {
      const toolbar = getCommentToolbar(comment);
      if (!toolbar || toolbar.querySelector(`.${COMMENT_BUTTON_CLASS}`)) continue;

      const author = findCommentAuthor(comment);
      const href = buildSearchUrl("commenter", author);
      if (!href) continue;

      toolbar.prepend(makeButton("Search comments", href, COMMENT_BUTTON_CLASS));
    }
  }

  function getMainCommentContainer() {
    const discussion = document.querySelector(".js-discussion");
    if (!discussion) return null;

    return (
      discussion.querySelector(".TimelineItem:first-of-type .timeline-comment") ||
      discussion.querySelector(".timeline-comment")
    );
  }

  function injectAuthorButton() {
    const mainComment = getMainCommentContainer();
    if (!mainComment) return;

    const toolbar = getCommentToolbar(mainComment);
    if (!toolbar || toolbar.querySelector(`.${AUTHOR_BUTTON_CLASS}`)) return;

    const author = findCommentAuthor(mainComment);
    const href = buildSearchUrl("author", author);
    if (!href) return;

    toolbar.prepend(makeButton("All open items", href, AUTHOR_BUTTON_CLASS));
  }

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;

    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .${COMMENT_BUTTON_CLASS},
      .${AUTHOR_BUTTON_CLASS} {
        margin-right: 6px;
      }
    `;
    document.head.appendChild(style);
  }

  function run() {
    if (!isIssueOrPrPage()) return;
    injectStyle();
    injectCommentButtons();
    injectAuthorButton();
  }

  let scheduled = false;
  function scheduleRun() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      run();
    });
  }

  const observer = new MutationObserver(() => {
    scheduleRun();
  });

  observer.observe(document.documentElement, { childList: true, subtree: true });
  run();
})();
