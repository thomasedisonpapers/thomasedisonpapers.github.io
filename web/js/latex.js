// The small LaTeX subset the review platform generates for an opening (notebook_latex.py), read back as HTML for openings whose only reviewed text is LaTeX.
import { esc } from "./core.js";
import { inline as marks } from "./ledger.js";

/** One line of LaTeX as HTML: the generator's commands become tags, its escapes become characters, and anything else stays as text. */
export function latexInline(source) {
  let text = String(source ?? "").replace(/\\\\\s*$/, "");
  text = text.replace(/\\hspace\*\{(\d+)\\fontdimen2\\font\}/g, (_, n) => " ".repeat(Math.min(200, Number(n))));
  text = text.replace(/\\(?:par|smallskip|bigskip|small)\b\s*/g, "").replace(/\\quad\b\s*/g, " ");
  text = text.replace(/\\textbackslash\{\}/g, "\\").replace(/\\textasciitilde\{\}/g, "~").replace(/\\textasciicircum\{\}/g, "^");
  // Escaped specials are taken out before tags are made, so a literal \{ never closes a command early.
  const held = [];
  text = text.replace(/\\([&%$#_{} ])/g, (_, ch) => { held.push(ch); return `\u0000${held.length - 1}\u0000`; });
  let html = marks(text);
  const tags = { textbf: "strong", textit: "em", emph: "em", textsc: "span class=\"lg-sc\"" };
  for (let pass = 0; pass < 4; pass++) {
    html = html.replace(/\\(textbf|textit|emph|textsc)\{([^{}]*)\}/g, (_, cmd, body) => `<${tags[cmd]}>${body}</${tags[cmd].split(" ")[0]}>`);
  }
  return html.replace(/\u0000(\d+)\u0000/g, (_, n) => esc(held[Number(n)]));
}

function cells(row) {
  const out = [""];
  for (let i = 0; i < row.length; i++) {
    if (row[i] === "\\" && i + 1 < row.length) { out[out.length - 1] += row[i] + row[i + 1]; i++; continue; }
    if (row[i] === "&") out.push(""); else out[out.length - 1] += row[i];
  }
  return out.map((c) => c.trim());
}

/** A whole opening's LaTeX as HTML, line by line; unknown lines are shown as written rather than dropped. */
export function renderLatex(source) {
  const out = [];
  let rows = null;
  let headRows = 0;
  for (const raw of String(source ?? "").split(/\r?\n/)) {
    const line = raw.trim();
    const section = line.match(/^\\section\*?\{(.*)\}$/);
    const caption = line.match(/^\\begin\{center\}\\small\s*(.*)\\end\{center\}$/);
    if (rows) {
      if (/^\\end\{tabular\}/.test(line)) {
        const head = rows.slice(0, headRows).map((r) => `<tr>${r.map((c) => `<th scope="col">${latexInline(c)}</th>`).join("")}</tr>`).join("");
        const body = rows.slice(headRows).map((r) => `<tr>${r.map((c) => `<td>${latexInline(c)}</td>`).join("")}</tr>`).join("");
        out.push(`<div class="lg-table-wrap" tabindex="0"><table class="lg-table">${head ? `<thead>${head}</thead>` : ""}<tbody>${body}</tbody></table></div>`);
        rows = null;
      } else if (line === "\\midrule") {
        headRows = rows.length;
      } else if (line && !/^\\(?:toprule|bottomrule)$/.test(line)) {
        rows.push(cells(line.replace(/\\\\\s*$/, "")));
      }
      continue;
    }
    if (section) out.push(`<h3 class="lg-page-head">${latexInline(section[1])}</h3>`);
    else if (caption) out.push(`<p class="lg-caption">${latexInline(caption[1])}</p>`);
    else if (/^\\begin\{tabular\}/.test(line)) { rows = []; headRows = 0; }
    else if (line === "\\begin{framed}") out.push(`<figure class="lg-drawing">`);
    else if (line === "\\end{framed}") out.push(`</figure>`);
    else if (/^\\(?:begin|end)\{(?:flushleft|center)\}$/.test(line) || line === "\\clearpage") continue;
    else if (line) out.push(`<div class="lg-line">${latexInline(line)}</div>`);
  }
  if (rows) out.push(`<div class="lg-line">${rows.map((r) => r.map(esc).join(" & ")).join("<br>")}</div>`);
  // An unclosed drawing frame from a damaged source is closed, so it cannot swallow the rest of the page.
  const opened = out.filter((h) => h === `<figure class="lg-drawing">`).length - out.filter((h) => h === `</figure>`).length;
  return out.join("") + "</figure>".repeat(Math.max(0, opened));
}
