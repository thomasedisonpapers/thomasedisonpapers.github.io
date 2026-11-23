// An account-book opening as the editors' structured pages: its lines, its ledger tables column by column, and its drawings.
import { esc } from "./core.js";

// The marks the drafting prompt writes for what a ledger shows besides words; anything else in brackets stays as written.
const MARK = /\[(unclear|struck out|insertion|margin|tick|posting cross|cancelled)(?::\s*([^\]]*))?\]/gi;
// Money and quantities read down a column, so these columns and figures alone in a cell are set flush right.
const FIGURE_COLUMN = /^(amount|debit|credit|dr\.?|cr\.?|rate|folio|fol\.?|total|balance|time|quantity|qty|price|\$|£)$/i;
const FIGURE = /^[\s$£]*(?:[\d][\d.,/ -]*|"|”|do\.?|-+|—)[\s]*$/i;
const POSITION = { left: "Left page", right: "Right page", top: "Top page", bottom: "Bottom page" };

/** One line or cell with its marks made visible, and every piece of the original text escaped. */
export function inline(text) {
  let out = "";
  let last = 0;
  for (const match of String(text ?? "").matchAll(MARK)) {
    out += esc(text.slice(last, match.index));
    last = match.index + match[0].length;
    const kind = match[1].toLowerCase();
    const reading = match[2] === undefined ? null : match[2].trim();
    if (kind === "unclear") {
      out += reading ? `<span class="lg-unclear" title="Uncertain reading">${esc(reading)}</span>`
        : `<span class="lg-unclear" title="Unclear in the original">[unclear]</span>`;
    } else if (kind === "struck out") {
      out += `<s class="lg-struck" title="Struck out in the original">${esc(reading ?? "")}</s>`;
    } else if (kind === "insertion") {
      out += `<ins class="lg-insert" title="Written in above the line">${esc(reading ?? "")}</ins>`;
    } else if (kind === "margin") {
      out += `<span class="lg-margin" title="Written in the margin">${esc(reading ?? "")}</span>`;
    } else if (kind === "tick") {
      out += `<span class="lg-mark" title="Tick mark" role="img" aria-label="tick">✓</span>`;
    } else if (kind === "posting cross") {
      out += `<span class="lg-mark" title="Posting cross: the entry was carried to the ledger" role="img" aria-label="posting cross">×</span>`;
    } else {
      out += `<span class="lg-cancel" title="The entry is cancelled in the original">Cancelled</span>`;
    }
  }
  return out + esc(String(text ?? "").slice(last));
}

function table(t) {
  const columns = (t.columns || []).map(String);
  const width = Math.max(columns.length, ...(t.rows || []).map((row) => row.length), 0);
  if (!width) return "";
  const figures = Array.from({ length: width }, (_, i) => FIGURE_COLUMN.test((columns[i] || "").trim()));
  const cell = (value, i, tag) => {
    const text = String(value ?? "");
    const right = figures[i] || (tag === "td" && text.trim() && FIGURE.test(text));
    return `<${tag}${right ? ' class="num"' : ""}${tag === "th" ? ' scope="col"' : ""}>${inline(text)}</${tag}>`;
  };
  const head = columns.some((c) => c.trim())
    ? `<thead><tr>${Array.from({ length: width }, (_, i) => cell(columns[i] || "", i, "th")).join("")}</tr></thead>` : "";
  const body = (t.rows || []).map((row) => `<tr>${Array.from({ length: width }, (_, i) => cell(row[i] ?? "", i, "td")).join("")}</tr>`).join("");
  // A wide ledger scrolls inside its own box, so the page itself never scrolls sideways on a phone.
  return `<div class="lg-table-wrap" tabindex="0"><table class="lg-table">${t.caption ? `<caption>${inline(t.caption)}</caption>` : ""}${head}<tbody>${body}</tbody></table></div>`;
}

function drawing(d) {
  const labels = (d.labels || []).filter((l) => String(l).trim());
  return `<figure class="lg-drawing"><figcaption>Drawing</figcaption>${d.summary ? `<p>${inline(d.summary)}</p>` : ""}
    ${labels.length ? `<p class="lg-labels">Labelled: ${labels.map(inline).join("; ")}</p>` : ""}</figure>`;
}

/** An opening's pages, in order; a page's heading names its side only when the scan shows more than one. */
export function renderOpening(pages) {
  if (!pages || !pages.length) return `<p class="muted">Nothing was transcribed from this scan.</p>`;
  return pages.map((page) => {
    const head = pages.length > 1 && POSITION[page.position] ? `<h3 class="lg-page-head">${esc(POSITION[page.position])}</h3>` : "";
    if (page.content === "blank") return `<section class="lg-page">${head}<p class="muted">Blank page.</p></section>`;
    const lines = (page.lines || []).map((line) => `<div class="lg-line">${inline(line) || "&nbsp;"}</div>`).join("");
    const names = [["People", page.people], ["Places", page.places]].filter(([, list]) => list && list.length)
      .map(([label, list]) => `<p class="lg-names"><strong>${label}:</strong> ${list.map(esc).join("; ")}</p>`).join("");
    return `<section class="lg-page">${head}${lines ? `<div class="lg-lines">${lines}</div>` : ""}
      ${(page.tables || []).map(table).join("")}${(page.drawings || []).map(drawing).join("")}${names}</section>`;
  }).join("");
}
