// Account books: the ledger renderer, the legacy LaTeX reader, the scan sources and the links that open the account reader.
import assert from "node:assert/strict";
import test from "node:test";
import { inline, renderOpening } from "../../web/js/ledger.js";
import { latexInline, renderLatex } from "../../web/js/latex.js";
import { accountHref, entryHref } from "../../web/js/cards.js";
import { citation, parseRanges, scanPages } from "../../web/js/account.js";

const LEDGER = [{ position: "single", content: "table", lines: ["Gold & Stock Page 2"], people: ["Gault, Jacob"], places: [], drawings: [],
  tables: [{ caption: "In account with James Hammit", columns: ["Month", "Day", "Particulars", "Folio", "Amount"],
    rows: [["1869", "Janry", "By Cash", "", "100.00"], ["", "", "Interest to date [tick]", "", "25.00"]] }] }];

test("hostile text in a ledger line is shown as text, never as markup", () => {
  assert.doesNotMatch(renderOpening([{ ...LEDGER[0], lines: ["<img src=x onerror=alert(1)>"] }]), /<img/);
});

test("hostile text in a table cell is shown as text, never as markup", () => {
  assert.doesNotMatch(renderOpening([{ ...LEDGER[0], tables: [{ caption: "", columns: [], rows: [["<script>alert(1)</script>"]] }] }]), /<script/);
});

test("hostile text inside a drafting mark is escaped too", () => {
  assert.doesNotMatch(inline("[struck out: <b>x</b>]"), /<b>/);
});

test("an amount column is set flush right so figures line up", () => {
  assert.match(renderOpening(LEDGER), /<td class="num">100\.00<\/td>/);
});

test("a ledger table keeps its account heading as its caption", () => {
  assert.match(renderOpening(LEDGER), /<caption>In account with James Hammit<\/caption>/);
});

test("a tick in the ledger is shown as a tick with its name for screen readers", () => {
  assert.match(inline("Interest [tick]"), /aria-label="tick">✓/);
});

test("an uncertain reading is shown with its reading and marked as uncertain", () => {
  assert.match(inline("Paid [unclear: Hammit]"), /<span class="lg-unclear" title="Uncertain reading">Hammit<\/span>/);
});

test("struck-out words are shown struck out", () => {
  assert.match(inline("[struck out: 25 00]"), /<s class="lg-struck"[^>]*>25 00<\/s>/);
});

test("a bracketed note that is not a drafting mark stays exactly as written", () => {
  assert.equal(inline("[see folio 12]"), "[see folio 12]");
});

test("the people named on a page are listed under it", () => {
  assert.match(renderOpening(LEDGER), /People:<\/strong> Gault, Jacob/);
});

test("a blank page says so rather than showing nothing", () => {
  assert.match(renderOpening([{ position: "left", content: "blank" }, { position: "right", content: "blank" }]), /Blank page\./);
});

test("a page's side is named only when the scan shows two pages", () => {
  assert.doesNotMatch(renderOpening(LEDGER), /lg-page-head/);
});

test("the platform's one-line table caption is read as a caption, not shown as LaTeX", () => {
  assert.equal(renderLatex("\\begin{center}\\small In account with James Hammit\\end{center}"), '<p class="lg-caption">In account with James Hammit</p>');
});

test("preserved spacing in LaTeX becomes spaces rather than a command", () => {
  assert.equal(latexInline("a\\hspace*{3\\fontdimen2\\font}b"), "a   b");
});

test("an escaped dollar sign in LaTeX is shown as a dollar sign", () => {
  assert.equal(latexInline("\\$5 \\& sundries"), "$5 &amp; sundries");
});

test("hostile text in legacy LaTeX is shown as text, never as markup", () => {
  assert.doesNotMatch(renderLatex("\\textbf{<img src=x onerror=alert(1)>}"), /<img/);
});

test("a LaTeX ledger table keeps the row above its rule as the heading", () => {
  const html = renderLatex("\\begin{tabular}{ll}\n\\toprule\n\\textbf{Day} & \\textbf{Amount} \\\\\n\\midrule\n2 & 5 00 \\\\\n\\bottomrule\n\\end{tabular}");
  assert.match(html, /<thead><tr><th scope="col"><strong>Day<\/strong><\/th>/);
});

test("an escaped ampersand inside a LaTeX cell does not split the cell", () => {
  assert.match(renderLatex("\\begin{tabular}{l}\nGold \\& Stock \\\\\n\\end{tabular}"), /<td>Gold &amp; Stock<\/td>/);
});

test("a mirrored scan is tried from the mirror first and edisondigital second", () => {
  const [page] = scanPages({ id: "A234A", openings: [{ n: 4, source: "https://edisondigital.rutgers.edu/files/original/A234A/au4.jpg" }] }, new Set([4]));
  assert.deepEqual([page.view, page.full], ["scans/A234A/full/0004.jpg", "https://edisondigital.rutgers.edu/files/original/A234A/au4.jpg"]);
});

test("a scan source on any other host is never loaded", () => {
  const [page] = scanPages({ id: "A234A", openings: [{ n: 1, source: "https://example.com/au1.jpg" }] });
  assert.equal(page.full, null);
});

test("a citation names the book, its ID and the scan", () => {
  assert.match(citation({ id: "A234A", title: "Account, Edison and Murray, 1873" }, 4, "https://x/account.html?id=A234A&scan=4"),
    /^“Account, Edison and Murray, 1873”, Account book A234A, scan 4, The Edison Papers/);
});

test("a search result for an account-book scan opens the account reader at that scan", () => {
  assert.equal(entryHref({ id: "A234A-0003", kind: "account", volume: "A234A", scan: 4 }), "account.html?id=A234A&scan=4");
});

test("a search result for a document still opens the document reader", () => {
  assert.equal(entryHref({ id: "E2002AAA" }), "document.html?id=E2002AAA");
});

test("an unsafe book ID never becomes a link into the account reader", () => {
  assert.equal(accountHref("../x", 1), "collections.html#accounts");
});

test("a scan the mirror does not hold yet is shown from edisondigital without asking the mirror", () => {
  const [page] = scanPages({ id: "A234A", openings: [{ n: 4, source: "https://edisondigital.rutgers.edu/files/original/A234A/au4.jpg" }] });
  assert.deepEqual([page.view, page.thumb], ["https://edisondigital.rutgers.edu/files/original/A234A/au4.jpg", null]);
});

test("an unmapped scan from the supported Internet Archive endpoint can be shown", () => {
  const source = "https://iiif.archive.org/iiif/book/page/full/full/0/default.jpg";
  const [page] = scanPages({ id: "A234A", openings: [{ n: 1, source }] });
  assert.equal(page.view, source);
});

test("the builder's scan ranges are read back as the scans they name", () => {
  assert.deepEqual([...parseRanges("1-3,7")], [1, 2, 3, 7]);
});

test("a notebook scan is shown from archive.org's book viewer at the canvas the scan counts from", () => {
  const iiif = "https://iiif.archive.org/image/iiif/3/taepnotebook-N149%2Fx.jp2";
  assert.equal(scanPages({ id: "N149", openings: [{ n: 30, iiif }] })[0].view, "https://archive.org/download/taepnotebook-N149/page/n29_w1600.jpg");
});

test("archive.org's image server is the fallback when its book viewer image fails", () => {
  const iiif = "https://iiif.archive.org/image/iiif/3/taepnotebook-N149%2Fx.jp2";
  assert.equal(scanPages({ id: "N149", openings: [{ n: 1, iiif }] })[0].full, `${iiif}/full/1600,/0/default.jpg`);
});

test("a notebook image address on any other host is never loaded", () => {
  assert.equal(scanPages({ id: "N149", openings: [{ n: 1, iiif: "https://example.com/x" }] })[0].view, "");
});

test("a search result for a notebook scan opens the notebook reader at that scan", () => {
  assert.equal(entryHref({ id: "N149-0003", kind: "notebook", volume: "N149", scan: 4 }), "notebook.html?id=N149&scan=4");
});

test("a citation of a notebook scan names it as a notebook", () => {
  assert.match(citation({ id: "N149", collection: "notebooks", title: "N-80-10-15.2 (1880)" }, 4, "https://x"), /Notebook N149, scan 4/);
});
