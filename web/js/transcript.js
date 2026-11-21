// The transcription laid out exactly as the review platform shows it: lifted from edison-pipeline app/static/app.js.
import { esc } from "./core.js";

const PAGE_MARKER = /^\[page\s+(\d+)\]$/i;
const INDENT_MIN_WORDS = 10;
const WORD = /[^\s]*[\p{L}\p{N}][^\s]*/gu;
const DATELINE = /\b(?:1[6-9]|20)\d{2}\.?$/;
const STRUCTURAL_TAG = /^\s*\[/;
const FULLY_QUOTED = /^["'“‘][^]*["'”’]$/;
const ALREADY_INDENTED = /^[ \t]/;
const PARAGRAPH_MIN_WORDS = 25;

function markUnclear(text) {
  return esc(text).replace(/\[unclear:[^\]]*\]/gi, (m) => `<mark>${m}</mark>`);
}

function isWholeParagraph(line) {
  return (line.match(WORD) || []).length >= PARAGRAPH_MIN_WORDS;
}

export function transcriptionBlocks(text) {
  const blocks = [];
  let run = [];
  const flush = () => {
    const body = run.join("\n").replace(/\s+$/, "");
    if (body.trim()) blocks.push({ kind: "para", text: body });
    run = [];
  };
  for (const chunk of (text || "").split(/\n[ \t]*\n+/)) {
    for (const line of chunk.split("\n")) {
      const marker = line.trim().match(PAGE_MARKER);
      if (marker) {
        flush();
        blocks.push({ kind: "page", number: marker[1] });
      } else if (isWholeParagraph(line)) {
        flush();
        run.push(line);
        flush();
      } else {
        run.push(line);
      }
    }
    flush();
  }
  return blocks;
}

export function isBodyParagraph(text) {
  if (text.includes("\n")) return false;
  if (ALREADY_INDENTED.test(text)) return false;
  const line = text.trim();
  if (DATELINE.test(line) || STRUCTURAL_TAG.test(line) || FULLY_QUOTED.test(line)) return false;
  return (line.match(WORD) || []).length >= INDENT_MIN_WORDS;
}

// Page breaks are buttons here, so a reader can turn the scan to the page they are reading.
export function renderTranscription(text) {
  const blocks = transcriptionBlocks(text);
  if (!blocks.length) return "";
  return blocks.map((block) => {
    if (block.kind === "page") {
      return `<div class="tp-page-break" data-page="${esc(block.number)}"><button type="button" data-goto="${esc(block.number)}">Page ${esc(block.number)}</button></div>`;
    }
    return `<p class="tp-para${isBodyParagraph(block.text) ? " tp-indent" : ""}">${markUnclear(block.text)}</p>`;
  }).join("");
}
