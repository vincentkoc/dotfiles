// ==UserScript==
// @name         GitHub Issue/PR - Search Shortcuts
// @namespace    usermonkey.github.issue.pr.search.shortcuts
// @version      1.1.0
// @description  Adds repo-scoped GitHub search shortcuts for comment authors and issue/PR authors, with lazy count badges.
// @match        https://github.com/*/*/issues/*
// @match        https://github.com/*/*/pull/*
// @grant        none
// ==/UserScript==

(function () {
  "use strict";

  const STYLE_ID = "gh-search-shortcuts-style";
  const COMMENT_BUTTON_CLASS = "js-gh-comment-search-shortcut";
  const AUTHOR_BUTTON_CLASS = "js-gh-author-open-items-shortcut";
  const COUNT_LOADING_CLASS = "is-gh-search-shortcut-loading";
  const SESSION_CACHE_KEY = "gh-search-shortcuts-cache-v1";
  const ISSUE_PATH_RE = /^\/[^/]+\/[^/]+\/(?:issues|pull)\/\d+(?:\/|$)/;
  const MAX_COMMENT_COUNT_BUTTONS = 8;
  const MAX_CONCURRENT_COUNT_REQUESTS = 2;
  const countCache = loadSessionCache();
  const countPromises = new Map();
  const pendingCountButtons = new Set();
  const countQueue = [];
  let activeCountRequests = 0;
  let countObserver = null;

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

  function loadSessionCache() {
    try {
      const raw = sessionStorage.getItem(SESSION_CACHE_KEY);
      if (!raw) return new Map();
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return new Map();
      return new Map(Object.entries(parsed));
    } catch {
      return new Map();
    }
  }

  function persistSessionCache() {
    try {
      sessionStorage.setItem(SESSION_CACHE_KEY, JSON.stringify(Object.fromEntries(countCache)));
    } catch {
      // Ignore storage failures. The in-memory cache still avoids duplicate fetches for this page.
    }
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
    a.dataset.baseLabel = label;
    a.innerHTML = `
      <span class="Button-content">
        <span class="Button-label">${label}</span>
        <span class="gh-search-shortcut-count" hidden></span>
      </span>
    `;
    return a;
  }

  function setButtonCountState(button, state) {
    if (!button) return;

    const countEl = button.querySelector(".gh-search-shortcut-count");
    const labelEl = button.querySelector(".Button-label");
    if (labelEl) labelEl.textContent = button.dataset.baseLabel || button.textContent || "";

    button.classList.toggle(COUNT_LOADING_CLASS, state === "loading");
    if (!countEl) return;

    if (state === "loading") {
      countEl.hidden = false;
      countEl.textContent = "…";
      return;
    }

    if (typeof state === "number" && Number.isFinite(state)) {
      countEl.hidden = false;
      countEl.textContent = `(${state})`;
      return;
    }

    countEl.hidden = true;
    countEl.textContent = "";
  }

  function getCommentToolbar(container) {
    return (
      container.querySelector(".timeline-comment-actions") ||
      container.querySelector(".timeline-comment-header div:last-child") ||
      null
    );
  }

  function getSearchCountFromDocument(doc) {
    const selectors = [
      ".issues-search-results h3",
      "[data-testid='search-sub-header']",
      ".search-title",
      ".codesearch-results h3",
      "main h3",
    ];

    for (const selector of selectors) {
      const text = norm(doc.querySelector(selector)?.textContent);
      const count = parseCountFromText(text);
      if (count !== null) return count;
    }

    const bodyText = norm(doc.body?.textContent || "");
    return parseCountFromText(bodyText);
  }

  function parseCountFromText(text) {
    if (!text) return null;

    const patterns = [
      /([\d,]+)\s+results?/i,
      /([\d,]+)\s+open/i,
      /results?\s+\(([\d,]+)\)/i,
    ];

    for (const pattern of patterns) {
      const match = text.match(pattern);
      if (!match) continue;
      const value = Number(match[1].replace(/,/g, ""));
      if (Number.isFinite(value)) return value;
    }

    return null;
  }

  async function fetchSearchCount(url) {
    const cached = countCache.get(url);
    if (cached !== undefined) return cached;

    const inflight = countPromises.get(url);
    if (inflight) return inflight;

    const promise = (async () => {
      const res = await fetch(url, { credentials: "same-origin" });
      if (!res.ok) throw new Error(`Search fetch failed: ${res.status}`);

      const html = await res.text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      const count = getSearchCountFromDocument(doc);
      if (count === null) throw new Error("Unable to parse search count");

      countCache.set(url, count);
      persistSessionCache();
      return count;
    })();

    countPromises.set(url, promise);

    try {
      return await promise;
    } finally {
      countPromises.delete(url);
    }
  }

  function drainCountQueue() {
    while (activeCountRequests < MAX_CONCURRENT_COUNT_REQUESTS && countQueue.length > 0) {
      const button = countQueue.shift();
      if (!button || !pendingCountButtons.has(button) || !document.contains(button)) continue;

      pendingCountButtons.delete(button);
      activeCountRequests += 1;
      setButtonCountState(button, "loading");

      fetchSearchCount(button.href)
        .then((count) => {
          setButtonCountState(button, count);
        })
        .catch(() => {
          setButtonCountState(button, "idle");
        })
        .finally(() => {
          activeCountRequests -= 1;
          drainCountQueue();
        });
    }
  }

  function enqueueCountLoad(button) {
    if (!button || button.dataset.countsEnabled !== "true") return;
    if (pendingCountButtons.has(button)) return;
    if (button.dataset.countLoaded === "true") return;

    button.dataset.countLoaded = "true";
    pendingCountButtons.add(button);
    countQueue.push(button);
    drainCountQueue();
  }

  function ensureCountObserver() {
    if (countObserver) return countObserver;

    countObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          countObserver.unobserve(entry.target);
          enqueueCountLoad(entry.target);
        }
      },
      {
        rootMargin: "200px 0px",
      }
    );

    return countObserver;
  }

  function watchButtonForCount(button) {
    if (!button || button.dataset.countsEnabled !== "true") return;

    const cached = countCache.get(button.href);
    if (cached !== undefined) {
      setButtonCountState(button, cached);
      button.dataset.countLoaded = "true";
      return;
    }

    ensureCountObserver().observe(button);
  }

  function injectCommentButtons() {
    const comments = Array.from(document.querySelectorAll(".timeline-comment"));
    let countEnabled = 0;

    for (const comment of comments) {
      const toolbar = getCommentToolbar(comment);
      if (!toolbar || toolbar.querySelector(`.${COMMENT_BUTTON_CLASS}`)) continue;

      const author = findCommentAuthor(comment);
      const href = buildSearchUrl("commenter", author);
      if (!href) continue;

      const button = makeButton("Search comments", href, COMMENT_BUTTON_CLASS);
      if (countEnabled < MAX_COMMENT_COUNT_BUTTONS) {
        button.dataset.countsEnabled = "true";
        countEnabled += 1;
      }

      toolbar.prepend(button);
      watchButtonForCount(button);
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

    const button = makeButton("All open items", href, AUTHOR_BUTTON_CLASS);
    button.dataset.countsEnabled = "true";
    toolbar.prepend(button);
    watchButtonForCount(button);
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

      .${COMMENT_BUTTON_CLASS} .Button-content,
      .${AUTHOR_BUTTON_CLASS} .Button-content {
        gap: 6px;
      }

      .gh-search-shortcut-count {
        color: var(--fgColor-muted, #57606a);
        font-variant-numeric: tabular-nums;
      }

      .${COUNT_LOADING_CLASS} .gh-search-shortcut-count {
        opacity: 0.75;
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
