// Search by meaning: which queries use it, how passages are scored, how close is close enough, and how the two rankings merge.
import assert from "node:assert/strict";
import test from "node:test";
import { parseQuery } from "../../web/js/search.js";
import { eligible, fuse, meaningText, scorePassages, topMatches } from "../../web/js/semantic.js";

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

test("documents far below the best match are not shown", () => {
  assert.deepEqual(topMatches(new Map([[1, 0.8], [2, 0.75], [3, 0.6]])), [1, 2]);
});

test("nothing is shown when even the best match is weak", () => {
  assert.deepEqual(topMatches(new Map([[1, 0.4], [2, 0.3]])), []);
});

test("no more than the limit are shown", () => {
  assert.equal(topMatches(new Map([...Array(100).keys()].map((n) => [n, 0.7])), { limit: 40 }).length, 40);
});

test("the shortlist limit leaves room for matches inside the selected collection", () => {
  const scores = new Map(Array.from({ length: 40 }, (_, n) => [n, 0.9 - n * 0.001]));
  scores.set(40, 0.72);
  assert.deepEqual(topMatches(scores, { allowed: new Set([40]) }), [40]);
});

test("a document found both ways outranks one found only one way", () => {
  const scores = fuse([[1, 2], [3, 1]]);
  assert.ok(scores.get(1) > scores.get(2) && scores.get(1) > scores.get(3));
});
