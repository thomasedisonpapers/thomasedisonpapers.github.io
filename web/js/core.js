// Shared by every page: safe fetching, where files live, the header and footer, icons, badges and thumbnails.

const TIMEOUT_MS = 15000;
const ID = /^[A-Za-z0-9_-]{1,64}$/;
const REVISION = /^[0-9a-f]{16}$/;
// Hosts that answered 429 or kept failing are skipped for the rest of the visit instead of being hammered.
const pausedHosts = new Set();
const failures = new Map();
// Listeners are attached only in a browser, so the renderers below can also be tested in Node.
const BROWSER = typeof document !== "undefined";
const SPRITE = new URL("../img/icons.svg", import.meta.url).href;
const LOGO = new URL("../img/logo.svg", import.meta.url).href;
let pointerCheck = null;
let reloadRequested = false;

export function esc(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

export function icon(name, extra = "") {
  return `<svg class="icon ${extra}" aria-hidden="true" focusable="false"><use href="${SPRITE}#i-${name}"></use></svg>`;
}

export function logoUrl() {
  return LOGO;
}

export function validId(value) {
  return typeof value === "string" && ID.test(value) ? value : null;
}

export function param(name) {
  return new URLSearchParams(location.search).get(name) || "";
}

function hostOf(url) {
  try { return new URL(url, location.href).host; } catch { return ""; }
}

async function fetchOnce(url, { noStore = false } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(url, { signal: controller.signal, cache: noStore ? "no-store" : "default" });
    if (response.status === 429) pausedHosts.add(hostOf(url));
    return response;
  } finally {
    clearTimeout(timer);
  }
}

// One retry with jitter for network errors and 5xx; a 404 is final, so the caller moves on to the next source.
export async function fetchJSON(urls, options = {}) {
  const list = Array.isArray(urls) ? urls : [urls];
  let lastError = null;
  for (const url of list) {
    const host = hostOf(url);
    // The site's own host is never skipped: without it nothing works, so it is always worth asking again.
    const own = host === location.host;
    if (!own && pausedHosts.has(host)) continue;
    for (let attempt = 0; attempt < 2; attempt++) {
      let response;
      try {
        response = await fetchOnce(url, options);
      } catch (error) {
        lastError = error;
        const count = (failures.get(host) || 0) + 1;
        failures.set(host, count);
        if (count >= 3 && !own) pausedHosts.add(host);
        if (attempt === 0) await new Promise((r) => setTimeout(r, 400 + Math.random() * 600));
        continue;
      }
      if (response.ok) {
        failures.delete(host);
        // A broken body is a data problem, not a sign the host is down, so it never counts towards pausing.
        return await response.json();
      }
      lastError = new Error(`${response.status} for ${url}`);
      if (response.status === 404) await recoverRevision(url);
      if (response.status < 500) break;
      if (attempt === 0) await new Promise((r) => setTimeout(r, 400 + Math.random() * 600));
    }
  }
  throw lastError || new Error("nothing to fetch");
}

async function recoverRevision(url) {
  const missing = /^data\/([0-9a-f]{16})\//.exec(url)?.[1];
  if (!missing || reloadRequested) return;
  // Reload the page when a deploy removed its data so ordinal lookups stay on one revision.
  pointerCheck ??= fetchJSON("data/current.json", { noStore: true })
    .catch(() => null)
    .finally(() => { pointerCheck = null; });
  const current = await pointerCheck;
  if (!reloadRequested && typeof current?.revision === "string" && REVISION.test(current.revision) && current.revision !== missing) {
    reloadRequested = true;
    location.reload();
  }
}

// A cached promise that is forgotten when it fails, so one bad moment is not remembered for the whole visit.
export function remember(cache, key, make) {
  if (!cache.has(key)) {
    const promise = make();
    cache.set(key, promise);
    promise.catch(() => { if (cache.get(key) === promise) cache.delete(key); });
  }
  return cache.get(key);
}

let sourcesPromise = null;
// Where the dataset (records, page images, PDFs) lives; a host move is a change to sources.json alone.
export function sources() {
  if (!sourcesPromise) {
    sourcesPromise = fetchJSON("sources.json", { noStore: true }).catch(() => ({ dataset: ["../dataset/"] }));
  }
  return sourcesPromise;
}

export async function datasetUrls(path) {
  if (/^https?:\/\//.test(path)) return [path];
  const { dataset } = await sources();
  return dataset.map((base) => base + path);
}

export function thumbUrl(item, datasetBase) {
  if (!item || !item.thumb) return "";
  if (item.thumb_from === "web" || /^https?:\/\//.test(item.thumb)) return item.thumb;
  if (item.thumb_from === "dataset") return datasetBase + item.thumb;
  return item.thumb;
}

const cache = new Map();
// The build's pointer is always fetched fresh; every other data file is read from that build's own folder, never a newer one.
export function catalog() {
  return remember(cache, "catalog", () => fetchJSON("data/current.json", { noStore: true })
    .then((current) => fetchJSON(`${dataPath(current.revision)}catalog.json`).then((data) => ({ ...data, revision: current.revision }))));
}

export function dataPath(revision) {
  return `data/${encodeURIComponent(revision)}/`;
}

export async function dataUrl(path) {
  const { revision } = await catalog();
  return dataPath(revision) + path;
}

export async function summaries(ordinals) {
  const meta = await indexMeta();
  const needed = [...new Set(ordinals.map((n) => Math.floor(n / meta.per_file)))];
  const files = new Map(await Promise.all(needed.map(async (file) =>
    [file, await remember(cache, `docs/${file}`, async () => fetchJSON(await dataUrl(`docs/${file}.json`)))])));
  return ordinals.map((n) => ({ ...files.get(Math.floor(n / meta.per_file))[n % meta.per_file], ordinal: n }));
}

export function indexMeta() {
  return remember(cache, "meta", async () => fetchJSON(await dataUrl("index/meta.json")));
}

export const STATUS = {
  approved: { label: "Verified", icon: "badge-check", note: "An editor has checked this transcription against the original." },
  draft: { label: "Under review", icon: "clock", note: "Transcribed, and waiting for an editor to check it, so it may contain mistakes." },
  edition: { label: "Digital edition", icon: "book-open", note: "Transcribed for the Edison Papers digital edition." },
  none: { label: "Not transcribed", icon: "image", note: "The scan is here; the text has not been transcribed yet." },
};

export function badge(status) {
  const s = STATUS[status] || STATUS.none;
  return `<span class="badge ${esc(status in STATUS ? status : "none")}">${icon(s.icon)}${esc(s.label)}</span>`;
}

export function thumbHtml(item, datasetBase, { alt = "" } = {}) {
  const src = thumbUrl(item, datasetBase);
  const verified = item && item.status === "approved"
    ? `<span class="thumb-badge" title="Verified by an editor">${icon("badge-check")}<span class="sr-only">Verified</span></span>` : "";
  if (!src) return `<span class="thumb missing">${verified}</span>`;
  return `<span class="thumb"><img src="${esc(src)}" alt="${esc(alt)}" loading="lazy" decoding="async">${verified}</span>`;
}

// One capturing listener fades every thumbnail in, or marks it missing, without inline handlers the CSP would block.
BROWSER && document.addEventListener("load", (event) => {
  if (event.target.tagName === "IMG" && event.target.parentNode.classList.contains("thumb")) event.target.classList.add("loaded");
}, true);
BROWSER && document.addEventListener("error", (event) => {
  const img = event.target;
  if (img.tagName === "IMG" && img.parentNode && img.parentNode.classList.contains("thumb")) {
    img.parentNode.classList.add("missing");
    img.remove();
  }
}, true);

export function statusHelp() {
  return `<span class="help-pop"><button type="button" aria-expanded="false">${icon("info")}What do the labels mean?</button>
    <span class="pop" hidden role="note">${Object.keys(STATUS).map((key) =>
      `<span class="pop-row">${badge(key)}<span>${esc(STATUS[key].note)}</span></span>`).join("")}</span></span>`;
}

function closeHelp(except = null) {
  document.querySelectorAll(".help-pop > button[aria-expanded=true]").forEach((button) => {
    if (button === except) return;
    button.nextElementSibling.hidden = true;
    button.setAttribute("aria-expanded", "false");
  });
}

export function wireHelp(root = document) {
  root.querySelectorAll(".help-pop > button").forEach((button) => {
    if (button.dataset.wired) return;
    button.dataset.wired = "1";
    button.addEventListener("click", () => {
      const pop = button.nextElementSibling;
      closeHelp(button);
      pop.hidden = !pop.hidden;
      button.setAttribute("aria-expanded", String(!pop.hidden));
      if (!pop.hidden) placeHelp(button, pop);
    });
  });
}

// Below its button when there is room, above it otherwise, and always inside the window.
function placeHelp(button, pop) {
  const anchor = button.getBoundingClientRect();
  const width = pop.offsetWidth;
  const height = pop.offsetHeight;
  const below = anchor.bottom + 8 + height <= window.innerHeight - 8;
  pop.style.top = `${Math.max(8, below ? anchor.bottom + 8 : anchor.top - 8 - height)}px`;
  pop.style.left = `${Math.max(8, Math.min(anchor.left, window.innerWidth - width - 8))}px`;
}

// Scrolling or resizing would leave an open explanation floating away from its button, so it closes.
BROWSER && window.addEventListener("resize", () => closeHelp());
BROWSER && document.addEventListener("scroll", () => closeHelp(), true);

// Escape or a click anywhere else closes an open label explanation, as a popover should.
BROWSER && document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeHelp(); });
BROWSER && document.addEventListener("click", (event) => { if (!event.target.closest(".help-pop")) closeHelp(); });

const NAV = [["topics.html", "Topics"], ["collections.html", "Collections"], ["search.html", "Search"], ["about.html", "About"]];

function themeLabel() {
  const dark = document.documentElement.dataset.theme === "dark";
  return `${icon(dark ? "sun" : "moon")}<span>${dark ? "Light" : "Dark"}</span>`;
}

export function header(current, { search = true } = {}) {
  const el = document.querySelector("header.site-header");
  el.innerHTML = `<a class="skip" href="#main">Skip to content</a><div class="wrap">
    <a class="brand" href="index.html" aria-label="The Edison Papers, home"><img src="${esc(LOGO)}" alt="">
      <span class="brand-name">The Edison Papers<small>Rutgers University</small></span></a>
    <nav class="site-nav" aria-label="Main">${NAV.map(([href, label]) =>
      `<a href="${href}"${href === current ? ' aria-current="page"' : ""}>${label}</a>`).join("")}</nav>
    <div class="header-tools">
      ${search ? `<form class="header-search" action="search.html" role="search"><label class="field">${icon("search")}
        <span class="sr-only">Search the archive</span><input name="q" type="search" placeholder="Search the archive" autocomplete="off"></label></form>` : ""}
      <button class="theme-toggle" type="button">${themeLabel()}</button></div></div>`;
  const toggle = el.querySelector(".theme-toggle");
  toggle.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("archive-theme", next); } catch { /* a private window keeps the choice for this page only */ }
    toggle.innerHTML = themeLabel();
  });
  const q = param("q");
  const box = el.querySelector(".header-search input");
  if (q && box && current === "search.html") box.value = q;
}

export async function footer() {
  const el = document.querySelector("footer.site-footer");
  el.innerHTML = `<div class="wrap">
    <div><h3>The Edison Papers</h3><p>Documents from the Thomas A. Edison Papers at Rutgers, each shown beside its transcription.
      Documents marked Verified have been checked by an editor.</p></div>
    <div><h3>Explore</h3><ul><li><a href="topics.html">Topics</a></li><li><a href="collections.html">Collections</a></li>
      <li><a href="search.html">Search</a></li><li><a href="about.html">About and downloads</a></li></ul></div>
    <div><h3>Rights</h3><ul><li>Scans courtesy of Thomas Edison National Historical Park</li><li>Transcriptions and metadata: CC0 1.0</li>
      <li>Logo: “Thomas Alva Edison” by Phill, Noun Project</li></ul></div>
    <div class="legal"><span id="stamp">Loading catalogue…</span><span>Thomas A. Edison Papers, Rutgers University</span></div></div>`;
  try {
    const data = await catalog();
    const built = new Date(data.built_at);
    const days = (Date.now() - built.getTime()) / 86400000;
    el.querySelector("#stamp").textContent = `Catalogue updated ${built.toLocaleDateString(undefined, { dateStyle: "long" })}` +
      ` · ${data.documents.toLocaleString()} documents, ${data.approved.toLocaleString()} verified` +
      (days > 14 ? " · This catalogue is more than two weeks old, so newer documents may be missing." : "");
  } catch {
    el.querySelector("#stamp").textContent = "The catalogue could not be loaded just now.";
  }
}

// Only the site's own relative pages and plain web addresses may become links; anything else (javascript:, data:) stays text.
export function safeHref(href) {
  const value = String(href || "").trim();
  return /^(https?:\/\/|[a-z0-9_-]+\.html(\?|#|$)|\.{0,2}\/|\?|#)/i.test(value) && !/^[a-z]+:(?!\/\/)/i.test(value) ? value : null;
}

export function crumbs(items) {
  return `<nav class="crumbs" aria-label="Breadcrumb">${items.map(([label, href], n) => {
    const safe = href && safeHref(href);
    return (n ? icon("chevron-right") : "") + (safe ? `<a href="${esc(safe)}">${esc(label)}</a>` : `<span${href ? "" : ' aria-current="page"'}>${esc(label)}</span>`);
  }).join("")}</nav>`;
}

// The facts under a title, each with its icon, in the order archives list them: date, people, type, collection.
export function facts(s, { collection = true } = {}) {
  const items = [];
  if (s.date) items.push(`<li>${icon("calendar")}${esc(s.date)}</li>`);
  if (s.author || s.recipient) {
    items.push(`<li>${icon("user")}<span>${esc(s.author || "Unknown")}${s.recipient ? ` to ${esc(s.recipient)}` : ""}</span></li>`);
  }
  if (s.type) items.push(`<li>${icon("file-text")}${esc(s.type)}</li>`);
  if (collection && (s.folder_title || s.folder)) items.push(`<li>${icon("folder")}${esc(s.folder_title || s.folder)}</li>`);
  return items.length ? `<ul class="facts">${items.join("")}</ul>` : "";
}

export function plural(n, word) {
  return `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
}
