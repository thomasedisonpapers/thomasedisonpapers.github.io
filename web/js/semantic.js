// Search by meaning: the query becomes a vector in a worker, is compared with every published passage, and is merged with keyword results.
import { fetchJSON } from "./core.js";

/** Whether a query is plain words worth matching by meaning; exact phrases, quotes, fields, IDs and exclusions stay keyword-only. */
export function eligible(query, text) {
  if (!query || query.mode === "exact" || /"/.test(String(text || ""))) return false;
  if (query.clauses.some((clause) => clause.negate || clause.alts.some((atom) => atom && atom.field))) return false;
  return (meaningText(text).match(/\p{L}{3,}/gu) || []).length >= 2;
}

/** The reader's words without year ranges, which are already applied as a filter. */
export function meaningText(text) {
  return String(text || "").replace(/\b(?:year|date):\s*\S+/gi, " ").replace(/\s+/g, " ").trim();
}

/** Each document's best passage similarity to the query; vectors are unit length, stored as signed bytes scaled by `scale`. */
export function scorePassages(query, vectors, ordinals, dim, scale = 127) {
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
  const typical = median(best.values());
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

async function fetchBytes(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);
  return response.arrayBuffer();
}

/**
 * The browser side of search by meaning: vectors load on first use, and the query model runs in a worker so the page never stalls.
 * `dataUrl(path)` resolves a file of the current build; `vendor` is the site's vendor folder.
 */
export function createSemantic({ dataUrl, vendor }) {
  let loading = null;
  let worker = null;
  let next = 0;
  const waiting = new Map();
  const load = () => {
    if (!loading) {
      loading = (async () => {
        const meta = await fetchJSON(await dataUrl("vectors/meta.json"));
        const [vectors, ordinals] = await Promise.all([fetchBytes(await dataUrl(`vectors/${meta.files.vectors}`)),
          fetchBytes(await dataUrl(`vectors/${meta.files.ordinals}`))]);
        return { meta, vectors: new Int8Array(vectors), ordinals: new Uint32Array(ordinals) };
      })();
      loading.catch(() => { loading = null; });
    }
    return loading;
  };
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
    /** Each document's best similarity to `text`. */
    async search(text) {
      const { meta, vectors, ordinals } = await load();
      const query = await embed(text, meta);
      return scorePassages(query, vectors, ordinals, meta.dim, meta.scale);
    },
    /** Whether this build has vectors at all, without loading the model. */
    async available() {
      try { await dataUrl("vectors/meta.json").then(fetchJSON); return true; } catch { return false; }
    },
  };
}
