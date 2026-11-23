// One account book read scan by scan: the scan on the left, its ledger pages on the right, with the book's details, downloads and citation.
import { STATUS, badge, crumbs, dataUrl, datasetUrls, esc, fetchJSON, footer, header, icon, param, plural, statusHelp, wireHelp } from "./core.js";
import { Viewer, mobileSwitch, scanPane, wireFocus, wireMobileSwitch, wireSplitter, wireTabs } from "./reader.js";
import { renderOpening } from "./ledger.js";
import { renderLatex } from "./latex.js";

const VOLUME_ID = /^[A-Za-z0-9_-]+$/;
const EDISONDIGITAL = "https://edisondigital.rutgers.edu/";
const SCAN_SOURCE = /^https:\/\/(?:edisondigital\.rutgers\.edu|iiif\.archive\.org)\/[^\s\\]*$/;
const DRAFT = "Under review by admin — transcription may not be accurate";

const pad = (n) => String(n).padStart(4, "0");

/** The scan numbers in a range list such as "1-10,15", as the builder writes which scans the mirror holds. */
export function parseRanges(text) {
  const out = new Set();
  for (const part of String(text || "").split(",")) {
    const match = part.trim().match(/^(\d{1,5})(?:-(\d{1,5}))?$/);
    if (!match) continue;
    const [from, to] = [Number(match[1]), Number(match[2] || match[1])];
    for (let n = from; n <= Math.min(to, from + 20000); n++) out.add(n);
  }
  return out;
}

/** The viewer's pages for a book: a mirrored scan from the data host, else the original on edisondigital, which the site only shows as an image. */
export function scanPages(volume, mirrored = new Set()) {
  return (volume.openings || []).map((o) => {
    const original = SCAN_SOURCE.test(o.source || "") ? o.source : null;
    return mirrored.has(o.n)
      ? { n: o.n, view: `scans/${volume.id}/full/${pad(o.n)}.jpg`, thumb: `scans/${volume.id}/thumb/${pad(o.n)}.jpg`, full: original }
      : { n: o.n, view: original || "", thumb: null, full: null };
  });
}

export function citation(volume, n, href) {
  const parts = [`“${volume.title}”`, `Account book ${volume.id}`, n ? `scan ${n}` : "", "The Edison Papers, Rutgers University"];
  return `${parts.filter(Boolean).join(", ")}. ${href} (accessed ${new Date().toLocaleDateString(undefined, { dateStyle: "long" })}).`;
}

function listed(label, values, limit = 0) {
  const list = [...new Set((values || []).filter(Boolean))];
  if (!list.length) return "";
  const shown = limit && list.length > limit ? list.slice(0, limit) : list;
  const more = shown.length < list.length ? ` <span class="muted">and ${(list.length - shown.length).toLocaleString()} more</span>` : "";
  return `<dt>${esc(label)}</dt><dd>${shown.map(esc).join("; ")}${more}</dd>`;
}

function aboutBook(volume, entry) {
  const external = volume.edisondigital || {};
  const links = [["item", "The book on edisondigital.rutgers.edu"], ["manifest", "Its IIIF manifest"]]
    .filter(([key]) => String(external[key] || "").startsWith(EDISONDIGITAL))
    .map(([key, label]) => `<a href="${esc(external[key])}" rel="noopener" target="_blank">${esc(label)} ${icon("external-link")}</a>`);
  return `${volume.description ? `<p>${esc(volume.description)}</p>` : ""}
    <dl class="about-table"><dt>Account book</dt><dd>${esc(volume.id)}</dd><dt>Title</dt><dd>${esc(volume.title)}</dd>
    ${volume.date ? `<dt>Dates</dt><dd>${esc(volume.date)}</dd>` : ""}
    ${volume.series && volume.series.length ? `<dt>Filed in</dt><dd>${volume.series.map(esc).join(" › ")}</dd>` : ""}
    ${listed("Subjects", volume.subjects)}${listed("Accounts with", volume.parties)}
    ${listed("People named", volume.people, 40)}${listed("Places named", volume.places, 40)}
    <dt>Scans</dt><dd>${plural((volume.openings || []).length, "scan")}; ${(entry?.approved || 0).toLocaleString()} verified</dd>
    <dt>Transcription</dt><dd>${badge(volume.status)}</dd>
    ${links.length ? `<dt>Also at</dt><dd>${links.join("<br>")}</dd>` : ""}</dl>
    <p class="muted" style="margin-top:12px">Scan numbers count the photographs of the book, not the page numbers written in it.</p>
    <p style="margin-top:16px">${statusHelp()}</p>`;
}

async function loadVolume(id) {
  const [entries, urls] = await Promise.all([dataUrl("volumes.json").then(fetchJSON), datasetUrls(`volumes/${id}/volume.json`)]);
  const entry = entries.find((v) => v.id === id);
  if (!entry) return null;
  const volume = await fetchJSON(urls);
  return volume && volume.id === id ? { volume, entry } : null;
}

async function render() {
  header("collections.html");
  footer();
  const page = document.getElementById("page");
  const id = VOLUME_ID.test(param("id") || "") ? param("id") : null;
  const found = id && await loadVolume(id);
  if (!found) {
    page.innerHTML = `${crumbs([["Home", "index.html"], ["Account books", "collections.html#accounts"], ["Not found", null]])}
      <div class="empty-state"><h1>Account book not found</h1><p><a href="collections.html#accounts">See every account book</a></p></div>`;
    return;
  }
  const { volume, entry } = found;
  const openings = volume.openings || [];
  const pages = scanPages(volume, parseRanges(entry.mirrored));
  const wanted = Number(param("scan"));
  const start = Math.max(0, openings.findIndex((o) => o.n === wanted));
  document.title = `${volume.title} · Account book ${volume.id} · The Edison Papers`;
  page.classList.add("doc-page");
  page.innerHTML = `${crumbs([["Home", "index.html"], ["Account books", "collections.html#accounts"], [volume.id, null]])}
  <div class="doc-head">
    <p class="eyebrow">Account book ${esc(volume.id)}</p>
    <h1>${esc(volume.title)}</h1>
    <ul class="facts">${volume.date ? `<li>${icon("calendar")}${esc(volume.date)}</li>` : ""}
      ${volume.subjects && volume.subjects.length ? `<li>${icon("tag")}${esc(volume.subjects.join(" · "))}</li>` : ""}
      <li>${icon("files")}${plural(openings.length, "scan")}</li></ul>
    <div class="status-line" id="scan-status"></div>
  </div>
  ${mobileSwitch()}
  <div class="reader" data-show="scan">
    ${scanPane({ unit: "scan", strip: openings.length > 1 })}
    <section class="text-pane" aria-label="Transcription and details">
      <div class="tabs" role="tablist">
        <button role="tab" aria-selected="true" data-tab="text" title="Transcription of this scan">${icon("file-text")}<span class="lbl">Transcription</span></button>
        <button role="tab" aria-selected="false" data-tab="details" title="About this account book">${icon("info")}<span class="lbl">About the book</span></button>
        <button role="tab" aria-selected="false" data-tab="downloads" title="Download and cite">${icon("download")}<span class="lbl">Download &amp; cite</span></button></div>
      <div class="text-scroll">
        <div data-panel="text"><div class="ledger" id="ledger" aria-live="polite"></div></div>
        <div data-panel="details" hidden>${aboutBook(volume, entry)}</div>
        <div data-panel="downloads" hidden><div class="download-list">
          ${entry.pdf ? `<a class="download" id="dl-pdf" href="#">${icon("file-text")}<div><strong>PDF of the whole book</strong><span>Every scan's transcription, typeset</span></div></a>`
            : `<div class="download">${icon("file-text")}<div><strong>PDF of the whole book</strong><span>Not available yet: it is made once every scan has been drafted.</span></div></div>`}
          <a class="download" id="dl-json" href="#">${icon("braces")}<div><strong>Data (JSON)</strong><span>The whole book: every scan's pages, tables and names</span></div></a>
          <a class="download" id="dl-scan" target="_blank" rel="noopener" hidden>${icon("image")}<div><strong>This scan</strong><span>The full-size image, opened in a new tab</span></div></a>
        </div>
        <h3 style="margin:24px 0 4px">Cite this scan</h3><p class="cite" id="cite"></p>
        <button class="btn" type="button" id="copy-cite">${icon("copy")}Copy citation</button></div>
      </div>
    </section>
  </div>
  <nav class="doc-nav" aria-label="Account books"><span></span>
    <a class="btn link" href="collections.html#accounts">All account books</a><span></span></nav>`;
  wireHelp();
  wireTabs(page);
  if (entry.pdf) page.querySelector("#dl-pdf").href = (await datasetUrls(entry.pdf))[0];
  page.querySelector("#dl-json").href = (await datasetUrls(`volumes/${volume.id}/volume.json`))[0];
  const ledger = page.querySelector("#ledger");
  const scanDownload = page.querySelector("#dl-scan");
  const show = (index) => {
    const current = openings[index];
    scanDownload.hidden = true;
    scanDownload.removeAttribute("href");
    const status = current.status === "approved" ? "approved" : "draft";
    page.querySelector("#scan-status").innerHTML = `${badge(status)}<span>Scan ${esc(current.n)}: ${esc((STATUS[status] || STATUS.none).note)}</span>`;
    ledger.innerHTML = `${status === "draft" ? `<div class="notice warn" role="note">${icon("clock")}<span><strong>${esc(DRAFT)}</strong>
      This scan was drafted and is waiting for an editor to check it against the original.</span></div>` : ""}
      ${current.latex !== undefined && !(current.pages || []).length ? renderLatex(current.latex) : renderOpening(current.pages)}`;
    const address = new URL(`account.html?id=${encodeURIComponent(volume.id)}&scan=${current.n}`, location.href).href;
    history.replaceState(null, "", address);
    page.querySelector("#cite").textContent = citation(volume, current.n, address);
  };
  // The download offers whichever copy of the scan actually loaded, the mirror's or edisondigital's.
  const loaded = (url) => { scanDownload.href = url; scanDownload.hidden = false; };
  new Viewer(page.querySelector(".scan-pane"), pages, { title: volume.title, unit: "scan", start, onShow: show, onLoad: loaded });
  wireSplitter(page.querySelector(".reader"), page.querySelector(".splitter"));
  wireFocus(page.querySelector("[data-act=focus]"));
  wireMobileSwitch(page);
  page.querySelector("#copy-cite").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    try { await navigator.clipboard.writeText(page.querySelector("#cite").textContent); button.lastChild.textContent = "Copied"; }
    catch { button.lastChild.textContent = "Select the text above to copy"; }
  });
}

if (typeof document !== "undefined") {
  render().catch((error) => {
    document.getElementById("page").innerHTML = `<div class="notice warn" style="margin:40px 0">${icon("info")}<span>This account book could not be loaded: ${esc(error.message)}.
      <a href="">Try again</a></span></div>`;
  });
}
