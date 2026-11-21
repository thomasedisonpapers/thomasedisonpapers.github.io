// A document read side by side: the scan with zoom, pan and turning on the left, its transcription and details on the right.
import { STATUS, badge, catalog, crumbs, dataPath, dataUrl, datasetUrls, esc, facts, fetchJSON, footer, header, icon, indexMeta, param, plural,
  sources, statusHelp, summaries, validId, wireHelp } from "./core.js";
import { Index, fold } from "./search.js";
import { renderTranscription } from "./transcript.js";
import { relatedShelf } from "./cards.js";

const MAX_ZOOM = 8;
const EDISONDIGITAL = "https://edisondigital.rutgers.edu/";
const NOTICES = {
  draft: ["clock", "Under review by admin — transcription may not be accurate", "This text was drafted and is waiting for an editor to check it against the original."],
  edition: ["book-open", "Transcription from the Edison Papers digital edition", ""],
};

async function locate(id) {
  const meta = await indexMeta();
  const index = new Index(await dataUrl("index/"), meta.shards, (url) => fetchJSON(url));
  const hits = await index.term(fold(id), meta.fields.id);
  const ordinal = [...hits.keys()][0];
  if (ordinal === undefined) return null;
  const [summary] = await summaries([ordinal]);
  if (!summary || summary.id !== id) return null;
  const { revision } = await catalog();
  return loadDocument(revision, id, summary.folder);
}

async function loadDocument(revision, id, folderId) {
  const base = dataPath(revision);
  if (!folderId) {
    const record = await fetchJSON(`${base}unfiled/${encodeURIComponent(id)}.json`);
    return { folder: null, record, position: -1 };
  }
  const folder = await fetchJSON(`${base}folders/${encodeURIComponent(folderId)}.json`);
  const position = folder.documents.findIndex((d) => d.id === id);
  return position === -1 ? null : { folder, record: folder.documents[position], position };
}

function citation(record) {
  const parts = [record.author, `“${record.title}”`, record.date, `Document ${record.id}`, "The Edison Papers, Rutgers University"];
  // The document's own address, without whatever page or view the reader had open, so a citation survives a move to a new domain.
  const address = new URL(`document.html?id=${encodeURIComponent(record.id)}`, location.href).href;
  return `${parts.filter(Boolean).join(", ")}. ${address} (accessed ${new Date().toLocaleDateString(undefined, { dateStyle: "long" })}).`;
}

function aboutTable(record) {
  const rows = [["Document ID", record.id], ["Title", record.title], ["Date", record.date], ["Type", record.type], ["Author", record.author],
    ["Recipient", record.recipient], ["Place", record.place], ["People mentioned", (record.names || []).concat(record.mentioned || [])],
    ["Places", record.places], ["Subjects", record.subjects], ["Microfilm", record.microfilm], ["Editors' notes", record.editors_notes],
    ["Marginal notes", record.marginal_notes], ["Pages", record.pages && record.pages.length ? String(record.pages.length) : ""]];
  const external = record.edisondigital && String(record.edisondigital.document || "").startsWith(EDISONDIGITAL);
  const collection = record.folder;
  const collectionLabel = esc(collection?.title || collection?.id);
  const collectionValue = collection?.id
    ? `<a href="folder.html?id=${encodeURIComponent(collection.id)}">${collectionLabel}</a>`
    : collectionLabel;
  return `<dl class="about-table">${rows.map(([label, value]) => {
    const list = Array.isArray(value) ? [...new Set(value.filter(Boolean))] : value ? [value] : [];
    return list.length ? `<dt>${esc(label)}</dt><dd>${list.map(esc).join("; ")}</dd>` : "";
  }).join("")}
    ${collectionValue ? `<dt>Collection</dt><dd>${collectionValue}</dd>` : ""}
    <dt>Transcription</dt><dd>${badge(record.status)}</dd>
    ${external ? `<dt>Also at</dt><dd><a href="${esc(record.edisondigital.document)}" rel="noopener" target="_blank">edisondigital.rutgers.edu ${icon("external-link")}</a></dd>` : ""}
  </dl><p style="margin-top:16px">${statusHelp()}</p>`;
}

// Related documents load after the page itself, so a slow or missing list never holds up reading.
async function showRelated(record) {
  const ordinals = (record.related || []).filter((n) => Number.isInteger(n) && n >= 0);
  if (!ordinals.length) return;
  try {
    const [items, { dataset }] = await Promise.all([summaries(ordinals), sources()]);
    const section = document.getElementById("related");
    const found = items.filter((s) => s && s.id);
    if (!section || !found.length) return;
    section.querySelector(".shelf").innerHTML = relatedShelf(found, dataset[0]);
    section.hidden = false;
  } catch {
    // The page is complete without them, so a failure leaves the section hidden.
  }
}

async function render() {
  header("");
  footer();
  const page = document.getElementById("page");
  const id = validId(param("id"));
  const found = id && await locate(id);
  if (!found || !found.record) {
    page.innerHTML = `${crumbs([["Home", "index.html"], ["Not found", null]])}<div class="empty-state"><h1>Document not found</h1>
      <p>No document has the ID ${esc(param("id"))}. <a href="search.html?q=${encodeURIComponent(param("id"))}">Search for it</a></p></div>`;
    return;
  }
  const { folder, record, position } = found;
  document.title = `${record.title} · The Edison Papers`;
  page.classList.add("doc-page");
  const notice = NOTICES[record.status];
  const pages = record.pages || [];
  const previous = folder?.documents[position - 1];
  const next = folder?.documents[position + 1];
  const updated = record.updated ? new Date(record.updated) : null;
  const trail = [["Home", "index.html"], ["Collections", "collections.html"]];
  if (folder) trail.push([folder.title, `folder.html?id=${encodeURIComponent(folder.id)}`]);
  trail.push([record.id, null]);

  page.innerHTML = `${crumbs(trail)}
  <div class="doc-head">
    <h1>${esc(record.title)}</h1>
    ${facts(record, { collection: false })}
    <div class="status-line">${badge(record.status)}<span>${esc((STATUS[record.status] || STATUS.none).note)}</span>
      ${updated && !Number.isNaN(updated.getTime()) ? `<span class="muted">Updated ${esc(updated.toLocaleDateString(undefined, { dateStyle: "long" }))}</span>` : ""}</div>
    ${notice ? `<div class="notice ${record.status === "draft" ? "warn" : "info"}" role="note">${icon(notice[0])}<span><strong>${esc(notice[1])}</strong>${notice[2] ? ` ${esc(notice[2])}` : ""}</span></div>` : ""}
    <p class="notice info" id="newer" hidden>${icon("info")}<span>A newer version of this document is available. <a href="">Refresh</a></span></p>
  </div>
  <div class="segmented mobile-switch" role="radiogroup" aria-label="Show">
    <label><input type="radio" name="show" value="scan" checked><span>${icon("image")} Scan</span></label>
    <label><input type="radio" name="show" value="text"><span>${icon("file-text")} Transcription</span></label></div>
  <div class="reader" data-show="scan">
    <section class="scan-pane" aria-label="Original scan">
      <div class="toolbar">
        ${tool("prev", "chevron-left", "Previous page", "Previous")}<span class="page-label" aria-live="polite"></span>
        ${tool("next", "chevron-right", "Next page", "Next")}<span class="spacer"></span>
        ${tool("out", "zoom-out", "Zoom out", "Zoom out")}${tool("in", "zoom-in", "Zoom in", "Zoom in")}<span class="sep" aria-hidden="true"></span>
        ${tool("fit", "maximize-2", "Fit the whole page", "Fit page")}${tool("width", "unfold-horizontal", "Fit the page width", "Fit width")}
        ${tool("turn", "rotate-cw", "Rotate a quarter turn", "Rotate")}<span class="sep" aria-hidden="true"></span>
        ${tool("focus", "maximize", "Focus: fill the window with the scan and text", "Focus")}
      </div>
      <div class="viewer" role="img" tabindex="0" aria-roledescription="scan viewer" aria-label="Scan. Plus and minus zoom, arrow keys move, R rotates.">
        <img alt=""><div class="state" hidden></div></div>
      ${pages.length > 1 ? `<nav class="strip" aria-label="Pages"></nav>` : ""}
    </section>
    <div class="splitter" role="separator" aria-orientation="vertical" tabindex="0" aria-valuemin="25" aria-valuemax="75"
      aria-label="Resize the scan and the transcription; use the arrow keys, or double-click to reset" title="Drag to resize"></div>
    <section class="text-pane" aria-label="Transcription and details">
      <div class="tabs" role="tablist">
        <button role="tab" aria-selected="true" data-tab="text" title="Transcription">${icon("file-text")}<span class="lbl">Transcription</span></button>
        <button role="tab" aria-selected="false" data-tab="details" title="About this document">${icon("info")}<span class="lbl">About</span></button>
        <button role="tab" aria-selected="false" data-tab="downloads" title="Download and cite">${icon("download")}<span class="lbl">Download &amp; cite</span></button></div>
      <div class="text-scroll">
        <div data-panel="text"><div class="transcription">${record.transcription ? renderTranscription(record.transcription)
          : `<p class="muted" style="font-family:var(--ui)">This document has not been transcribed yet. The scan is shown on the left.</p>`}</div></div>
        <div data-panel="details" hidden>${aboutTable(record)}</div>
        <div data-panel="downloads" hidden><div class="download-list">
          ${record.pdf ? `<a class="download" id="dl-pdf" href="#" download="${esc(record.id)}.pdf">${icon("file-text")}<div><strong>PDF</strong><span>Index, transcription and names, with its status</span></div></a>`
            : `<button class="download btn-reset" type="button" id="print" style="border:1px solid var(--hairline);background:none;text-align:left;font:inherit;cursor:pointer">${icon("printer")}<div><strong>Print or save as PDF</strong><span>The transcription and its details</span></div></button>`}
          <a class="download" id="dl-json" href="#" download="${esc(record.id)}.json">${icon("braces")}<div><strong>Data (JSON)</strong><span>Every field of this record</span></div></a>
          ${pages.length ? `<div class="download" style="flex-wrap:wrap">${icon("image")}<div><strong>Page images</strong><span>Full size: ${pages.map((p) => `<a data-full="${esc(p.n)}" href="#">page ${esc(p.n)}</a>`).join(", ")}</span></div></div>` : ""}
        </div>
        <h3 style="margin:24px 0 4px">Cite this document</h3><p class="cite" id="cite"></p>
        <button class="btn" type="button" id="copy-cite">${icon("copy")}Copy citation</button></div>
      </div>
    </section>
  </div>
  ${folder ? `<nav class="doc-nav" aria-label="Documents in this collection">
    ${previous ? `<a class="btn" href="document.html?id=${encodeURIComponent(previous.id)}">${icon("chevron-left")}${esc(previous.id)}</a>` : "<span></span>"}
    <a class="btn link" href="folder.html?id=${encodeURIComponent(folder.id)}">All ${plural(folder.documents.length, "document")} in this collection</a>
    ${next ? `<a class="btn" href="document.html?id=${encodeURIComponent(next.id)}">${esc(next.id)}${icon("chevron-right")}</a>` : "<span></span>"}
  </nav>` : ""}
  <section class="related-docs" id="related" hidden aria-labelledby="related-title">
    <div class="block-head"><div><h2 id="related-title">Related documents</h2>
      <p>Documents about similar things, found by meaning across every collection.</p></div></div>
    <div class="shelf"></div>
  </section>`;
  wireHelp();
  showRelated(record);
  await wireDownloads(record);
  wireTabs();
  const viewer = new Viewer(page.querySelector(".scan-pane"), pages, record);
  wireSplitter(page.querySelector(".reader"), page.querySelector(".splitter"));
  wireFocus(page.querySelector("[data-act=focus]"));
  page.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => viewer.show(Number(b.dataset.goto) - 1)));
  page.querySelectorAll("input[name=show]").forEach((r) => r.addEventListener("change", () => { page.querySelector(".reader").dataset.show = r.value; }));
  watchForNewer(record);
}

// A toolbar button keeps its name for screen readers and on hover even when a narrow pane hides its label.
function tool(act, iconName, title, label) {
  return `<button type="button" data-act="${act}" title="${esc(title)}" aria-label="${esc(title)}">${icon(iconName)}<span class="lbl">${esc(label)}</span></button>`;
}

const SPLIT_KEY = "archive-split";
const SPLIT_DEFAULT = 55;

// The divider between scan and text: dragged, moved with the arrow keys, reset with a double-click, and remembered.
function wireSplitter(reader, splitter) {
  const set = (percent) => {
    const value = Math.max(25, Math.min(75, Math.round(percent * 10) / 10));
    reader.style.setProperty("--split", `${value}%`);
    splitter.setAttribute("aria-valuenow", String(Math.round(value)));
    try { localStorage.setItem(SPLIT_KEY, String(value)); } catch { /* the split still works, it just is not remembered */ }
  };
  let saved = SPLIT_DEFAULT;
  try { saved = Number(localStorage.getItem(SPLIT_KEY)) || SPLIT_DEFAULT; } catch { /* storage can be blocked */ }
  set(saved);
  splitter.addEventListener("pointerdown", (event) => {
    splitter.setPointerCapture(event.pointerId);
    splitter.classList.add("dragging");
    document.body.classList.add("resizing");
  });
  splitter.addEventListener("pointermove", (event) => {
    if (!splitter.hasPointerCapture(event.pointerId)) return;
    const box = reader.getBoundingClientRect();
    set(((event.clientX - box.left) / box.width) * 100);
  });
  const stop = (event) => {
    if (splitter.hasPointerCapture(event.pointerId)) splitter.releasePointerCapture(event.pointerId);
    splitter.classList.remove("dragging");
    document.body.classList.remove("resizing");
  };
  splitter.addEventListener("pointerup", stop);
  splitter.addEventListener("pointercancel", stop);
  splitter.addEventListener("dblclick", () => set(SPLIT_DEFAULT));
  splitter.addEventListener("keydown", (event) => {
    const now = Number(splitter.getAttribute("aria-valuenow")) || SPLIT_DEFAULT;
    const moves = { ArrowLeft: now - 3, ArrowRight: now + 3, Home: SPLIT_DEFAULT };
    if (event.key in moves) { event.preventDefault(); set(moves[event.key]); }
  });
}

// Focus fills the window with the scan and the text; Escape or the same button brings the page back.
function wireFocus(button) {
  const sync = () => {
    const on = document.body.classList.contains("focus-mode");
    button.innerHTML = `${icon(on ? "minimize" : "maximize")}<span class="lbl">${on ? "Exit focus" : "Focus"}</span>`;
    button.setAttribute("aria-pressed", String(on));
  };
  button.addEventListener("click", () => { document.body.classList.toggle("focus-mode"); sync(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("focus-mode")) { document.body.classList.remove("focus-mode"); sync(); }
  });
  sync();
}

async function wireDownloads(record) {
  const page = document.getElementById("page");
  if (record.pdf) page.querySelector("#dl-pdf").href = (await datasetUrls(record.pdf))[0];
  // The record is offered as the reader sees it, made into a file in the browser so no extra request is needed.
  const blob = new Blob([JSON.stringify(record, null, 2)], { type: "application/json" });
  page.querySelector("#dl-json").href = URL.createObjectURL(blob);
  for (const link of page.querySelectorAll("[data-full]")) {
    const p = record.pages[Number(link.dataset.full) - 1];
    link.href = (await datasetUrls(p.full))[0];
    link.target = "_blank";
    link.rel = "noopener";
  }
  const print = page.querySelector("#print");
  if (print) print.addEventListener("click", () => window.print());
  const cite = citation(record);
  page.querySelector("#cite").textContent = cite;
  page.querySelector("#copy-cite").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    try { await navigator.clipboard.writeText(cite); button.lastChild.textContent = "Copied"; } catch { button.lastChild.textContent = "Select the text above to copy"; }
  });
}

function wireTabs() {
  const page = document.getElementById("page");
  const tabs = [...page.querySelectorAll(".tabs [role=tab]")];
  tabs.forEach((tab) => tab.addEventListener("click", () => {
    tabs.forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
    page.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== tab.dataset.tab; });
  }));
}

// Checks for a newer build every five minutes and offers a refresh when this document changed.
function watchForNewer(record) {
  setInterval(async () => {
    try {
      const current = await fetchJSON("data/current.json", { noStore: true });
      const known = (await catalog()).revision;
      if (current.revision === known) return;
      const fresh = (await loadDocument(current.revision, record.id, record.folder?.id))?.record;
      if (!fresh || fresh.hash !== record.hash) document.getElementById("newer").hidden = false;
    } catch { /* checking again in five minutes is enough */ }
  }, 300000);
}

class Viewer {
  constructor(pane, pages, record) {
    this.pane = pane;
    this.pages = pages;
    this.record = record;
    this.el = pane.querySelector(".viewer");
    this.img = this.el.querySelector("img");
    this.stateEl = this.el.querySelector(".state");
    this.index = 0;
    this.turn = 0;
    this.request = 0;
    this.pointers = new Map();
    const act = (name, fn) => pane.querySelector(`[data-act=${name}]`).addEventListener("click", fn);
    act("prev", () => this.show(this.index - 1));
    act("next", () => this.show(this.index + 1));
    act("in", () => this.zoomCenter(1.25));
    act("out", () => this.zoomCenter(0.8));
    act("fit", () => { this.mode = "page"; this.fit(); });
    act("width", () => { this.mode = "width"; this.fit(); });
    act("turn", () => { this.turn = (this.turn + 90) % 360; this.fit(); });
    // Positions come from the viewer's own box, not the transformed image under the pointer.
    const point = (e) => { const r = this.el.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    this.el.addEventListener("wheel", (e) => { e.preventDefault(); this.zoomAt(...point(e), e.deltaY < 0 ? 1.15 : 1 / 1.15); }, { passive: false });
    this.el.addEventListener("dblclick", (e) => this.zoomAt(...point(e), 2));
    this.el.addEventListener("pointerdown", (e) => this.down(e));
    this.el.addEventListener("pointermove", (e) => this.move(e));
    ["pointerup", "pointercancel", "pointerleave"].forEach((t) => this.el.addEventListener(t, (e) => this.up(e)));
    this.el.addEventListener("keydown", (e) => this.key(e));
    this.mode = "page";
    // A chosen fit holds as the window or the divider changes size; a reader's own zoom is left alone.
    new ResizeObserver(() => { if (this.img.naturalWidth && this.mode !== "free") this.fit(); }).observe(this.el);
    this.buildStrip();
    this.show(0);
  }

  async buildStrip() {
    const strip = this.pane.querySelector(".strip");
    if (!strip) return;
    for (const page of this.pages) {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("aria-label", `Page ${page.n}`);
      const img = document.createElement("img");
      img.alt = "";
      img.loading = "lazy";
      img.src = (await datasetUrls(page.thumb))[0];
      button.appendChild(img);
      button.addEventListener("click", () => this.show(page.n - 1));
      strip.appendChild(button);
    }
    this.markStrip();
  }

  markStrip() {
    this.pane.querySelectorAll(".strip button").forEach((b, n) => b.setAttribute("aria-current", String(n === this.index)));
  }

  // Each page tries its screen-size image on every source, then the full scan, before showing a Retry button.
  async show(n) {
    if (!this.pages.length) {
      this.message("No scan is available for this document.");
      return;
    }
    this.index = Math.max(0, Math.min(this.pages.length - 1, n));
    this.turn = 0;
    if (this.mode === "free") this.mode = "page";
    const page = this.pages[this.index];
    this.pane.querySelector(".page-label").textContent = `Page ${this.index + 1} of ${this.pages.length}`;
    this.pane.querySelector("[data-act=prev]").disabled = this.index === 0;
    this.pane.querySelector("[data-act=next]").disabled = this.index === this.pages.length - 1;
    this.markStrip();
    document.querySelectorAll(".tp-page-break").forEach((b) => b.classList.toggle("current", Number(b.dataset.page) === this.index + 1));
    const mine = ++this.request;
    const candidates = [...await datasetUrls(page.view), ...await datasetUrls(page.full)];
    this.message("Loading the scan…");
    this.img.style.visibility = "hidden";
    for (const url of candidates) {
      if (mine !== this.request) return;
      const ok = await new Promise((resolve) => {
        const timer = setTimeout(() => resolve(false), 15000);
        this.img.onload = () => { clearTimeout(timer); resolve(true); };
        this.img.onerror = () => { clearTimeout(timer); resolve(false); };
        this.img.src = url;
      });
      if (mine !== this.request) return;
      if (ok) {
        this.img.alt = `Page ${this.index + 1} of ${this.pages.length}: ${this.record.title}`;
        this.el.setAttribute("aria-label", `${this.img.alt}. Plus and minus zoom, arrow keys move, R rotates.`);
        this.stateEl.hidden = true;
        this.img.style.visibility = "visible";
        this.fit();
        return;
      }
    }
    this.message("This scan could not be loaded just now.", true);
  }

  message(text, retry = false) {
    this.stateEl.hidden = false;
    this.stateEl.innerHTML = `<div>${esc(text)}${retry ? `<br><button type="button" class="btn">${icon("rotate-cw")}Try again</button>` : ""}</div>`;
    if (retry) this.stateEl.querySelector("button").addEventListener("click", () => this.show(this.index));
  }

  dims() {
    const w = this.img.naturalWidth || 1;
    const h = this.img.naturalHeight || 1;
    return this.turn % 180 ? [h, w, w, h] : [w, h, w, h];
  }

  fit() {
    if (this.mode === "free") this.mode = "page";
    const [bw, bh, w, h] = this.dims();
    const vw = this.el.clientWidth;
    const vh = this.el.clientHeight;
    // The smallest zoom is always the whole page; "width" fills the pane's width and starts at the top of the page.
    this.fitScale = Math.min(vw / bw, vh / bh) * 0.96;
    this.scale = this.mode === "width" ? (vw / bw) * 0.96 : this.fitScale;
    this.tx = vw / 2 - (this.scale * w) / 2;
    this.ty = this.mode === "width" ? (this.scale * bh) / 2 - (this.scale * h) / 2 + vw * 0.02 : vh / 2 - (this.scale * h) / 2;
    this.apply();
  }

  apply() {
    const [, , w, h] = this.dims();
    // Turned about the page's centre, so fitting and zooming use the same centre whatever the turn.
    this.img.style.transform = `translate(${this.tx}px, ${this.ty}px) scale(${this.scale}) translate(${w / 2}px, ${h / 2}px) rotate(${this.turn}deg) translate(${-w / 2}px, ${-h / 2}px)`;
  }

  zoomAt(x, y, factor) {
    if (!this.fitScale) return;
    this.mode = "free";
    const next = Math.max(this.fitScale, Math.min(this.fitScale * MAX_ZOOM, this.scale * factor));
    const ratio = next / this.scale;
    this.tx = x - (x - this.tx) * ratio;
    this.ty = y - (y - this.ty) * ratio;
    this.scale = next;
    if (Math.abs(next - this.fitScale) < 1e-6) { this.fit(); return; }
    this.apply();
  }

  zoomCenter(factor) {
    this.zoomAt(this.el.clientWidth / 2, this.el.clientHeight / 2, factor);
  }

  down(e) {
    // A button inside the viewer (Try again) keeps its own click instead of starting a drag.
    if (e.target.closest("button")) return;
    this.el.setPointerCapture(e.pointerId);
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    this.el.classList.add("dragging");
  }

  move(e) {
    if (!this.pointers.has(e.pointerId)) return;
    this.mode = "free";
    const before = this.pointers.get(e.pointerId);
    if (this.pointers.size === 2) {
      // Two fingers: the change in their distance is the zoom, about their midpoint.
      const other = [...this.pointers.entries()].find(([id]) => id !== e.pointerId)[1];
      const rect = this.el.getBoundingClientRect();
      const was = Math.hypot(before.x - other.x, before.y - other.y);
      const now = Math.hypot(e.clientX - other.x, e.clientY - other.y);
      if (was > 0) this.zoomAt((e.clientX + other.x) / 2 - rect.left, (e.clientY + other.y) / 2 - rect.top, now / was);
    } else {
      this.tx += e.clientX - before.x;
      this.ty += e.clientY - before.y;
      this.apply();
    }
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  }

  pan(dx, dy) {
    this.mode = "free";
    this.tx += dx;
    this.ty += dy;
    this.apply();
  }

  up(e) {
    this.pointers.delete(e.pointerId);
    if (!this.pointers.size) this.el.classList.remove("dragging");
  }

  key(e) {
    const step = 60;
    const actions = {
      "+": () => this.zoomCenter(1.25), "=": () => this.zoomCenter(1.25), "-": () => this.zoomCenter(0.8), "0": () => this.fit(),
      ArrowLeft: () => this.pan(step, 0), ArrowRight: () => this.pan(-step, 0), ArrowUp: () => this.pan(0, step), ArrowDown: () => this.pan(0, -step),
      w: () => { this.mode = "width"; this.fit(); }, W: () => { this.mode = "width"; this.fit(); },
      r: () => { this.turn = (this.turn + 90) % 360; this.fit(); }, R: () => { this.turn = (this.turn + 90) % 360; this.fit(); },
      PageDown: () => this.show(this.index + 1), PageUp: () => this.show(this.index - 1),
    };
    if (actions[e.key]) { e.preventDefault(); actions[e.key](); }
  }
}

render().catch((error) => {
  document.getElementById("page").innerHTML = `<div class="notice warn" style="margin:40px 0">${icon("info")}<span>This document could not be loaded: ${esc(error.message)}.
    <a href="">Try again</a></span></div>`;
});
