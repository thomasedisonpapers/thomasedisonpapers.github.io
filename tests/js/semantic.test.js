// Search by meaning: which queries use it, how passages are scored, how close is close enough, and how the two rankings merge.
import assert from "node:assert/strict";
import test from "node:test";
import { parseQuery } from "../../web/js/search.js";
import { eligible, fuse, keywordWeight, meaningCutoff, meaningScope, meaningText, scorePassages, topMatches } from "../../web/js/semantic.js";

const ask = (text, mode = "all") => eligible(parseQuery(text, mode), text);

test("plain words of a question are matched by meaning", () => {
  assert.equal(ask("letters about hearing loss"), true);
});

test("plain words in any-word mode are matched by meaning too", () => {
  assert.equal(ask("letters about hearing loss", "any"), true);
});

test("a single word is left to keyword search", () => {
  assert.equal(ask("phonograph"), false);
});

test("a quoted phrase is left to keyword search", () => {
  assert.equal(ask('"storage battery" problems'), false);
});

test("a field search is left to keyword search", () => {
  assert.equal(ask("author:edison batteries"), false);
});

test("an exclusion is left to keyword search", () => {
  assert.equal(ask("aviation -helicopter"), false);
});

test("Boolean operators are left to keyword search", () => {
  assert.deepEqual([ask("battery AND storage"), ask("battery OR storage"), ask("battery and storage")], [false, false, true]);
});

test("an explicit wildcard is left to keyword search", () => {
  assert.equal(ask("batter* storage"), false);
});

test("exact phrase mode is left to keyword search", () => {
  assert.equal(ask("hearing loss", "exact"), false);
});

test("a year range is a filter, not words to match by meaning", () => {
  assert.equal(meaningText("year:1920..1925 cement houses"), "cement houses");
});

test("a document scores its best passage", () => {
  const vectors = Int8Array.from([127, 0, 0, 127, 64, 0]);
  assert.equal(Math.round(scorePassages([1, 0], vectors, Uint32Array.from([5, 5, 6]), 2).get(5) * 100), 100);
});

// An archive where most documents sit at `typical`, as unrelated documents do for any one query.
const archive = (matches, typical = 0.43) => new Map([...Array.from({ length: 30 }, (_, n) => [100 + n, typical]), ...matches]);

test("documents far below the best match are not shown", () => {
  assert.deepEqual(topMatches(archive([[1, 0.8], [2, 0.75], [3, 0.6]], 0.3)), [1, 2]);
});

test("a match must stand out from the archive's typical document, not merely sit near the best (regression: off-topic minutes for concrete houses, smoke test 21 November)", () => {
  assert.deepEqual(topMatches(archive([[1, 0.674], [2, 0.628], [3, 0.575]])), [1, 2]);
});

test("nothing is shown when even the best match is weak", () => {
  assert.deepEqual(topMatches(new Map([[1, 0.4], [2, 0.3]])), []);
});

test("nothing is shown when the best match falls short of 0.55", () => {
  assert.deepEqual(topMatches(archive([[1, 0.54]], 0.42)), []);
});

test("no more than the limit are shown", () => {
  assert.equal(topMatches(new Map([...Array(100).keys()].map((n) => [n, 0.7])), { limit: 40 }).length, 40);
});

test("the shortlist limit leaves room for matches inside the selected collection", () => {
  const scores = new Map(Array.from({ length: 120 }, (_, n) => [n, n < 40 ? 0.9 - n * 0.001 : 0.3]));
  scores.set(40, 0.84);
  assert.deepEqual(topMatches(scores, { allowed: new Set([40]) }), [40]);
});

test("a filter cannot lower the archive-wide cutoff and admit weaker matches", () => {
  const scores = archive([[1, 0.86], ...Array.from({ length: 15 }, (_, n) => [n + 2, 0.58])], 0.25);
  assert.deepEqual(topMatches(scores, { allowed: new Set(Array.from({ length: 15 }, (_, n) => n + 2)) }), []);
});

test("meaning matches in another collection preserve the keyword fallback in the selected collection", () => {
  const facets = { folder: [0, 1], year: [1920, 1921], type: [0, 0], status: [0, 0] };
  const filters = { folders: new Set([0]), types: new Set(), statuses: new Set() };
  const { matches, counts } = meaningScope([1], [], [0], facets, filters);
  assert.deepEqual([matches, counts.folder], [[], new Map([[0, 1], [1, 1]])]);
});

test("the fallback keeps collections containing strict keyword matches available", () => {
  const facets = { folder: [0, 1, 2], year: [1920, 1921, 1922], type: [0, 0, 0], status: [0, 0, 0] };
  const filters = { folders: new Set([0]), types: new Set(), statuses: new Set() };
  const { counts } = meaningScope([1], [2], [0], facets, filters);
  assert.equal(counts.folder.get(2), 1);
});

test("a document found both ways outranks one found only one way", () => {
  const scores = fuse([[1, 2], [3, 1]]);
  assert.ok(scores.get(1) > scores.get(2) && scores.get(1) > scores.get(3));
});

test("a keyword match counted at half weight falls below the best meaning match (regression: unrelated letter first for hearing loss, smoke test 21 November)", () => {
  const scores = fuse([[9], [1]], { weight: (list) => (list === 0 ? keywordWeight(0.4, 0.7) : 1) });
  assert.ok(scores.get(1) > scores.get(9));
});

test("a keyword match without an embedding retains its full weight", () => {
  assert.equal(keywordWeight(undefined, 0.7), 1);
});

test("the cutoff is the best match itself when no document is typical of the archive", () => {
  assert.equal(meaningCutoff(new Map([[1, 0.7], [2, 0.7]])), 0.7);
});
