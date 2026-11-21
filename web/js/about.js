// What the site is, what its labels mean, how to search, and what can be downloaded.
import { catalog, crumbs, footer, header, statusHelp, wireHelp } from "./core.js";

header("about.html");
footer();
document.getElementById("page").innerHTML = `${crumbs([["Home", "index.html"], ["About", null]])}
  <div style="max-width:72ch;padding-bottom:72px">
  <div class="page-head"><h1>About</h1><p>The Edison Papers shows documents from the Thomas A. Edison Papers at Rutgers beside their
    transcriptions, so anyone can read them without deciphering the handwriting.</p></div>
  <h2 id="labels" style="margin:16px 0 10px">What the labels mean</h2><p>${statusHelp()}</p>
  <p>Transcriptions marked <strong>Under review</strong> were drafted and are waiting for an editor to check them, so they may contain mistakes.
    Once checked they are marked <strong>Verified</strong>, and the label changes on its own.</p>
  <h2 id="search" style="margin:28px 0 10px">How to search</h2>
  <dl class="about-table">
    <dt>Words</dt><dd><code>phonograph records</code> finds documents with both words.</dd>
    <dt>Exact phrase</dt><dd><code>"storage battery"</code> finds the words side by side.</dd>
    <dt>Either word</dt><dd><code>edison OR insull</code>, or choose <em>Any word</em>.</dd>
    <dt>Leave out</dt><dd><code>-cement</code> removes documents that mention cement.</dd>
    <dt>Word beginnings</dt><dd><code>batter*</code> finds battery, batteries and more.</dd>
    <dt>One field</dt><dd><code>title:</code> <code>author:</code> <code>to:</code> <code>people:</code> <code>place:</code> <code>type:</code>
      <code>subject:</code> <code>text:</code> <code>id:</code> <code>collection:</code></dd>
    <dt>Dates</dt><dd><code>year:1880</code> or <code>year:1878..1885</code></dd>
    <dt>Common words</dt><dd>Words such as “the” and “of” are too common to search for on their own, so they are ignored.</dd>
  </dl>
  <h2 id="downloads" style="margin:28px 0 10px">Downloads and rights</h2>
  <p>Every document page offers its PDF, its record as JSON and its page images. Transcriptions and metadata are released under CC0 1.0;
    scans are courtesy of Thomas Edison National Historical Park.</p>
  <p class="muted">Logo: “Thomas Alva Edison” by Phill, from the Noun Project. Icons: Lucide (ISC licence). Typefaces: Inter and Source Serif 4
    (SIL Open Font Licence).</p>
  <p id="built" class="muted"></p></div>`;
wireHelp();
catalog().then((data) => {
  document.getElementById("built").textContent = `This catalogue was built on ${new Date(data.built_at).toLocaleString()}.`;
}).catch(() => {});
