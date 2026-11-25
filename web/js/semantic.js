// Search by meaning: the query becomes a vector in a worker, is compared with every published passage, and is merged with keyword results.
import { fetchJSON, remember } from "./core.js";
import { applyFilters } from "./search.js";

/** Whether a query is plain words worth matching by meaning; explicit search syntax stays keyword-only. */
export function eligible(query, text) {
  const words = meaningText(text);
  if (!query || query.mode === "exact" || /"/.test(String(text || "")) || /(?:^|\s)(?:AND|OR|NOT)(?=\s|$)/.test(words)) return false;
  if (query.clauses.some((clause) => clause.negate || clause.alts.some((atom) => atom?.field || atom?.raw?.endsWith("*")))) return false;
  return (words.match(/\p{L}{3,}/gu) || []).length >= 2;
}

/** The reader's words without year ranges, which are already applied as a filter. */
export function meaningText(text) {
  return String(text || "").replace(/\b(?:year|date):\s*\S+/gi, " ").replace(/\s+/g, " ").trim();
}

/** Each document's best passage similarity to the query; vectors are unit length, stored as signed bytes scaled by `scale`. */
export function scorePassages(query, vectors, ordinals, dim, scale = 127) {
  if (query?.length !== dim || !query.every(Number.isFinite) || vectors.length !== ordinals.length * dim) {
    throw new Error("The semantic index and query vector have incompatible dimensions");
  }
  const best = new Map();
  for (let row = 0, offset = 0; row < ordinals.length; row++, offset += dim) {
    let dot = 0;
    for (let i = 0; i < dim; i++) dot += query[i] * vectors[offset + i];
    const score = dot / scale;
    const ordinal = ordinals[row];
    if (!(best.get(ordinal) >= score)) best.set(ordinal, score);
  }
  return best;
}

function median(values) {
  const sorted = Float64Array.from(values).sort();
  return sorted.length ? sorted[sorted.length >> 1] : 0;
}

/**
 * The lowest similarity that still counts as a match, or null when even the best is weak.
 * How similar an unrelated document looks depends on the query, so a match must sit in the top `band` of the gap
 * between the archive's typical document and the best one, as well as within `within` of the best.
 */
export function meaningCutoff(best, { floor = 0.55, within = 0.12, band = 0.4 } = {}) {
  let top = -Infinity;
  for (const score of best.values()) if (score > top) top = score;
  if (!(top >= floor)) return null;
  // With clusters only the nearest are scored, so the typical document comes from every cluster's mean instead.
  const typical = Number.isFinite(best.typical) ? best.typical : median(best.values());
  return Math.max(top - within, top - band * Math.max(0, top - typical));
}

/** The documents close enough in meaning to show, best first, and no more than `limit`. */
export function topMatches(best, { limit = 40, allowed = null, cutoff, ...options } = {}) {
  const threshold = cutoff === undefined ? meaningCutoff(best, options) : cutoff;
  if (threshold === null) return [];
  return [...best.entries()]
    .filter(([ordinal, score]) => score >= threshold && (!allowed || allowed.has(ordinal)))
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit).map(([ordinal]) => ordinal);
}

export function meaningScope(closest, strict, fallback, facets, filters) {
  const candidates = new Set([...strict, ...closest]);
  const matching = applyFilters(candidates, facets, filters);
  const allowed = new Set(matching.kept);
  const matches = closest.filter((ordinal) => allowed.has(ordinal));
  if (!fallback || matches.length) return { matches, counts: matching.counts };
  // The fallback can use different keyword expansions, so keep strict matches available through other facets.
  for (const ordinal of fallback) candidates.add(ordinal);
  return { matches, counts: applyFilters(candidates, facets, filters).counts };
}

export function keywordWeight(similarity, cutoff) {
  return cutoff !== null && Number.isFinite(similarity) && similarity < cutoff ? 0.5 : 1;
}

/** Reciprocal Rank Fusion: a document scores the sum of weight / (k + rank) over each list it appears in. */
export function fuse(lists, { k = 60, weight = () => 1 } = {}) {
  const scores = new Map();
  lists.forEach((list, which) => {
    list.forEach((ordinal, rank) => scores.set(ordinal, (scores.get(ordinal) || 0) + weight(which, ordinal) / (k + rank + 1)));
  });
  return scores;
}

function validMeta(meta) {
  const common = Number.isSafeInteger(meta?.count) && meta.count >= 1 && Number.isSafeInteger(meta.dim) && meta.dim >= 1
    && Number.isFinite(meta.scale) && meta.scale > 0;
  if (!common) return false;
  if (meta.layout === "flat") return typeof meta.files?.vectors === "string" && typeof meta.files?.ordinals === "string";
  return meta.layout === "ivf" && Array.isArray(meta.clusters) && meta.clusters.length >= 1
    && meta.clusters.every((n) => Number.isSafeInteger(n) && n >= 0) && meta.clusters.reduce((a, b) => a + b, 0) === meta.count
    && Number.isSafeInteger(meta.probe?.vectors) && meta.probe.vectors >= 1
    && /^[a-z0-9.]+$/.test(meta.files?.centroids || "") && /^[a-z0-9]+\/$/.test(meta.files?.clusters || "")
    && (meta.files.ordinals === undefined || /^[a-z0-9.-]+$/.test(meta.files.ordinals))
    && (meta.centre_norms === undefined || (Array.isArray(meta.centre_norms) && meta.centre_norms.length === meta.clusters.length
      && meta.centre_norms.every((n) => Number.isFinite(n) && n >= 0 && n <= 1.01)));
}

/** A query's similarity to each cluster's mean, which is the cluster's average similarity to it. */
export function centreSimilarities(query, centres, dim, scale = 127) {
  if (query?.length !== dim || !query.every(Number.isFinite) || centres.length % dim) {
    throw new Error("The semantic index and query vector have incompatible dimensions");
  }
  const out = new Float64Array(centres.length / dim);
  for (let c = 0, offset = 0; c < out.length; c++, offset += dim) {
    let dot = 0;
    for (let i = 0; i < dim; i++) dot += query[i] * centres[offset + i];
    out[c] = dot / scale;
  }
  return out;
}

/** Each cluster's average similarity to the query, from its direction's similarity and its centre's length when the build gives them. */
export function averageSimilarities(similarity, norms) {
  return norms ? similarity.map((value, n) => value * norms[n]) : similarity;
}

/** The clusters to read for a query: the nearest first, until together they hold `budget` passages. */
export function probeClusters(similarity, counts, budget, allowed = null) {
  const order = [...similarity.keys()].sort((a, b) => similarity[b] - similarity[a]);
  const picked = [];
  let total = 0;
  for (const n of order) {
    if (!counts[n] || (allowed && !allowed.has(n))) continue;
    picked.push(n);
    total += counts[n];
    if (total >= budget) break;
  }
  return picked;
}

/** The value at which half the weight lies below, here the similarity of the archive's middle passage to a query. */
export function weightedMedian(values, weights) {
  const order = [...values.keys()].sort((a, b) => values[a] - values[b]);
  const half = weights.reduce((a, b) => a + b, 0) / 2;
  let seen = 0;
  for (const n of order) {
    seen += weights[n];
    if (seen >= half) return values[n];
  }
  return 0;
}

async function fetchBytes(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw new Error(`${url} answered ${response.status}`);
    return await response.arrayBuffer();
  } finally {
    clearTimeout(timer);
  }
}

/**
 * The browser side of search by meaning: vectors load on first use, and the query model runs in a worker so the page never stalls.
 * `dataUrl(path)` resolves a file of the current build; `vendor` is the site's vendor folder.
 */
export function createSemantic({ dataUrl, vendor }) {
  const cache = new Map();
  let worker = null;
  let next = 0;
  const waiting = new Map();
  const metadata = () => remember(cache, "meta", async () => {
    const meta = await fetchJSON(await dataUrl("vectors/meta.json"));
    if (!validMeta(meta)) throw new Error("The semantic index metadata is invalid");
    return meta;
  });
  const centroids = () => remember(cache, "centroids", async () => {
    const meta = await metadata();
    const bytes = await fetchBytes(await dataUrl(`vectors/${meta.files.centroids}`));
    if (bytes.byteLength !== meta.clusters.length * meta.dim) throw new Error("The semantic index files do not match their declared sizes");
    return new Int8Array(bytes);
  });
  const clusterOrdinals = () => remember(cache, "cluster-ordinals", async () => {
    const meta = await metadata();
    const bytes = await fetchBytes(await dataUrl(`vectors/${meta.files.ordinals}`));
    if (bytes.byteLength !== meta.count * Uint32Array.BYTES_PER_ELEMENT) {
      throw new Error("The semantic index files do not match their declared sizes");
    }
    return new Uint32Array(bytes);
  });
  // Each cluster is kept once read, so a second search near the first downloads little or nothing.
  const cluster = (n) => remember(cache, `cluster-${n}`, async () => {
    const meta = await metadata();
    const count = meta.clusters[n];
    const bytes = await fetchBytes(await dataUrl(`vectors/${meta.files.clusters}${String(n).padStart(5, "0")}.bin`));
    if (bytes.byteLength !== count * (meta.dim + Uint32Array.BYTES_PER_ELEMENT)) {
      throw new Error("The semantic index files do not match their declared sizes");
    }
    const offset = count * meta.dim;
    const ordinals = offset % Uint32Array.BYTES_PER_ELEMENT ? new Uint32Array(bytes.slice(offset)) : new Uint32Array(bytes, offset);
    return { vectors: new Int8Array(bytes, 0, offset), ordinals };
  });
  const load = () => remember(cache, "vectors", async () => {
    const meta = await metadata();
    const paths = [meta.files.vectors, meta.files.ordinals];
    const [vectors, ordinals] = await Promise.all(paths.map(async (file) => fetchBytes(await dataUrl(`vectors/${file}`))));
    if (vectors.byteLength !== meta.count * meta.dim || ordinals.byteLength !== meta.count * Uint32Array.BYTES_PER_ELEMENT) {
      throw new Error("The semantic index files do not match their declared sizes");
    }
    return { meta, vectors: new Int8Array(vectors), ordinals: new Uint32Array(ordinals) };
  });
  const fail = (message) => {
    for (const { reject } of waiting.values()) reject(new Error(message));
    waiting.clear();
    worker = null;
  };
  const embed = (text, meta) => new Promise((resolve, reject) => {
    if (!worker) {
      worker = new Worker(new URL("semantic-worker.js", import.meta.url), { type: "module" });
      worker.onmessage = ({ data }) => {
        const job = waiting.get(data.id);
        if (!job) return;
        waiting.delete(data.id);
        if (data.error) job.reject(new Error(data.error)); else job.resolve(data.vector);
      };
      worker.onerror = (event) => fail(event.message || "the search model could not start");
    }
    const id = ++next;
    waiting.set(id, { resolve, reject });
    worker.postMessage({ id, text, setup: { vendor, model: meta.hf, revision: meta.revision, dtype: meta.dtype, prefix: meta.query_prefix } });
  });
  return {
    /** Each document's best similarity to `text`; with clusters, among the nearest clusters, carrying the archive's `typical` similarity. */
    async search(text, allowed = null) {
      const meta = await metadata();
      if (meta.layout === "flat") {
        const { vectors, ordinals } = await load();
        return scorePassages(await embed(text, meta), vectors, ordinals, meta.dim, meta.scale);
      }
      const [query, centres, owners] = await Promise.all([embed(text, meta), centroids(),
        allowed && meta.files.ordinals ? clusterOrdinals() : null]);
      const similarity = centreSimilarities(query, centres, meta.dim, meta.scale);
      let eligibleClusters = null;
      if (owners) {
        eligibleClusters = new Set();
        let start = 0;
        for (const [n, count] of meta.clusters.entries()) {
          const end = start + count;
          for (let i = start; i < end; i++) {
            if (allowed.has(owners[i])) {
              eligibleClusters.add(n);
              break;
            }
          }
          start = end;
        }
      }
      const best = new Map();
      // Keep the archive-wide probe for the cutoff and facets, and add clusters that can contain filtered matches.
      const global = probeClusters(similarity, meta.clusters, meta.probe.vectors);
      const scoped = eligibleClusters ? probeClusters(similarity, meta.clusters, meta.probe.vectors, eligibleClusters) : [];
      const picked = [...new Set([...global, ...scoped])];
      for (let start = 0; start < picked.length; start += 12) {
        for (const part of await Promise.all(picked.slice(start, start + 12).map(cluster))) {
          for (const [ordinal, score] of scorePassages(query, part.vectors, part.ordinals, meta.dim, meta.scale)) {
            if (!(best.get(ordinal) >= score)) best.set(ordinal, score);
          }
        }
      }
      // Centres are stored as directions; times their length, each gives its cluster's average similarity.
      best.typical = weightedMedian(averageSimilarities(similarity, meta.centre_norms), meta.clusters);
      return best;
    },
    /** Whether this build has vectors at all, without loading the model. */
    async available() {
      try { await metadata(); return true; } catch { return false; }
    },
  };
}
