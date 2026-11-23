// A document read side by side: the scan with zoom, pan and turning on the left, its transcription and details on the right.
import { STATUS, badge, catalog, crumbs, dataPath, dataUrl, datasetUrls, esc, facts, fetchJSON, footer, header, icon, indexMeta, param, plural,
  sources, statusHelp, summaries, validId, wireHelp } from "./core.js";
import { Index, fold } from "./search.js";
import { renderTranscription } from "./transcript.js";
import { accountHref, relatedShelf } from "./cards.js";
import { Viewer, mobileSwitch, scanPane, wireFocus, wireMobileSwitch, wireSplitter, wireTabs } from "./reader.js";

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
  if (summary.kind === "account") return { redirect: accountHref(summary.volume, summary.scan) };
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
  if (found?.redirect) {
    location.replace(found.redirect);
    return;
  }
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
  ${mobileSwitch()}
  <div class="reader" data-show="scan">
    ${scanPane({ unit: "page", strip: pages.length > 1 })}
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
  wireTabs(page);
  // The transcription marks where the page on screen begins, so the reader can find their place in the text.
  const onShow = (index) => document.querySelectorAll(".tp-page-break").forEach((b) => b.classList.toggle("current", Number(b.dataset.page) === index + 1));
  const viewer = new Viewer(page.querySelector(".scan-pane"), pages, { title: record.title, onShow });
  wireSplitter(page.querySelector(".reader"), page.querySelector(".splitter"));
  wireFocus(page.querySelector("[data-act=focus]"));
  page.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => viewer.show(Number(b.dataset.goto) - 1)));
  wireMobileSwitch(page);
  watchForNewer(record);
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

render().catch((error) => {
  document.getElementById("page").innerHTML = `<div class="notice warn" style="margin:40px 0">${icon("info")}<span>This document could not be loaded: ${esc(error.message)}.
    <a href="">Try again</a></span></div>`;
});
