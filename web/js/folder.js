// One collection: its description and every document in it, in DocID order.
import { catalog, crumbs, dataUrl, esc, fetchJSON, footer, header, icon, param, plural, sources, statusHelp, thumbHtml, validId, wireHelp } from "./core.js";
import { documentEntry } from "./cards.js";

async function render() {
  header("collections.html");
  footer();
  const page = document.getElementById("page");
  const id = validId(param("id"));
  const data = await catalog();
  const entry = id && data.folders.find((f) => f.id === id);
  if (!entry) {
    page.innerHTML = `${crumbs([["Home", "index.html"], ["Collections", "collections.html"], ["Not found", null]])}
      <div class="empty-state"><h1>Collection not found</h1><p><a href="collections.html">See all collections</a></p></div>`;
    return;
  }
  document.title = `${entry.title} · The Edison Papers`;
  const jsonUrl = await dataUrl(`folders/${encodeURIComponent(id)}.json`);
  const [folder, { dataset }] = await Promise.all([fetchJSON(jsonUrl), sources()]);
  const counts = folder.summaries.reduce((acc, s) => ({ ...acc, [s.status]: (acc[s.status] || 0) + 1 }), {});
  page.innerHTML = `${crumbs([["Home", "index.html"], ["Collections", "collections.html"], [entry.title, null]])}
    <div class="folder-head">${thumbHtml(entry, dataset[0], { alt: "" })}
      <div class="page-head" style="padding:0"><p class="eyebrow">Collection ${esc(id)}</p><h1>${esc(folder.title)}</h1><p>${esc(folder.description)}</p>
        <ul class="facts"><li>${icon("files")}${plural(folder.summaries.length, "document")}</li>
          <li>${icon("badge-check")}${(counts.approved || 0).toLocaleString()} verified</li>
          <li>${icon("clock")}${(counts.draft || 0).toLocaleString()} under review</li><li>${statusHelp()}</li></ul>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
          <a class="btn" href="search.html?folder=${encodeURIComponent(id)}">${icon("search")}Search this collection</a>
          <a class="btn" href="${esc(jsonUrl)}" download="${esc(id)}.json">${icon("download")}Download data (JSON)</a></div></div></div>
    <div class="results" style="padding-bottom:72px">${folder.summaries.map((s) => documentEntry(s, dataset[0], { collection: false })).join("")}</div>`;
  wireHelp();
}

render().catch((error) => { document.getElementById("page").innerHTML = `<div class="notice warn">This collection could not be loaded: ${esc(error.message)}</div>`; });
