// Untrusted text (AI drafts, harvested catalogue values, URL parameters) can never become markup on any page.
import assert from "node:assert/strict";
import test from "node:test";
import { documentEntry, folderEntry, relatedShelf, topicCard } from "../../web/js/cards.js";
import { badge, crumbs, esc, facts, thumbHtml, validId } from "../../web/js/core.js";
import { highlight, parseQuery } from "../../web/js/search.js";
import { renderTranscription } from "../../web/js/transcript.js";

const HOSTILE = `<img src=x onerror=alert(1)>"'><script>alert(2)</script>`;
// A real tag (not escaped text) that is a script, carries an event handler, or links to javascript:, once quoted values are set aside.
function live(html) {
  return (html.match(/<[a-z][^>]*>/gi) || []).some((tag) => {
    const bare = tag.replace(/"[^"]*"/g, '""');
    return /^<(script|iframe|object|embed)\b/i.test(tag) || /\son[a-z]+\s*=/i.test(bare) || /href\s*=\s*"\s*javascript:/i.test(tag)
      || /<img\b[^>]*\ssrc\s*=\s*x\b/i.test(bare);
  });
}

function assertSafe(html) {
  assert.equal(live(html), false, html);
}

test("the markup check itself catches a live payload", () => {
  assert.equal(live(HOSTILE), true);
});

const hostileSummary = {
  id: "E2002AAA", title: HOSTILE, description: HOSTILE, date: HOSTILE, author: HOSTILE, recipient: HOSTILE, type: HOSTILE,
  folder: "E2002", folder_title: HOSTILE, status: "draft", pages: 1, thumb: `x" onerror="alert(1)`, thumb_from: "dataset",
};

test("a hostile title, description and names cannot inject markup into a document entry", () => {
  assertSafe(documentEntry(hostileSummary, "../dataset/"));
});

test("a hostile title and date cannot inject markup into the related documents shelf", () => {
  assertSafe(relatedShelf([hostileSummary], "../dataset/"));
});

test("a hostile collection title and description cannot inject markup into a collection entry", () => {
  assertSafe(folderEntry({ id: "E2002", title: HOSTILE, description: HOSTILE, count: 1, approved: 0 }, ""));
});

test("a hostile topic cannot inject markup into a topic card", () => {
  assertSafe(topicCard({ id: `x"><script>`, title: HOSTILE, description: HOSTILE, count: 1 }, ""));
});

test("a hostile thumbnail address cannot break out of its attribute", () => {
  assertSafe(thumbHtml({ thumb: `x" onerror="alert(1)`, thumb_from: "site" }, ""));
});

test("hostile facts cannot inject markup", () => {
  assertSafe(facts(hostileSummary));
});

test("an unknown status cannot inject markup into a badge", () => {
  assertSafe(badge(`"><script>alert(1)</script>`));
});

test("a hostile breadcrumb cannot inject markup", () => {
  assertSafe(crumbs([[HOSTILE, `javascript:alert(1)`], [HOSTILE, null]]));
});

test("a hostile transcription cannot inject markup", () => {
  assertSafe(renderTranscription(`${HOSTILE}\n\n[page 2]\n\n${HOSTILE}`));
});

test("a hostile unclear-word marker cannot inject markup", () => {
  assertSafe(renderTranscription(`[unclear:${HOSTILE}]`));
});

test("highlighting a hostile text cannot inject markup", () => {
  assertSafe(highlight(HOSTILE, parseQuery("img script alert"), esc));
});

test("a hostile query cannot inject markup through highlighting", () => {
  assertSafe(highlight("A letter", parseQuery(HOSTILE), esc));
});

test("escaping covers all five characters that matter in HTML", () => {
  assert.equal(esc(`&<>"'`), "&amp;&lt;&gt;&quot;&#39;");
});

test("an id with a path or markup in it is refused before any address is built", () => {
  assert.equal(validId("../E2002"), null);
});

test("an id with an angle bracket is refused", () => {
  assert.equal(validId("E2002<x>"), null);
});

test("an ordinary DocID is accepted", () => {
  assert.equal(validId("X018C9-0915"), "X018C9-0915");
});

test("a query of any shape parses without throwing", () => {
  const pieces = ['"', "-", "OR", "NOT", "title:", "year:", "*", "(", ")", "\\", "<", HOSTILE, "edison", "year:1880..", "id:"];
  let seed = 11;
  const rand = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  for (let n = 0; n < 2000; n++) {
    const text = Array.from({ length: 6 }, () => pieces[Math.floor(rand() * pieces.length)]).join(" ");
    assert.doesNotThrow(() => parseQuery(text, ["all", "any", "exact"][n % 3]));
  }
});
