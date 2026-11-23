// The landing page: one clear search, then ways in by topic, decade, recently verified work and collection.
import { catalog, esc, footer, header, icon, logoUrl, plural, sources, summaries, thumbHtml, statusHelp, wireHelp } from "./core.js";
import { folderEntry, topicCard } from "./cards.js";
import { enhanceSelect } from "./controls.js";

const POPULAR = [["Phonograph", "phonograph"], ["Letters to Mina", "to:mina"], ["Storage battery", '"storage battery"'],
  ["1910–1915", "year:1910..1915"], ["Portland cement", '"portland cement"']];
const SCOPES = [["", "Everything"], ["title", "Titles"], ["people", "People"], ["text", "Transcriptions"], ["place", "Places"]];

async function render() {
  header("index.html", { search: false });
  footer();
  const data = await catalog();
  const { dataset } = await sources();
  const base = dataset[0];
  const recent = data.recent.length ? await summaries(data.recent) : [];
  const maxDecade = Math.max(1, ...Object.values(data.decades));
  const keys = Object.keys(data.decades).map(Number);
  const decades = [];
  for (let d = Math.min(1860, ...keys); d <= Math.max(1930, ...keys); d += 10) decades.push(d);

  document.querySelector("main").innerHTML = `
  <section class="hero"><div class="wrap">
    <img class="hero-logo" src="${esc(logoUrl())}" alt="">
    <p class="eyebrow">Thomas A. Edison Papers · Rutgers University</p>
    <h1>Explore Edison's papers, page by page.</h1>
    <p class="lede">Search letters, notes and business records, and read each transcription beside the original document.</p>
    <form class="searchbar" action="search.html" role="search" id="home-search">
      <label class="sr-only" for="scope">Search in</label>
      <select id="scope">${SCOPES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <label class="input">${icon("search")}<span class="sr-only">Search the archive</span>
        <input id="home-q" type="search" placeholder="Search names, places, inventions or a phrase" autocomplete="off"></label>
      <button type="submit">Search</button>
    </form>
    <input type="hidden" name="q">
    <div class="popular"><span>Popular:</span>${POPULAR.map(([label, q]) => `<a class="chip" href="search.html?q=${encodeURIComponent(q)}">${esc(label)}</a>`).join("")}</div>
    <div class="stats">
      <div><strong>${data.documents.toLocaleString()}</strong><span>documents</span></div>
      <div><strong>${data.approved.toLocaleString()}</strong><span>verified by editors</span></div>
      <div><strong>${data.folders.length.toLocaleString()}</strong><span>collections</span></div>
      <div><strong>${keys.length ? `${Math.min(...keys)}s–${Math.max(...keys)}s` : "—"}</strong><span>date range</span></div>
    </div>
  </div><a class="hero-next" href="#begin">Ways to begin${icon("chevron-down")}</a></section>

  <section class="block alt" id="begin"><div class="wrap">
    <div class="block-head"><div><h2>Three ways to begin</h2><p>Free to use, with nothing to sign up for.</p></div></div>
    <div class="ways">
      <div class="way">${icon("search")}<h3>Search everything</h3><p>Names, places, inventions or a phrase in quotes. Narrow the results by date,
        collection or document type.</p></div>
      <div class="way">${icon("layers")}<h3>Browse a topic</h3><p>Start from a subject such as the phonograph or the family letters, and see
        every document about it.</p></div>
      <div class="way">${icon("book-open")}<h3>Read side by side</h3><p>Open a document to see the original page beside its transcription, then
        download the PDF, the images or the data.</p></div>
    </div>
  </div></section>

  <section class="block"><div class="wrap">
    <div class="block-head"><div><h2>Browse by topic</h2><p>Each topic gathers the documents the editors filed under a subject, across every collection.</p></div>
      <a class="see-all" href="topics.html">All ${data.topic_count || data.topics.length} topics ${icon("chevron-right")}</a></div>
    <div class="topics">${data.topics.slice(0, 8).map((t) => topicCard(t, base)).join("")}</div>
  </div></section>

  <section class="block alt"><div class="wrap">
    <div class="block-head"><div><h2>Browse by decade</h2><p>The surviving documents from each decade. Choose one to read them in date order.</p></div></div>
    <div class="timeline">${decades.map((d) => {
      const n = data.decades[d] || 0;
      return `<a class="decade${n ? "" : " empty"}" href="search.html?q=${encodeURIComponent(`year:${d}..${d + 9}`)}&amp;sort=oldest"
        aria-label="The ${d}s, ${plural(n, "document")}"><span class="track"><span class="bar" data-h="${Math.round((n / maxDecade) * 100)}"></span></span>
        <span class="lbl">${d}s</span><span class="n">${n.toLocaleString()}</span></a>`;
    }).join("")}</div>
  </div></section>

  ${recent.length ? `<section class="block"><div class="wrap">
    <div class="block-head"><div><h2>Recently verified</h2><p>The latest documents an editor has checked against the original. ${statusHelp()}</p></div>
      <a class="see-all" href="search.html?status=approved">All verified ${icon("chevron-right")}</a></div>
    <div class="shelf">${recent.map((s) => `<a class="shelf-item" href="document.html?id=${encodeURIComponent(s.id)}">
      ${thumbHtml(s, base, { alt: "" })}<strong>${esc(s.title)}</strong><span>${esc(s.date || "Undated")}</span></a>`).join("")}</div>
  </div></section>` : ""}

  <section class="block alt"><div class="wrap">
    <div class="block-head"><div><h2>Collections</h2><p>The archive's folders, each kept together as it was filed.</p></div>
      <a class="see-all" href="collections.html">All collections ${icon("chevron-right")}</a></div>
    <div class="results">${data.folders.slice(0, 4).map((f) => folderEntry(f, base)).join("")}</div>
  </div></section>`;

  // Bar heights go through the CSSOM, which the security policy allows, rather than inline style attributes.
  document.querySelectorAll(".bar[data-h]").forEach((bar) => { bar.style.height = `${bar.dataset.h}%`; });
  enhanceSelect(document.getElementById("scope"), { label: "Search in" });
  document.getElementById("home-search").addEventListener("submit", (event) => {
    event.preventDefault();
    const words = document.getElementById("home-q").value.trim();
    const scope = document.getElementById("scope").value;
    const q = scope && words ? words.split(/\s+/).map((w) => `${scope}:${w}`).join(" ") : words;
    location.href = `search.html${q ? `?q=${encodeURIComponent(q)}` : ""}`;
  });
  wireHelp();
}

render().catch((error) => {
  document.querySelector("main").innerHTML = `<div class="wrap empty-state"><h2>The archive could not be loaded</h2>
    <p>${esc(error.message)}. Please try again in a moment.</p></div>`;
});
