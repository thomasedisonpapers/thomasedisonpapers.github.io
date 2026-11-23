// The site's search: tokenizing like the builder, reading queries, combining postings and counting filters.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { Index, applyFilters, describeQuery, fnv1a, highlight, parseQuery, runQuery, setStopwords, setVariants, shardOf, sortResults, stem, suggest, tokenize, yearBounds } from "../../web/js/search.js";

const fixture = JSON.parse(readFileSync(new URL("../fixtures/tokenize.json", import.meta.url), "utf-8"));
const stems = JSON.parse(readFileSync(new URL("../fixtures/stems.json", import.meta.url), "utf-8"));
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

test("the date filter can initialize for a catalogue with hundreds of thousands of entries", () => {
  assert.deepEqual(yearBounds([0, 1870, ...Array(200_000).fill(1900), 1925]), [1870, 1925]);
});

for (const { text, terms } of fixture) {
  test(`the site tokenizes ${JSON.stringify(text)} exactly as the builder does`, () => {
    assert.deepEqual(tokenize(text), terms);
  });
}

setStopwords(["the", "and", "of"]);
setVariants({ lab: ["laboratory"], laboratory: ["lab"], co: ["company"], company: ["co"] });

test("the site stems every word of our transcriptions exactly as the builder does", () => {
  const differ = Object.entries(stems).filter(([word, root]) => stem(word) !== root);
  assert.deepEqual(differ, []);
});

test("laboratory and laboratories share a stem", () => {
  assert.equal(stem("laboratories"), stem("laboratory"));
});

test("a word with a listed variant also searches the variant", () => {
  assert.deepEqual(parseQuery("lab").clauses[0].alts.map((a) => a.words.join(" ")), ["lab", "laboratory"]);
});

test("a word and its variant stay one clause, so the other words are still all required", () => {
  assert.equal(parseQuery("west orange lab").clauses.length, 3);
});

test("the site hashes a word pair exactly as the builder does", () => {
  assert.equal(fnv1a("storage battery"), 3456667115);
});

test("a word pair lives in the hashed file the builder chose", () => {
  assert.equal(shardOf("storage battery"), "p1eb");
});

test("a word lives in the file of its first three letters", () => {
  assert.equal(shardOf("edison"), "edi");
});

test("a short word is padded to its three-letter file", () => {
  assert.equal(shardOf("x"), "x__");
});

test("a common word on its own is left out and explained", () => {
  assert.equal(parseQuery("the").notes.length, 1);
});

test("plain words become one clause each, all required", () => {
  assert.equal(parseQuery("edison cement").clauses.length, 2);
});

test("a quoted phrase becomes a phrase atom", () => {
  assert.equal(parseQuery('"portland cement"').clauses[0].alts[0].kind, "phrase");
});

test("a field tag limits a word to that field", () => {
  assert.equal(parseQuery("author:edison").clauses[0].alts[0].field, "author");
});

test("from: is read as the author field", () => {
  assert.equal(parseQuery("from:edison").clauses[0].alts[0].field, "author");
});

test("OR joins two words into one clause", () => {
  assert.equal(parseQuery("edison OR insull").clauses[0].alts.length, 2);
});

test("a leading minus excludes a word", () => {
  assert.equal(parseQuery("-cement").clauses[0].negate, true);
});

test("a trailing star searches word beginnings", () => {
  assert.equal(parseQuery("batter*").clauses[0].alts[0].kind, "prefix");
});

test("a year range sets both ends", () => {
  const q = parseQuery("year:1885..1878");
  assert.deepEqual([q.yearFrom, q.yearTo], [1878, 1885]);
});

test("an unknown field is searched as words and explained", () => {
  assert.equal(parseQuery("colour:red").notes.length, 1);
});

test("exact mode reads the whole box as one phrase", () => {
  assert.equal(parseQuery("storage battery company", "exact").clauses[0].alts[0].words.length, 3);
});

test("any-word mode turns each word into a word beginning", () => {
  assert.equal(parseQuery("battery", "any").clauses[0].alts[0].kind, "prefix");
});

test("any-word mode joins every word into one clause", () => {
  assert.equal(parseQuery("battery cement", "any").clauses.length, 1);
});

test("an unbalanced quote never throws", () => {
  assert.doesNotThrow(() => parseQuery('"half a phrase'));
});

test("the search details describe a field search in plain words", () => {
  assert.equal(describeQuery(parseQuery("author:edison")), 'Documents with "edison" in author.');
});

// A tiny index in the builder's format: stems, gap-encoded postings, words by three letters, pairs by hash.
// Doc 0 has "Edison" in its title; doc 1 has "edison" in its text and "storage battery" in its title; doc 2 has "batteries" in its text.
const SHARDS = {
  edi: { edison: [0, 1, 1, 512] },
  sto: { storag: [1, 1] },
  [shardOf("storag batteri")]: { "storag batteri": [1, 1] },
  bat: { batteri: [1, 1, 1, 512] },
  lab: { laboratori: [2, 512], lab: [0, 1] },
  [shardOf("edison laboratori")]: { "edison laboratori": [1, 512] },
};
const META = { documents: 3, weights: { title: 8, text: 1 } };
const index = () => new Index("", Object.keys(SHARDS), async (url) => SHARDS[url.split(".json")[0]] || {});

test("a title match outranks a transcription match", async () => {
  const result = await runQuery(parseQuery("edison"), index(), META);
  assert.deepEqual(sortResults(result.ordinals, "", result.scores, { year: [], idrank: [] }), [0, 1]);
});

test("a field search ignores matches in other fields", async () => {
  const result = await runQuery(parseQuery("text:edison"), index(), META);
  assert.deepEqual(result.ordinals, [1]);
});

test("a phrase needs its word pair", async () => {
  const result = await runQuery(parseQuery('"storage battery"'), index(), META);
  assert.deepEqual(result.ordinals, [1]);
});

test("a word beginning finds every word that starts with it", async () => {
  const result = await runQuery(parseQuery("batter*"), index(), META);
  assert.deepEqual(result.ordinals.sort(), [1, 2]);
});

test("an excluded word removes its documents", async () => {
  const result = await runQuery(parseQuery("batter* -edison"), index(), META);
  assert.deepEqual(result.ordinals, [2]);
});

test("an empty query lists every document", async () => {
  const result = await runQuery(parseQuery(""), index(), META);
  assert.equal(result.ordinals.length, 3);
});

const FACETS = { year: [1880, 1910, 0], type: [0, 1, -1], folder: [0, 0, 1], status: [0, 1, 1], idrank: [2, 0, 1] };
const NO_FILTERS = { yearFrom: null, yearTo: null, types: new Set(), folders: new Set(), statuses: new Set() };

test("the first status in the list is counted (regression: verified documents were missing from the counts, 20 November)", () => {
  assert.equal(applyFilters([0, 1, 2], FACETS, NO_FILTERS).counts.status.get(0), 1);
});

test("an undated document is not counted in any year", () => {
  assert.equal(applyFilters([0, 1, 2], FACETS, NO_FILTERS).counts.year.has(0), false);
});

test("a year range keeps only documents dated inside it", () => {
  assert.deepEqual(applyFilters([0, 1, 2], FACETS, { ...NO_FILTERS, yearFrom: 1900, yearTo: 1920 }).kept, [1]);
});

test("a facet's counts ignore its own selection", () => {
  const { counts } = applyFilters([0, 1, 2], FACETS, { ...NO_FILTERS, statuses: new Set([0]) });
  assert.equal(counts.status.get(1), 2);
});

test("sorting by DocID follows the id ranks", () => {
  assert.deepEqual(sortResults([0, 1, 2], "id", new Map(), FACETS), [1, 2, 0]);
});

test("highlighting escapes the text around a match", () => {
  assert.equal(highlight("<b>Edison</b>", parseQuery("edison"), esc), "&lt;b&gt;<mark>Edison</mark>&lt;/b&gt;");
});

test("highlighting never marks inside an escaped character (regression: AT&T read as AT&<mark>amp</mark>;T, 20 November)", () => {
  assert.equal(highlight("AT&T", parseQuery("amp"), esc), "AT&amp;T");
});

test("a search for lab finds documents that say laboratory", async () => {
  const result = await runQuery(parseQuery("lab"), index(), META);
  assert.deepEqual(result.ordinals.sort(), [0, 2]);
});

test("words typed side by side rank first where the document has them side by side", async () => {
  // Both documents mention west and orange in their text; only document 1 says "West Orange".
  const shards = { wes: { west: [0, 512, 1, 512] }, ora: { orang: [0, 512, 1, 512] }, [shardOf("west orang")]: { "west orang": [1, 512] } };
  const together = new Index("", Object.keys(shards), async (url) => shards[url.split(".json")[0]] || {});
  const result = await runQuery(parseQuery("west orange"), together, { documents: 2, weights: { text: 1 } });
  assert.equal(sortResults(result.ordinals, "", result.scores, { year: [], idrank: [] })[0], 1);
});

test("highlighting marks every form of a word", () => {
  assert.equal(highlight("The Laboratories", parseQuery("laboratory"), esc), "The <mark>Laboratories</mark>");
});

test("highlighting marks a variant as well as the word typed", () => {
  assert.equal(highlight("Edison lab", parseQuery("laboratory"), esc), "Edison <mark>lab</mark>");
});

test("a misspelt word gets the closest indexed word as a suggestion", async () => {
  assert.deepEqual(await suggest(parseQuery("batteriez"), index()), [{ typed: "batteriez", suggestion: "batteri" }]);
});

test("a document matching more words of an OR group ranks higher", async () => {
  const result = await runQuery(parseQuery("edison OR storage", "all"), index(), META);
  assert.equal(sortResults(result.ordinals, "", result.scores, { year: [], idrank: [] })[0], 1);
});

test("a file the index does not list costs no request", async () => {
  let asked = 0;
  const counting = new Index("", ["edi"], async () => { asked++; return {}; });
  await counting.atom({ kind: "term", words: ["zebra"], mask: 0 });
  assert.equal(asked, 0);
});

test("a failed index file reaches the page instead of reading as no results", async () => {
  const failing = new Index("", ["edi"], async () => { throw new Error("offline"); });
  await assert.rejects(failing.atom({ kind: "term", words: ["edison"], mask: 0 }));
});

test("NOT before a year range does not leak onto the next word (regression: NOT year:1915 cement, 20 November)", () => {
  assert.equal(parseQuery("NOT year:1915 cement").clauses[0].negate, false);
});

test("a field name followed by a space still names the field", () => {
  assert.equal(parseQuery("title: cement").clauses[0].alts[0].field, "title");
});

test("a minus before OR does not make it a joining word", () => {
  assert.equal(parseQuery("edison -OR insull").clauses.length, 3);
});
