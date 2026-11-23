// The search experience shared by the search page and topic pages: query, refine sidebar, results, views and paging.
import { catalog, dataUrl, esc, fetchJSON, icon, indexMeta, plural, sources, statusHelp, summaries, wireHelp, STATUS } from "./core.js";
import { documentEntry } from "./cards.js";
import { createSemantic, eligible, fuse, keywordWeight, meaningCutoff, meaningText, topMatches } from "./semantic.js";
import { enhanceSelect, rangeSlider } from "./controls.js";
import { Index, applyFilters, describeQuery, highlight, maskFields, parseQuery, runQuery, setStopwords, setVariants, sortResults, suggest, yearBounds } from "./search.js";

const MODES = ["all", "any", "exact"];
const ADVANCED_FIELDS = [["", "All fields"], ["title", "Title"], ["author", "Author"], ["recipient", "Recipient"], ["people", "People mentioned"],
  ["text", "Transcription"], ["place", "Place"], ["subject", "Subject"], ["type", "Document type"], ["id", "Document ID"]];

function readState() {
  const p = new URLSearchParams(location.search);
  const set = (name) => new Set(p.getAll(name).filter(Boolean));
  return {
    q: p.get("q") || "", mode: MODES.includes(p.get("mode")) ? p.get("mode") : "all",
    sort: ["", "oldest", "newest", "id"].includes(p.get("sort")) ? p.get("sort") : "",
    view: p.get("view") === "grid" ? "grid" : "list", page: Math.max(1, Number(p.get("page")) || 1),
    per: [20, 50, 100].includes(Number(p.get("per"))) ? Number(p.get("per")) : 20,
    from: Number(p.get("from")) || null, to: Number(p.get("to")) || null, types: set("type"), folders: set("folder"), statuses: set("status"),
  };
}

function stateParams(state) {
  const p = new URLSearchParams(location.search);
  for (const key of ["q", "mode", "sort", "view", "page", "per", "from", "to", "type", "folder", "status"]) p.delete(key);
  if (state.q) p.set("q", state.q);
  if (state.mode !== "all") p.set("mode", state.mode);
  if (state.sort) p.set("sort", state.sort);
  if (state.view !== "list") p.set("view", state.view);
  if (state.page > 1) p.set("page", state.page);
  if (state.per !== 20) p.set("per", state.per);
  if (state.from) p.set("from", state.from);
  if (state.to) p.set("to", state.to);
  state.types.forEach((v) => p.append("type", v));
  state.folders.forEach((v) => p.append("folder", v));
  state.statuses.forEach((v) => p.append("status", v));
  return p;
}

function writeState(state, push) {
  const url = `${location.pathname}?${stateParams(state)}`;
  if (push) history.pushState(null, "", url); else history.replaceState(null, "", url);
}

/** Mounts search into `root`; `within` is a fixed set of document ordinals (a topic) that the reader's words narrow. */
export async function mountResults(root, { within = null, placeholder = "Search the archive" } = {}) {
  const scope = within ? new Set(within) : null;
  const restrict = (found) => (scope ? { ...found, ordinals: found.ordinals.filter((n) => scope.has(n)) } : found);
  const [data, meta, { dataset }] = await Promise.all([catalog(), indexMeta(), sources()]);
  const facets = await fetchJSON(await dataUrl("facets.json"));
  setStopwords(meta.stopwords);
  setVariants(meta.synonyms);
  const index = new Index(await dataUrl("index/"), meta.shards, (url) => fetchJSON(url));
  // Account books are collections too, named in the filters by their titles rather than their IDs.
  const folderTitles = Object.fromEntries([...data.folders, ...(data.volumes || [])].map((f) => [f.id, f.title]));
  let state = readState();
  let request = 0;

  root.innerHTML = `
    <form class="search-top" role="search">
      <div class="searchbar compact"><label class="input">${icon("search")}<span class="sr-only">Search</span>
        <input id="q" type="search" autocomplete="off" placeholder="${esc(placeholder)}"></label><button type="submit">Search</button></div>
      <div class="search-options">
        <div class="segmented" role="radiogroup" aria-label="Match">
          ${[["all", "All words"], ["any", "Any word"], ["exact", "Exact phrase"]].map(([v, l]) =>
            `<label><input type="radio" name="mode" value="${v}"><span>${l}</span></label>`).join("")}</div>
        <label class="switch" id="meaning-switch" hidden title="Also finds documents that say the same thing in other words. The first search downloads a 32 MB model once.">
          <input type="checkbox" id="meaning" role="switch"><span class="track" aria-hidden="true"></span><span>Search by meaning</span></label>
        <button type="button" class="btn link" id="adv-toggle" aria-expanded="false">${icon("sliders-horizontal")}Advanced search</button>
        <a class="btn link" href="about.html#search">${icon("info")}Search tips</a>
      </div>
      <div class="advanced" id="advanced" hidden></div>
    </form>
    <button type="button" class="btn filters-toggle" aria-expanded="false" aria-controls="refine-panel">${icon("funnel")}<span>Filters</span></button>
    <div class="search-layout">
      <aside class="refine" id="refine-panel" aria-label="Refine results"></aside>
      <section aria-labelledby="results-count">
        <div class="results-bar">
          <div class="count-line" id="results-count" tabindex="-1" aria-live="polite"></div>
          <div class="results-tools">
            <label class="sr-only" for="sort">Sort by</label><select id="sort">
              <option value="">Best match</option><option value="oldest">Date: oldest first</option>
              <option value="newest">Date: newest first</option><option value="id">Document ID</option></select>
            <label class="sr-only" for="per">Results per page</label><select id="per"><option value="20">20 per page</option>
              <option value="50">50 per page</option><option value="100">100 per page</option></select>
            <div class="segmented" role="radiogroup" aria-label="View">
              <label><input type="radio" name="view" value="list"><span>${icon("list")} List</span></label>
              <label><input type="radio" name="view" value="grid"><span>${icon("layout-grid")} Grid</span></label></div>
          </div>
        </div>
        <div class="active-filters" id="active-filters" hidden></div>
        <p class="details-line" id="details" hidden></p>
        <p class="details-line meaning-status" id="meaning-status" aria-live="polite" hidden></p>
        <div id="results-wrap"><div class="results" id="rows"></div></div>
        <nav class="pager" id="pager" aria-label="Pages"></nav>
      </section>
    </div>`;

  // The whole archive's date span, so the date slider's ends stay put as results change.
  const [firstYear, lastYear] = yearBounds(facets.year);
  // Search by meaning loads only when used, and is off by default for visitors whose browser asks to save data.
  const semantic = scope ? null : createSemantic({ dataUrl, vendor: new URL("../vendor/", import.meta.url).href });
  const meaningBox = root.querySelector("#meaning");
  const remembered = (() => { try { return localStorage.getItem("edison.meaning"); } catch { return null; } })();
  meaningBox.checked = remembered ? remembered === "1" : !(navigator.connection && navigator.connection.saveData);
  semantic?.available().then((ok) => { root.querySelector("#meaning-switch").hidden = !ok; });
  const form = root.querySelector("form");
  const input = root.querySelector("#q");
  const refine = root.querySelector(".refine");
  // On narrow screens the filters fold away behind a button, so the results are the first thing below the search box.
  const filtersButton = root.querySelector(".filters-toggle");
  const showFilters = (open) => {
    filtersButton.setAttribute("aria-expanded", String(open));
    refine.classList.toggle("open", open);
  };
  filtersButton.addEventListener("click", () => showFilters(filtersButton.getAttribute("aria-expanded") !== "true"));
  // The panel's own Show results button closes it and takes the reader to what the filters left.
  refine.addEventListener("click", (event) => {
    if (!event.target.closest(".refine-done")) return;
    showFilters(false);
    const count = root.querySelector("#results-count");
    count.scrollIntoView({ block: "start" });
    count.focus({ preventScroll: true });
  });
  const sync = () => {
    input.value = state.q;
    root.querySelector(`input[name=mode][value="${state.mode}"]`).checked = true;
    root.querySelector(`input[name=view][value="${state.view}"]`).checked = true;
    root.querySelector("#sort").value = state.sort;
    root.querySelector("#per").value = String(state.per);
    root.querySelector("#results-wrap").className = state.view === "grid" ? "grid-view" : "";
  };
  sync();
  enhanceSelect(root.querySelector("#sort"), { label: "Sort by" });
  enhanceSelect(root.querySelector("#per"), { label: "Results per page" });

  // Advanced search writes the ordinary query, so the result stays a plain, shareable link.
  const adv = root.querySelector("#advanced");
  adv.innerHTML = `<div class="adv-rows"></div><div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    <button type="button" class="btn" id="adv-add">Add a row</button><button type="button" class="btn primary" id="adv-run">Search</button>
    <span class="muted adv-preview" style="font-size:.86rem">Your search: <code></code></span></div>`;
  const addRow = () => {
    const row = document.createElement("div");
    row.className = "adv-row";
    row.innerHTML = `<select aria-label="Join"><option value="AND">AND</option><option value="OR">OR</option><option value="NOT">NOT</option></select>
      <select aria-label="Field">${ADVANCED_FIELDS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <input aria-label="Words" placeholder="Words or a phrase"><button type="button" class="btn">Remove</button>`;
    row.querySelector("button").addEventListener("click", () => { row.remove(); preview(); });
    row.querySelectorAll("select, input").forEach((el) => el.addEventListener("input", preview));
    adv.querySelector(".adv-rows").appendChild(row);
    const [join, field] = row.querySelectorAll("select");
    enhanceSelect(join, { label: "Join" });
    enhanceSelect(field, { label: "Field" });
  };
  const built = () => [...adv.querySelectorAll(".adv-row")].map((row, n) => {
    const [join, field] = row.querySelectorAll("select");
    const words = row.querySelector("input").value.trim();
    if (!words) return "";
    const term = (field.value ? `${field.value}:` : "") + (/\s/.test(words) ? `"${words.replace(/"/g, "")}"` : words);
    if (join.value === "NOT") return `-${term}`;
    return (n && join.value === "OR" ? "OR " : "") + term;
  }).filter(Boolean).join(" ");
  const preview = () => { adv.querySelector(".adv-preview code").textContent = built() || "(add words above)"; };
  addRow(); addRow();
  adv.querySelector("#adv-add").addEventListener("click", addRow);
  adv.querySelector("#adv-run").addEventListener("click", () => {
    state = { ...state, q: built(), mode: "all", page: 1 };
    sync();
    writeState(state, true);
    run(true);
  });
  root.querySelector("#adv-toggle").addEventListener("click", (event) => {
    adv.hidden = !adv.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!adv.hidden));
    preview();
  });

  const update = (changes, focus = false, reveal = false) => {
    state = { ...state, ...changes };
    sync();
    writeState(state, true);
    run(focus, reveal);
  };
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    update({ q: input.value.trim(), mode: root.querySelector("input[name=mode]:checked").value, page: 1 }, true);
  });
  root.querySelectorAll("input[name=mode]").forEach((r) => r.addEventListener("change", () => { if (input.value.trim()) form.requestSubmit(); }));
  root.querySelectorAll("input[name=view]").forEach((r) => r.addEventListener("change", () => update({ view: r.value })));
  meaningBox.addEventListener("change", () => {
    try { localStorage.setItem("edison.meaning", meaningBox.checked ? "1" : "0"); } catch { /* A private window keeps the choice for this page only. */ }
    run();
  });
  root.querySelector("#sort").addEventListener("change", (e) => update({ sort: e.target.value, page: 1 }));
  root.querySelector("#per").addEventListener("change", (e) => update({ per: Number(e.target.value), page: 1 }));
  window.addEventListener("popstate", () => { state = readState(); sync(); run(); });

  async function run(focus = false, reveal = false) {
    const mine = ++request;
    const requestedPage = state.page;
    const perPage = state.per;
    const countEl = root.querySelector("#results-count");
    const rowsEl = root.querySelector("#rows");
    const slow = setTimeout(() => { if (mine === request) countEl.textContent = "Searching…"; }, 300);
    try {
      let own = parseQuery(state.q, state.mode);
      let query = own;
      const filters = { yearFrom: state.from || own.yearFrom, yearTo: state.to || own.yearTo,
        types: new Set([...state.types].map((t) => facets.types.indexOf(t)).filter((n) => n >= 0)),
        folders: new Set([...state.folders].map((f) => facets.folders.indexOf(f)).filter((n) => n >= 0)),
        statuses: new Set([...state.statuses].map((s) => facets.statuses.indexOf(s)).filter((n) => n >= 0)) };
      let result = restrict(await runQuery(query, index, meta));
      if (mine !== request) return;
      let { kept, counts } = applyFilters(result.ordinals, facets, filters);
      // The query as typed, kept so that meaning matches can take the place of the "most of your words" fallback.
      const strict = { result, kept, query, own };
      let broadened = false;
      let meaningOnly = false;
      // No document holds every word: show the ones holding most of them, best first, and say so, as search engines do.
      if (!kept.length && state.mode === "all" && own.clauses.filter((c) => !c.negate).length > 1) {
        const loose = parseQuery(state.q, "any");
        const looser = restrict(await runQuery(loose, index, meta));
        if (mine !== request) return;
        const relaxed = applyFilters(looser.ordinals, facets, filters);
        if (relaxed.kept.length) {
          ({ kept, counts } = relaxed);
          result = looser;
          query = loose;
          own = { ...loose, notes: own.notes };
          broadened = true;
        }
      }
      // Draws one set of results; the meaning pass calls it again with the merged list, so both look and behave the same.
      const present = async (shown, shownCounts, ordered, { meaning = new Set(), extra = "" } = {}) => {
        const hints = shown.length ? [] : await suggest(own, index);
        const pages = Math.max(1, Math.ceil(ordered.length / perPage));
        const page = Math.min(requestedPage, pages);
        const slice = ordered.slice((page - 1) * perPage, page * perPage);
        const items = slice.length ? await summaries(slice) : [];
        if (mine !== request) return null;
        clearTimeout(slow);
        countEl.textContent = ordered.length ? `${plural(ordered.length, "result")}${pages > 1 ? ` · page ${page} of ${pages}` : ""}` : "No results";
        const details = root.querySelector("#details");
        const notes = own.notes || [];
        details.hidden = !(own.clauses.length || own.yearFrom || notes.length);
        const lead = meaningOnly ? "No document has all of your words, so these are the documents closest in meaning."
          : `${broadened ? "No document has all of your words, so these have most of them, best matches first. " : ""}${describeQuery(own)}`;
        details.innerHTML = `${icon("info")} ${esc(lead)}${notes.length ? ` ${notes.map(esc).join(" ")}` : ""}${extra ? ` ${esc(extra)}` : ""}`;
        rowsEl.innerHTML = items.length ? items.map((s) => documentEntry(s, dataset[0], {
          title: highlight(s.title, query, esc), description: s.description ? highlight(s.description, query, esc) : null,
          matched: query.clauses.length && !meaning.has(s.ordinal) ? maskFields(result.where.get(s.ordinal) || 0).join(", ") : "",
          byMeaning: meaning.has(s.ordinal),
        })).join("") : emptyState(hints);
        renderPager(pages, page);
        renderRefine(shownCounts, filters);
        return page;
      };
      const sort = state.sort || (query.clauses.length ? "" : "oldest");
      let page = await present(kept, counts, sortResults(kept, sort, result.scores, facets));
      if (mine !== request) return;
      if (focus) countEl.focus();
      if (reveal) revealResults();
      const status = root.querySelector("#meaning-status");
      status.hidden = true;
      if (!meaningBox.checked || !semantic || !eligible(own, state.q)) {
        state.page = page;
        return;
      }
      // Keyword results are already showing; documents that match by meaning are merged in when the model has answered.
      const waiting = setTimeout(() => {
        if (mine !== request) return;
        status.textContent = "Also looking for documents that match by meaning…";
        status.hidden = false;
      }, 400);
      try {
        const best = await semantic.search(meaningText(state.q));
        if (mine !== request) return;
        const cutoff = meaningCutoff(best);
        const closest = topMatches(best, { cutoff, limit: Infinity });
        if (closest.length || (broadened && strict.result.ordinals.length)) {
          if (broadened) {
            ({ result, kept, query, own } = strict);
            broadened = false;
          }
          // Count all qualifying matches so the result cap cannot disable a filter.
          const candidates = new Set([...result.ordinals, ...closest]);
          const { kept: matching, counts: facetCounts } = applyFilters(candidates, facets, filters);
          const allowed = new Set(matching);
          const inScope = closest.filter((ordinal) => allowed.has(ordinal));
          meaningOnly = !kept.length && inScope.length > 0;
          const found = new Set(kept);
          // Cap semantic additions after filtering, so overlapping keyword matches never consume their slots.
          const additions = (ordinals, keywords) => ordinals.filter((ordinal) => !keywords.has(ordinal)).slice(0, 40);
          const fresh = additions(inScope, found);
          const meaning = new Set(fresh);
          const byMeaning = inScope.filter((ordinal) => found.has(ordinal) || meaning.has(ordinal));
          const union = [...kept, ...fresh];
          const fused = fuse([sortResults(kept, "", result.scores, facets), byMeaning],
            { weight: (list, ordinal) => list === 0 ? keywordWeight(best.get(ordinal), cutoff) : 1 });
          const order = sort ? sortResults(union, sort, result.scores, facets)
            : union.slice().sort((a, b) => (fused.get(b) || 0) - (fused.get(a) || 0) || (best.get(b) ?? -1) - (best.get(a) ?? -1) || a - b);
          page = await present(union, facetCounts, order, {
            meaning, extra: fresh.length && !meaningOnly ? `Also showing ${plural(fresh.length, "document")} that match by meaning.` : "" });
        }
      } catch {
        // Search by meaning is an extra; when the model or vectors cannot load, the keyword results simply stand.
      } finally {
        clearTimeout(waiting);
        if (mine === request) {
          state.page = page;
          status.hidden = true;
        }
      }
    } catch (error) {
      clearTimeout(slow);
      if (mine !== request) return;
      countEl.textContent = "Search is unavailable right now";
      rowsEl.innerHTML = `<div class="notice warn">${icon("info")}<span>The search index could not be loaded (${esc(error.message)}).
        Please try again in a moment; browsing <a href="collections.html">collections</a> still works.</span></div>`;
    }
  }

  function emptyState(hints = []) {
    const broader = stateParams({ ...state, mode: "any", page: 1 });
    const corrected = hints.length ? hints.reduce((text, h) => text.replace(new RegExp(`\\b${h.typed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i"), h.suggestion), state.q) : "";
    const fixedLink = corrected ? stateParams({ ...state, q: corrected, page: 1 }) : null;
    return `<div class="empty-state"><h2>No results</h2>
      ${fixedLink ? `<p>Did you mean <a href="?${esc(fixedLink.toString())}"><strong>${esc(corrected)}</strong></a>?</p>` : ""}
      <p>Try fewer words, choose <strong>Any word</strong>, or remove a filter.</p>
      ${state.mode !== "any" && state.q ? `<p><a href="?${esc(broader.toString())}">Search “${esc(state.q)}” more broadly</a></p>` : ""}</div>`;
  }

  function renderPager(pages, page) {
    const pager = root.querySelector("#pager");
    if (pages < 2) { pager.innerHTML = ""; return; }
    const list = [...new Set([1, pages, page - 1, page, page + 1].filter((n) => n >= 1 && n <= pages))].sort((a, b) => a - b);
    let html = `<button class="btn" data-page="${page - 1}" ${page === 1 ? "disabled" : ""}>${icon("chevron-left")}Previous</button>`;
    list.forEach((n, i) => {
      if (i && n - list[i - 1] > 1) html += `<span class="muted" aria-hidden="true">…</span>`;
      html += `<button class="btn" data-page="${n}" ${n === page ? 'aria-current="page"' : ""}>${n}</button>`;
    });
    html += `<button class="btn" data-page="${page + 1}" ${page === pages ? "disabled" : ""}>Next${icon("chevron-right")}</button>`;
    pager.innerHTML = html;
    pager.querySelectorAll("button[data-page]").forEach((b) => b.addEventListener("click", () => {
      update({ page: Number(b.dataset.page) }, true);
      root.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
  }

  // One bar per decade across the archive's whole span, so the bars line up with the slider beneath them.
  function allDecades() {
    const out = [];
    for (let d = Math.floor(firstYear / 10) * 10; d <= lastYear; d += 10) out.push(d);
    return out;
  }

  // Which long lists are opened past their first few values, and what was typed to find one, kept across re-renders.
  const expanded = new Set();
  const finds = new Map();
  const FIRST = 5;
  const sets = { status: "statuses", type: "types", folder: "folders" };
  // Each list keeps one order, the whole archive's largest first, so a value never moves when another filter is chosen.
  const archiveOrder = (column, names) => {
    const totals = new Map();
    for (const value of facets[column]) if (value >= 0) totals.set(value, (totals.get(value) || 0) + 1);
    return [...totals.entries()].sort((a, b) => b[1] - a[1] || String(facets[names][a[0]]).localeCompare(String(facets[names][b[0]]))).map(([i]) => i);
  };
  const order = { status: archiveOrder("status", "statuses"), type: archiveOrder("type", "types"), folder: archiveOrder("folder", "folders") };

  // Hides values past the first few unless the list is opened, a value is chosen, or it matches what was typed.
  function showValues(fieldset) {
    const key = fieldset.dataset.key;
    const typed = (finds.get(key) || "").trim().toLowerCase();
    const labels = [...fieldset.querySelectorAll("label.opt")];
    let folded = 0;
    labels.forEach((label, n) => {
      const chosen = label.querySelector("input").checked;
      label.hidden = typed ? !label.textContent.toLowerCase().includes(typed) : !(expanded.has(key) || n < FIRST || chosen);
      if (!typed && label.hidden) folded++;
    });
    const more = fieldset.querySelector(".facet-more");
    if (more) {
      more.hidden = Boolean(typed) || labels.length <= FIRST;
      more.setAttribute("aria-expanded", String(expanded.has(key)));
      more.querySelector("span").textContent = expanded.has(key) ? "Show fewer" : `Show ${folded} more`;
    }
    const none = fieldset.querySelector(".facet-none");
    if (none) none.hidden = !typed || labels.some((label) => !label.hidden);
  }

  // The focused control's identity, so the same control can take focus back after the sidebar is redrawn.
  const focusKey = (el) => (!el || !refine.contains(el) ? null
    : el.matches("input[type=checkbox]") ? `input[name="${el.name}"][value="${CSS.escape(el.value)}"]`
      : el.id ? `#${CSS.escape(el.id)}` : el.dataset.key ? `${el.tagName.toLowerCase()}.${el.className.split(" ")[0]}[data-key="${el.dataset.key}"]`
        : el.matches("summary") ? `details[data-key="${el.parentElement.dataset.key}"] > summary` : null);

  function renderRefine(counts, filters) {
    const refocus = focusKey(document.activeElement);
    const open = new Set([...refine.querySelectorAll("details[open]")].map((d) => d.dataset.key));
    const isOpen = (key) => (refine.children.length ? open.has(key) : true);
    const decades = new Map();
    for (const [year, n] of counts.year) decades.set(Math.floor(year / 10) * 10, (decades.get(Math.floor(year / 10) * 10) || 0) + n);
    const max = Math.max(1, ...decades.values());
    // A value with nothing in these results stays in its place, dimmed, rather than vanishing and pulling the list up.
    const option = (name, value, label, n, on) => `<label class="opt${on ? " on" : ""}${n || on ? "" : " empty"}"><input type="checkbox" name="${name}" value="${esc(value)}"
      ${on ? "checked" : ""} ${n || on ? "" : "disabled"}><span class="opt-label" title="${esc(label)}">${esc(label)}</span><span class="n">${n.toLocaleString()}</span></label>`;
    const group = (key, title, body) => `<details data-key="${key}" ${isOpen(key) ? "open" : ""}><summary>${title}${icon("chevron-down")}</summary>
      <fieldset data-key="${key}"><legend class="sr-only">${title}</legend>${body || '<span class="facet-empty">None in the archive</span>'}</fieldset></details>`;
    // A list of values: a box to find one when the list is long, the values in archive order, and a button for the rest.
    const values = (key, noun, labelOf) => {
      const rows = order[key].map((i) => [facets[{ status: "statuses", type: "types", folder: "folders" }[key]][i], counts[key].get(i) || 0]);
      if (!rows.length) return "";
      const find = rows.length > 8 ? `<label class="facet-find">${icon("search")}<span class="sr-only">Find a ${noun}</span>
        <input type="search" data-key="${key}" placeholder="Find a ${noun}" autocomplete="off" value="${esc(finds.get(key) || "")}"></label>` : "";
      return find + rows.map(([value, n]) => option(key, value, labelOf(value), n, state[sets[key]].has(value))).join("")
        + `<p class="facet-none" hidden>No ${noun} matches</p>`
        + (rows.length > FIRST ? `<button type="button" class="facet-more" data-key="${key}" aria-expanded="false">${icon("chevron-down")}<span></span></button>` : "");
    };
    refine.innerHTML = `<div class="refine-title"><span>Refine results</span></div>
      ${group("date", "Date", `<div class="hist-wrap"><div class="hist" aria-hidden="true">${allDecades().map((d) => `<span data-h="${Math.round((decades.get(d) || 0) / max * 100)}"
        class="${(!filters.yearFrom || d + 9 >= filters.yearFrom) && (!filters.yearTo || d <= filters.yearTo) ? "on" : ""}"></span>`).join("")}</div>
        <div class="range-host"></div></div>
        <div class="hist-axis" aria-hidden="true"><span>${firstYear}</span><span>${lastYear}</span></div>
        <div class="years"><label class="sr-only" for="yf">From year</label><input id="yf" type="number" min="1800" max="1999" placeholder="From" value="${state.from || ""}">
        <span>to</span><label class="sr-only" for="yt">To year</label><input id="yt" type="number" min="1800" max="1999" placeholder="To" value="${state.to || ""}"></div>`)}
      ${group("status", "Transcription", values("status", "status", (s) => (STATUS[s] ? STATUS[s].label : s)) + `<div class="facet-help">${statusHelp()}</div>`)}
      ${group("type", "Document type", values("type", "type", (t) => t))}
      ${group("folder", "Collection", values("folder", "collection", (f) => folderTitles[f] || f))}
      <button type="button" id="refine-done" class="btn primary refine-done">Show results</button>`;
    renderChips();
    refine.querySelectorAll(".hist span[data-h]").forEach((bar) => { bar.style.height = `${Math.max(2, Number(bar.dataset.h))}%`; });
    const host = refine.querySelector(".range-host");
    if (host && lastYear > firstYear) {
      rangeSlider(host, { min: firstYear, max: lastYear, from: state.from || filters.yearFrom, to: state.to || filters.yearTo, label: "Years",
        onChange: (from, to) => update({ from, to, page: 1 }, false, true) });
    }
    wireHelp(refine);
    refine.querySelectorAll("fieldset[data-key]").forEach(showValues);
    refine.querySelectorAll(".facet-more").forEach((button) => button.addEventListener("click", () => {
      const key = button.dataset.key;
      if (expanded.has(key)) expanded.delete(key); else expanded.add(key);
      showValues(button.closest("fieldset"));
    }));
    refine.querySelectorAll(".facet-find input").forEach((box) => box.addEventListener("input", () => {
      finds.set(box.dataset.key, box.value);
      showValues(box.closest("fieldset"));
    }));
    refine.querySelectorAll("input[type=checkbox]").forEach((box) => box.addEventListener("change", () => {
      const key = sets[box.name];
      const next = new Set(state[key]);
      if (box.checked) next.add(box.value); else next.delete(box.value);
      update({ [key]: next, page: 1 }, false, true);
    }));
    refine.querySelectorAll("#yf, #yt").forEach((el) => el.addEventListener("change", () => update({
      from: Number(refine.querySelector("#yf").value) || null, to: Number(refine.querySelector("#yt").value) || null, page: 1 }, false, true)));
    // The control just used keeps focus, so keyboard and screen-reader users stay where they were.
    const again = refocus && refine.querySelector(refocus);
    if (again) again.focus({ preventScroll: true });
  }

  // Filters in use, shown above the results where they never push the sidebar's lists down.
  function renderChips() {
    const chips = [];
    if (state.from || state.to) chips.push(["years", "", `${state.from || firstYear}–${state.to || lastYear}`]);
    for (const s of state.statuses) chips.push(["statuses", s, STATUS[s] ? STATUS[s].label : s]);
    for (const t of state.types) chips.push(["types", t, t]);
    for (const f of state.folders) chips.push(["folders", f, folderTitles[f] || f]);
    const bar = root.querySelector("#active-filters");
    bar.hidden = !chips.length;
    filtersButton.querySelector("span").textContent = chips.length ? `Filters · ${chips.length} in use` : "Filters";
    bar.innerHTML = chips.length ? `<span class="active-label">Filtered by</span>${chips.map(([k, v, label]) => `<button type="button" class="filter-chip"
      data-k="${k}" data-v="${esc(v)}" aria-label="Remove filter ${esc(label)}" title="Remove filter: ${esc(label)}"><span>${esc(label)}</span>${icon("x")}</button>`).join("")}
      <button type="button" class="btn link" id="clear">Clear all</button>` : "";
    bar.querySelectorAll(".filter-chip").forEach((chip) => chip.addEventListener("click", () => {
      if (chip.dataset.k === "years") { update({ from: null, to: null, page: 1 }, false, true); return; }
      const next = new Set(state[chip.dataset.k]);
      next.delete(chip.dataset.v);
      update({ [chip.dataset.k]: next, page: 1 }, false, true);
    }));
    const clear = bar.querySelector("#clear");
    if (clear) clear.addEventListener("click", () => update({ types: new Set(), folders: new Set(), statuses: new Set(), from: null, to: null, page: 1 }, false, true));
  }

  // The sidebar scrolls with the page and holds still once its top or its bottom reaches the window's edge, so it needs no scrollbar of its own.
  const HEADER = 80;
  let stickTop = HEADER;
  let lastY = window.scrollY;
  function follow() {
    const lowest = Math.min(HEADER, window.innerHeight - refine.offsetHeight - 24);
    stickTop = Math.max(lowest, Math.min(HEADER, stickTop - (window.scrollY - lastY)));
    lastY = window.scrollY;
    refine.style.top = `${stickTop}px`;
  }
  window.addEventListener("scroll", follow, { passive: true });
  window.addEventListener("resize", follow);
  new ResizeObserver(follow).observe(refine);

  // After a filter change, the first results come into view when the reader had scrolled past them, as a fresh results page would open.
  function revealResults() {
    const section = root.querySelector("#results-count").closest("section");
    if (section.getBoundingClientRect().top >= HEADER) return;
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: window.scrollY + section.getBoundingClientRect().top - HEADER - 8, behavior: smooth ? "smooth" : "auto" });
  }

  run();
}
