// ==UserScript==
// @name         GitHub GHSA Advisories - Enhanced Filter
// @namespace    usermonkey.github.ghsa.enhanced.filter
// @version      1.0.2
// @description  Adds fast client-side search, filters, and sorting to GitHub repository Security Advisories list pages.
// @match        https://github.com/*
// @grant        none
// ==/UserScript==

(function () {
  "use strict";

  const ROOT_ID = "ghsa-enhanced-toolbar";
  const STYLE_ID = "ghsa-enhanced-style";
  const ADVISORIES_PATH_RE = /^\/[^/]+\/[^/]+\/security\/advisories(?:\/|$)/;

  function isAdvisoriesPage() {
    return ADVISORIES_PATH_RE.test(window.location.pathname);
  }

  function norm(s) {
    return (s || "").replace(/\s+/g, " ").trim();
  }

  function parseDateMs(el) {
    if (!el) return 0;
    const iso = el.getAttribute("datetime");
    if (!iso) return 0;
    const t = Date.parse(iso);
    return Number.isFinite(t) ? t : 0;
  }

  function severityWeight(sev) {
    const map = {
      critical: 4,
      high: 3,
      moderate: 2,
      medium: 2,
      low: 1,
      unknown: 0,
    };
    return map[sev] ?? 0;
  }

  function detectState(row) {
    const iconWrap = row.querySelector(".tooltipped[aria-label]");
    const label = norm(iconWrap?.getAttribute("aria-label") || "").toLowerCase();
    if (label.includes("published")) return "published";
    if (label.includes("draft")) return "draft";
    if (label.includes("closed")) return "closed";
    return "unknown";
  }

  function parseRow(row) {
    const link = row.querySelector("a[href*='/security/advisories/GHSA-']");
    const title = norm(link?.textContent || "");
    const href = link?.getAttribute("href") || "";

    const muted = row.querySelector(".text-small.color-fg-muted");
    const ghsaMatch = norm(muted?.textContent || "").match(/GHSA-[\w-]+/i);
    const ghsa = ghsaMatch ? ghsaMatch[0] : "";

    const author = norm(row.querySelector("a.author")?.textContent || "").toLowerCase();
    const relTime = row.querySelector("relative-time[datetime]");
    const dateMs = parseDateMs(relTime);

    const sevText = norm(row.querySelector(".Label")?.textContent || "Unknown").toLowerCase();
    const severity = sevText || "unknown";

    const state = detectState(row);

    return {
      row,
      title,
      href,
      ghsa,
      author,
      dateMs,
      severity,
      state,
      searchBlob: [title, ghsa, author, severity, state, href].join(" ").toLowerCase(),
    };
  }

  function getCanonicalList() {
    return document.querySelector("#advisories .hx_Box--firstRowRounded0 > ul");
  }

  function queryRows() {
    return Array.from(document.querySelectorAll("#advisories li.Box-row"));
  }

  function normalizeRowPlacement() {
    const list = getCanonicalList();
    if (!list) return;
    const rows = queryRows();
    for (const row of rows) {
      if (row.parentElement !== list) {
        list.appendChild(row);
      }
    }
  }

  function isReady() {
    return !!document.querySelector("#advisories .Box ul > li.Box-row");
  }

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      #${ROOT_ID} {
        border: 1px solid var(--borderColor-default, #d0d7de);
        border-radius: 6px;
        padding: 10px;
        margin: 0 0 12px;
        background: var(--bgColor-muted, #f6f8fa);
      }
      #${ROOT_ID} .ghsa-row {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        align-items: center;
      }
      #${ROOT_ID} input,
      #${ROOT_ID} select,
      #${ROOT_ID} button,
      #${ROOT_ID} label {
        font-size: 12px;
      }
      #${ROOT_ID} input[type="text"] {
        min-width: 280px;
        padding: 5px 8px;
        border: 1px solid var(--borderColor-default, #d0d7de);
        border-radius: 6px;
        background: var(--bgColor-default, #fff);
      }
      #${ROOT_ID} select,
      #${ROOT_ID} button {
        padding: 5px 8px;
        border: 1px solid var(--borderColor-default, #d0d7de);
        border-radius: 6px;
        background: var(--bgColor-default, #fff);
        cursor: pointer;
      }
      #${ROOT_ID} .ghsa-checks {
        display: inline-flex;
        gap: 6px;
        flex-wrap: wrap;
      }
      #${ROOT_ID} .ghsa-meta {
        margin-top: 8px;
        font-size: 12px;
        color: var(--fgColor-muted, #57606a);
      }
      #${ROOT_ID} .ghsa-reset {
        margin-left: auto;
      }
      #${ROOT_ID} .ghsa-hidden {
        display: none !important;
      }
    `;
    document.head.appendChild(style);
  }

  function createToolbar() {
    const root = document.createElement("div");
    root.id = ROOT_ID;
    root.innerHTML = `
      <div class="ghsa-row">
        <input type="text" class="ghsa-q" placeholder="Search title, GHSA id, author, severity..." />

        <select class="ghsa-state" title="State filter">
          <option value="all">All states</option>
          <option value="published">Published</option>
          <option value="draft">Draft</option>
          <option value="closed">Closed</option>
        </select>

        <select class="ghsa-author" title="Author filter">
          <option value="all">All authors</option>
        </select>

        <select class="ghsa-sort" title="Sort order">
          <option value="date_desc">Newest first</option>
          <option value="date_asc">Oldest first</option>
          <option value="severity_desc">Severity high to low</option>
          <option value="severity_asc">Severity low to high</option>
          <option value="title_asc">Title A to Z</option>
          <option value="title_desc">Title Z to A</option>
        </select>

        <span class="ghsa-checks" title="Severity filter">
          <label><input type="checkbox" class="ghsa-sev" value="critical" checked /> critical</label>
          <label><input type="checkbox" class="ghsa-sev" value="high" checked /> high</label>
          <label><input type="checkbox" class="ghsa-sev" value="moderate" checked /> moderate</label>
          <label><input type="checkbox" class="ghsa-sev" value="low" checked /> low</label>
          <label><input type="checkbox" class="ghsa-sev" value="unknown" checked /> unknown</label>
        </span>

        <button class="ghsa-reset" type="button">Reset</button>
      </div>
      <div class="ghsa-meta"></div>
    `;
    return root;
  }

  function mountToolbar() {
    const advisoriesRoot = document.querySelector("#advisories");
    if (!advisoriesRoot) return null;

    const existing = document.getElementById(ROOT_ID);
    if (existing) return existing;

    const box = advisoriesRoot.querySelector(".Box");
    if (!box) return null;

    const toolbar = createToolbar();
    box.parentElement?.insertBefore(toolbar, box);
    return toolbar;
  }

  function fillAuthors(toolbar, items) {
    const select = toolbar.querySelector(".ghsa-author");
    if (!select) return;

    const current = select.value || "all";
    const names = Array.from(new Set(items.map((i) => i.author).filter(Boolean))).sort((a, b) =>
      a.localeCompare(b)
    );

    select.innerHTML = '<option value="all">All authors</option>';
    for (const n of names) {
      const opt = document.createElement("option");
      opt.value = n;
      opt.textContent = n;
      select.appendChild(opt);
    }

    if (Array.from(select.options).some((o) => o.value === current)) {
      select.value = current;
    }
  }

  function apply(toolbar, items) {
    const q = norm(toolbar.querySelector(".ghsa-q")?.value || "").toLowerCase();
    const state = toolbar.querySelector(".ghsa-state")?.value || "all";
    const author = toolbar.querySelector(".ghsa-author")?.value || "all";
    const sort = toolbar.querySelector(".ghsa-sort")?.value || "date_desc";
    const allowedSev = new Set(
      Array.from(toolbar.querySelectorAll(".ghsa-sev:checked")).map((el) => el.value)
    );

    const terms = q ? q.split(/\s+/).filter(Boolean) : [];

    let filtered = items.filter((it) => {
      if (state !== "all" && it.state !== state) return false;
      if (author !== "all" && it.author !== author) return false;
      if (!allowedSev.has(it.severity)) return false;
      if (!terms.length) return true;
      return terms.every((t) => it.searchBlob.includes(t));
    });

    filtered.sort((a, b) => {
      if (sort === "date_asc") return a.dateMs - b.dateMs;
      if (sort === "date_desc") return b.dateMs - a.dateMs;
      if (sort === "severity_desc") return severityWeight(b.severity) - severityWeight(a.severity);
      if (sort === "severity_asc") return severityWeight(a.severity) - severityWeight(b.severity);
      if (sort === "title_asc") return a.title.localeCompare(b.title);
      if (sort === "title_desc") return b.title.localeCompare(a.title);
      return 0;
    });

    for (const it of items) {
      it.row.classList.add("ghsa-hidden");
    }

    const list = getCanonicalList();
    if (list) {
      for (const it of filtered) {
        it.row.classList.remove("ghsa-hidden");
        list.appendChild(it.row);
      }
    }

    const sevCounts = { critical: 0, high: 0, moderate: 0, low: 0, unknown: 0 };
    for (const it of filtered) {
      sevCounts[it.severity] = (sevCounts[it.severity] || 0) + 1;
    }

    const meta = toolbar.querySelector(".ghsa-meta");
    if (meta) {
      meta.textContent =
        `Showing ${filtered.length}/${items.length}` +
        ` | critical:${sevCounts.critical || 0}` +
        ` high:${sevCounts.high || 0}` +
        ` moderate:${sevCounts.moderate || 0}` +
        ` low:${sevCounts.low || 0}` +
        ` unknown:${sevCounts.unknown || 0}`;
    }
  }

  function bind(toolbar) {
    if (toolbar.dataset.ghsaBound === "1") return;
    toolbar.dataset.ghsaBound = "1";

    const rerender = () => apply(toolbar, toolbar.__ghsaItems || []);

    toolbar.querySelector(".ghsa-q")?.addEventListener("input", rerender);
    toolbar.querySelector(".ghsa-state")?.addEventListener("change", rerender);
    toolbar.querySelector(".ghsa-author")?.addEventListener("change", rerender);
    toolbar.querySelector(".ghsa-sort")?.addEventListener("change", rerender);
    toolbar.querySelectorAll(".ghsa-sev").forEach((el) => el.addEventListener("change", rerender));

    toolbar.querySelector(".ghsa-reset")?.addEventListener("click", () => {
      const q = toolbar.querySelector(".ghsa-q");
      if (q) q.value = "";

      const state = toolbar.querySelector(".ghsa-state");
      if (state) state.value = "all";

      const author = toolbar.querySelector(".ghsa-author");
      if (author) author.value = "all";

      const sort = toolbar.querySelector(".ghsa-sort");
      if (sort) sort.value = "date_desc";

      toolbar.querySelectorAll(".ghsa-sev").forEach((el) => {
        el.checked = true;
      });

      apply(toolbar, toolbar.__ghsaItems || []);
    });
  }

  function render() {
    if (!isAdvisoriesPage()) return;
    if (!isReady()) return;

    injectStyle();
    const toolbar = mountToolbar();
    if (!toolbar) return;

    normalizeRowPlacement();
    const items = queryRows().map(parseRow);
    if (!items.length) return;

    toolbar.__ghsaItems = items;
    fillAuthors(toolbar, items);
    bind(toolbar);
    apply(toolbar, items);
  }

  let debounceId = 0;
  function scheduleRender() {
    window.clearTimeout(debounceId);
    debounceId = window.setTimeout(render, 100);
  }

  const observer = new MutationObserver(scheduleRender);

  function init() {
    render();
    observer.observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener("turbo:load", scheduleRender);
    window.addEventListener("turbo:render", scheduleRender);
    window.addEventListener("pjax:end", scheduleRender);
    window.addEventListener("popstate", scheduleRender);
  }

  init();
})();
