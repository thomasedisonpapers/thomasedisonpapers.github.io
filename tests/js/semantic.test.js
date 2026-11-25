// Search by meaning: which queries use it, how passages are scored, how close is close enough, and how the two rankings merge.
import assert from "node:assert/strict";
import test from "node:test";
import { parseQuery } from "../../web/js/search.js";
import { averageSimilarities, createSemantic, eligible, fuse, keywordWeight, meaningCutoff, meaningScope, meaningText, probeClusters, scorePassages, topMatches, weightedMedian } from "../../web/js/semantic.js";

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

test("a query vector with the wrong dimension is rejected before scoring", () => {
  assert.throws(() => scorePassages([1], Int8Array.of(127, 0), Uint32Array.of(5), 2), /incompatible dimensions/);
});

test("semantic search refuses a truncated download before starting the model", async (t) => {
  globalThis.location = { href: "https://example.test/search.html", host: "example.test" };
  t.after(() => { delete globalThis.location; });
  t.mock.method(globalThis, "fetch", async (url) => {
    if (url === "vectors/meta.json") return Response.json({ layout: "flat", count: 2, dim: 2, scale: 127,
      files: { vectors: "vectors.i8", ordinals: "ordinals.u32" } });
    return new Response(url.endsWith(".i8") ? Int8Array.of(127, 0) : Uint32Array.of(5, 6));
  });
  const search = createSemantic({ dataUrl: async (path) => path });
  await assert.rejects(search.search("storage battery"), /do not match their declared sizes/);
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

test("the clusters nearest a query are read first until they hold the passage budget", () => {
  assert.deepEqual(probeClusters(Float64Array.of(0.1, 0.9, 0.5, 0.7), [100, 100, 100, 100], 250), [1, 3, 2]);
});

test("an empty cluster is never fetched", () => {
  assert.deepEqual(probeClusters(Float64Array.of(0.9, 0.5), [0, 10], 5), [1]);
});

test("the typical similarity is the one at which half the passages lie below", () => {
  assert.equal(weightedMedian(Float64Array.of(0.2, 0.5, 0.8), [10, 1, 10]), 0.5);
});

test("with clusters the cutoff measures the gap from the archive's typical passage, not from the clusters read", () => {
  const best = new Map([[1, 0.8], [2, 0.75], [3, 0.74]]);
  best.typical = 0.4;
  assert.equal(Math.round(meaningCutoff(best) * 100), 68);
});

test("a search over clusters reads only the nearest and scores their passages", async (t) => {
  globalThis.location = { href: "https://example.test/search.html", host: "example.test" };
  const asked = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    asked.push(url);
    if (url === "vectors/meta.json") return Response.json({ layout: "ivf", count: 2, dim: 4, scale: 127, clusters: [1, 1],
      probe: { vectors: 1 }, files: { centroids: "centroids.i8", clusters: "clusters/" } });
    if (url.endsWith("centroids.i8")) return new Response(Int8Array.of(127, 0, 0, 0, 0, 127, 0, 0));
    const bytes = new Uint8Array(8);
    bytes.set(new Uint8Array(Int8Array.of(127, 0, 0, 0).buffer));
    bytes.set(new Uint8Array(Uint32Array.of(url.endsWith("00000.bin") ? 5 : 6).buffer), 4);
    return new Response(bytes);
  });
  // The query model runs in a worker; this one answers at once with the vector of the first cluster's direction.
  globalThis.Worker = class { postMessage({ id }) { setTimeout(() => this.onmessage({ data: { id, vector: Float32Array.of(1, 0, 0, 0) } })); } };
  t.after(() => { delete globalThis.location; delete globalThis.Worker; });
  const best = await createSemantic({ dataUrl: async (path) => path }).search("storage battery");
  assert.deepEqual([[...best.keys()], asked.filter((u) => u.includes("clusters/"))], [[5], ["vectors/clusters/00000.bin"]]);
});

test("a filtered search also probes a cluster containing the selected collection", async (t) => {
  globalThis.location = { href: "https://example.test/search.html", host: "example.test" };
  t.mock.method(globalThis, "fetch", async (url) => {
    if (url === "vectors/meta.json") return Response.json({ layout: "ivf", count: 3, dim: 2, scale: 127, clusters: [1, 1, 1],
      probe: { vectors: 1 }, files: { centroids: "centroids.i8", clusters: "clusters/", ordinals: "cluster-ordinals.u32" } });
    if (url.endsWith("centroids.i8")) return new Response(Int8Array.of(127, 0, 110, 0, 100, 0));
    if (url.endsWith("cluster-ordinals.u32")) return new Response(Uint32Array.of(5, 6, 7));
    const index = Number(url.match(/(\d+)\.bin$/)[1]);
    const bytes = new ArrayBuffer(6);
    new Int8Array(bytes).set([127 - 10 * index, 0]);
    new DataView(bytes).setUint32(2, 5 + index, true);
    return new Response(bytes);
  });
  globalThis.Worker = class { postMessage({ id }) { queueMicrotask(() => this.onmessage({ data: { id, vector: Float32Array.of(1, 0) } })); } };
  t.after(() => { delete globalThis.location; delete globalThis.Worker; });

  const best = await createSemantic({ dataUrl: async (path) => path }).search("storage battery", new Set([7]));
  assert.deepEqual([...best.keys()], [5, 7]);
});

test("a cluster's average similarity is its direction's similarity times its centre's length", () => {
  assert.deepEqual([...averageSimilarities(Float64Array.of(0.8, 0.5), [0.5, 1])], [0.4, 0.5]);
});

test("a build without centre lengths keeps its centres as averages", () => {
  const similarity = Float64Array.of(0.8, 0.5);
  assert.equal(averageSimilarities(similarity), similarity);
});
