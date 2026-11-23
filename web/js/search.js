// The archive's search: a query language for readers, run against the static index the builder writes.
import { stem } from "./stemmer.js";

export { stem };

export const FIELD_NAMES = {
  title: "title", author: "author", from: "author", recipient: "recipient", to: "recipient", names: "names", people: "names",
  person: "names", name: "names", subject: "subjects", subjects: "subjects", type: "type", place: "place", places: "place",
  folder: "folder", collection: "folder", description: "description", text: "text", transcription: "text", id: "id",
};
export const FIELD_LABELS = {
  title: "title", author: "author", recipient: "recipient", names: "people", subjects: "subject", type: "type",
  place: "place", folder: "collection", description: "description", text: "transcription", id: "document ID",
};
const FIELD_BITS = { title: 1, author: 2, recipient: 4, names: 8, subjects: 16, type: 32, place: 64, folder: 128,
  description: 256, text: 512, id: 1024 };

// Letters that do not decompose into a base letter are spelled out first, so "Straße" and "Ætna" stay searchable.
const SPELLED = { "ß": "ss", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ø": "o", "Ø": "o", "ł": "l", "Ł": "l", "đ": "d", "Đ": "d", "þ": "th", "Þ": "th" };

// Kept identical to builder/textindex.py: tests/fixtures/tokenize.json checks both.
export function fold(text) {
  return String(text || "").replace(/[ßæÆœŒøØłŁđĐþÞ]/g, (c) => SPELLED[c]).normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
}

export function tokenize(text) {
  return (fold(text).match(/[a-z0-9]+/g) || []).filter((w) => w.length > 1 || /^\d+$/.test(w));
}

export const PAIR_SHARDS = 512;
let STOPWORDS = new Set();
let VARIANTS = {};

// The builder lists the common words it left out of the index in meta.json; the site leaves them out of queries too.
export function setStopwords(words) {
  STOPWORDS = new Set(words || []);
}

// The editors' list of other spellings ("lab" for "laboratory"), symmetric, read from meta.json.
export function setVariants(map) {
  VARIANTS = map && typeof map === "object" ? map : {};
}

export function fnv1a(text) {
  let value = 2166136261;
  for (const byte of new TextEncoder().encode(text)) value = Math.imul(value ^ byte, 16777619) >>> 0;
  return value;
}

// Kept identical to builder/textindex.py shard_of: a word by its first three characters, a word pair by a hash.
export function shardOf(term) {
  if (term.includes(" ")) return `p${(fnv1a(term) % PAIR_SHARDS).toString(16).padStart(3, "0")}`;
  return term.length >= 3 ? term.slice(0, 3) : term + "_".repeat(3 - term.length);
}

// Kept identical to builder/textindex.py phrase_keys: neighbouring pairs of uncommon words, stemmed.
export function phraseKeys(words) {
  const pairs = [];
  for (let i = 0; i + 1 < words.length; i++) {
    if (!STOPWORDS.has(words[i]) && !STOPWORDS.has(words[i + 1])) pairs.push(`${stem(words[i])} ${stem(words[i + 1])}`);
  }
  return pairs.length ? pairs : words.filter((w) => !STOPWORDS.has(w)).map(stem);
}

function atomFor(raw, field, { phrase = false, broad = false } = {}) {
  const mask = field ? FIELD_BITS[field] : 0;
  if (field === "id" && !phrase && /^[A-Za-z0-9_-]+$/.test(raw) && /[-_]/.test(raw)) {
    return { kind: "term", words: [fold(raw)], mask, field, raw };
  }
  const prefix = !phrase && /\*$/.test(raw);
  const words = tokenize(raw);
  if (!words.length) return null;
  if (words.length === 1 && STOPWORDS.has(words[0]) && !prefix) return { skipped: words[0] };
  if (phrase || words.length > 1) return { kind: words.length > 1 ? "phrase" : "term", words, mask, field, raw };
  if ((prefix || broad) && words[0].length >= 3) return { kind: "prefix", words, mask, field, raw };
  return { kind: "term", words, mask, field, raw };
}

/** Turns what a reader typed into clauses; nothing they type can make it throw. */
export function parseQuery(input, mode = "all") {
  const text = String(input || "").trim();
  const query = { clauses: [], yearFrom: null, yearTo: null, notes: [], mode };
  if (!text) return query;
  if (mode === "exact") {
    const atom = atomFor(text.replace(/"/g, " "), null, { phrase: true });
    if (atom) query.clauses.push({ alts: [atom], negate: false });
    return query;
  }
  // A field name may be followed by a space ("title: cement"); a known word like OR only counts unquoted and unprefixed.
  const pattern = /(-?)(?:([a-zA-Z]+):\s*)?(?:"([^"]*)"?|(\S+))/g;
  let match;
  let joinNext = false;
  let explicitAnd = false;
  while ((match = pattern.exec(text))) {
    const [, minus, rawField, quoted, bare] = match;
    const keyword = quoted === undefined && !rawField && !minus ? bare : null;
    if (keyword === "OR") { joinNext = query.clauses.length > 0; continue; }
    if (keyword === "AND") { explicitAnd = true; continue; }
    if (keyword === "NOT") { query.pendingNot = true; continue; }
    let field = null;
    let value = quoted ?? bare;
    if (rawField) {
      const key = rawField.toLowerCase();
      if (key === "year" || key === "date") {
        const years = String(value).match(/^(\d{4})(?:\s*(?:\.\.|-|–|to)\s*(\d{4}))?$/);
        const excluded = minus === "-" || query.pendingNot;
        query.pendingNot = false;
        if (years && excluded) {
          query.notes.push(`Leaving out a year is not supported, so "${minus}${rawField}:${value}" was ignored.`);
        } else if (years) {
          query.yearFrom = Number(years[1]);
          query.yearTo = Number(years[2] || years[1]);
          if (query.yearFrom > query.yearTo) [query.yearFrom, query.yearTo] = [query.yearTo, query.yearFrom];
        } else {
          query.notes.push(`"${rawField}:${value}" is not a year or range such as year:1878..1880, so it was ignored.`);
        }
        continue;
      }
      if (FIELD_NAMES[key]) field = FIELD_NAMES[key];
      else { query.notes.push(`"${rawField}:" is not a field this archive knows, so it was searched as ordinary words.`); value = `${rawField} ${value}`; }
    }
    const atom = atomFor(value, field, { phrase: quoted !== undefined, broad: mode === "any" });
    if (!atom) continue;
    if (atom.skipped) {
      query.notes.push(`"${atom.skipped}" is too common to search for on its own, so it was left out.`);
      continue;
    }
    const variants = atom.kind !== "prefix" && atom.field !== "id" ? VARIANTS[atom.words.join(" ")] || [] : [];
    const extra = variants.map((v) => atomFor(quoted !== undefined || v.includes(" ") ? v : v, field, { phrase: v.includes(" ") }))
      .filter((a) => a && !a.skipped);
    if (extra.length) atom.variants = extra.map((a) => a.words.join(" "));
    const negate = minus === "-" || Boolean(query.pendingNot);
    query.pendingNot = false;
    // In "any word" mode words join one OR group, unless the reader wrote AND between them.
    if ((joinNext || (mode === "any" && !explicitAnd)) && query.clauses.length && !negate) {
      const last = query.clauses[query.clauses.length - 1];
      if (!last.negate) { last.alts.push(atom, ...extra); joinNext = false; explicitAnd = false; continue; }
    }
    joinNext = false;
    explicitAnd = false;
    query.clauses.push({ alts: [atom, ...extra], negate });
  }
  delete query.pendingNot;
  return query;
}

/** How the query was read, in plain words, for the "Search details" line. */
export function describeQuery(query) {
  const parts = query.clauses.map((clause) => {
    const alts = clause.alts.map((a) => {
      const words = a.kind === "phrase" ? `the phrase "${a.words.join(" ")}"` : a.kind === "prefix" ? `words starting "${a.words[0]}"` : `"${a.words[0]}"`;
      return a.field ? `${words} in ${FIELD_LABELS[a.field]}` : words;
    });
    return (clause.negate ? "not " : "") + (alts.length > 1 ? `(${alts.join(" or ")})` : alts[0]);
  });
  let out = parts.length ? `Documents with ${parts.join(" and ")}` : "All documents";
  if (query.yearFrom) out += query.yearFrom === query.yearTo ? `, dated ${query.yearFrom}` : `, dated ${query.yearFrom}–${query.yearTo}`;
  return out + ".";
}

// Postings are [gap, mask, gap, mask, ...]: each gap is the distance from the previous document that has the term.
function decode(list, required) {
  const out = new Map();
  let doc = 0;
  for (let i = 0; i < list.length; i += 2) {
    doc += list[i];
    const mask = list[i + 1] & (required || 0x7fffffff);
    if (mask) out.set(doc, mask);
  }
  return out;
}

export class Index {
  /** `base` is one build's index folder and `known` its list of files, so a term with no file costs no request. */
  constructor(base, known, loadJSON) {
    this.base = base;
    this.known = known ? new Set(known) : null;
    this.loadJSON = loadJSON;
    this.shards = new Map();
  }

  shard(name) {
    if (this.known && !this.known.has(name)) return Promise.resolve({});
    if (!this.shards.has(name)) {
      const promise = this.loadJSON(`${this.base}${encodeURIComponent(name)}.json`);
      this.shards.set(name, promise);
      // A failed file is forgotten so the next search asks again, and the failure reaches the page instead of reading as "no results".
      promise.catch(() => { if (this.shards.get(name) === promise) this.shards.delete(name); });
    }
    return this.shards.get(name);
  }

  async term(word, mask) {
    return decode((await this.shard(shardOf(word)))[word] || [], mask);
  }

  // Postings as a Map of document ordinal to the mask of fields the atom matched in.
  async atom(atom) {
    if (atom.kind === "term") return this.term(atom.field === "id" ? atom.words[0] : stem(atom.words[0]), atom.mask);
    if (atom.kind === "prefix") {
      const [word] = atom.words;
      const normalized = stem(word);
      const shard = shardOf(word);
      const begins = [word, normalized];
      // Stemming can move the word to another shard, so also look up the exact stem there.
      const [terms, out] = await Promise.all([this.shard(shard),
        shardOf(normalized) === shard ? new Map() : this.term(normalized, atom.mask)]);
      for (const [term, list] of Object.entries(terms)) {
        if (term.includes(" ") || !begins.some((b) => term.startsWith(b))) continue;
        for (const [doc, mask] of decode(list, atom.mask)) out.set(doc, (out.get(doc) || 0) | mask);
      }
      return out;
    }
    // A phrase is its neighbouring pairs of uncommon words (or those words alone), all found in one field.
    let current = null;
    for (const key of phraseKeys(atom.words)) {
      const found = await this.term(key, atom.mask);
      const next = new Map();
      for (const [doc, mask] of found) {
        const both = current ? mask & (current.get(doc) || 0) : mask;
        if (both) next.set(doc, both);
      }
      current = next;
    }
    return current || new Map();
  }
}

/** Runs a parsed query: matching ordinals with scores and the fields each matched in. */
export async function runQuery(query, index, meta) {
  const total = meta.documents;
  const weights = meta.weights;
  const bitWeight = Object.entries(FIELD_BITS).map(([name, bit]) => [bit, weights[name] || 1]);
  const positive = query.clauses.filter((c) => !c.negate);
  const negative = query.clauses.filter((c) => c.negate);
  let matched = null;
  const scores = new Map();
  const where = new Map();
  const weightOf = (mask) => {
    let best = 1;
    for (const [bit, weight] of bitWeight) if (mask & bit && weight > best) best = weight;
    return best;
  };
  for (const clause of positive) {
    // Every alternative a document matches adds to its score, so "cement kiln quarry" ranks a document with all three first.
    const union = new Map();
    const gained = new Map();
    for (const atom of clause.alts) {
      const found = await index.atom(atom);
      const idf = Math.log(1 + total / Math.max(1, found.size));
      for (const [doc, mask] of found) {
        union.set(doc, (union.get(doc) || 0) | mask);
        gained.set(doc, (gained.get(doc) || 0) + idf * weightOf(mask));
      }
    }
    const next = new Map();
    for (const [doc, mask] of union) {
      if (matched && !matched.has(doc)) continue;
      next.set(doc, true);
      scores.set(doc, (scores.get(doc) || 0) + gained.get(doc));
      where.set(doc, (where.get(doc) || 0) | mask);
    }
    matched = next;
  }
  for (let i = 0; i + 1 < positive.length; i++) {
    const [a, b] = [positive[i].alts[0], positive[i + 1].alts[0]];
    if (!a || !b || a.kind !== "term" || b.kind !== "term" || a.field || b.field) continue;
    if (STOPWORDS.has(a.words[0]) || STOPWORDS.has(b.words[0])) continue;
    const together = await index.term(`${stem(a.words[0])} ${stem(b.words[0])}`, 0);
    const idf = Math.log(1 + total / Math.max(1, together.size));
    for (const [doc, mask] of together) {
      if (matched && matched.has(doc)) scores.set(doc, (scores.get(doc) || 0) + idf * weightOf(mask) * 0.75);
    }
  }
  let ordinals = matched ? [...matched.keys()] : Array.from({ length: total }, (_, n) => n);
  for (const clause of negative) {
    const excluded = new Set();
    for (const atom of clause.alts) for (const doc of (await index.atom(atom)).keys()) excluded.add(doc);
    ordinals = ordinals.filter((doc) => !excluded.has(doc));
  }
  return { ordinals, scores, where };
}

export function maskFields(mask) {
  return Object.entries(FIELD_BITS).filter(([, bit]) => mask & bit).map(([name]) => FIELD_LABELS[name]);
}

export function yearBounds(years) {
  let first = Infinity;
  let last = -Infinity;
  for (const year of years) {
    if (year > 0) {
      first = Math.min(first, year);
      last = Math.max(last, year);
    }
  }
  return first === Infinity ? [1850, 1950] : [first, last];
}

/** Facet filters and counts over the matched set; each facet's counts ignore its own selection, as catalogues do. */
export function applyFilters(ordinals, facets, filters) {
  const passes = (doc, skip) => {
    const year = facets.year[doc];
    if (skip !== "year" && (filters.yearFrom || filters.yearTo)) {
      if (!year || (filters.yearFrom && year < filters.yearFrom) || (filters.yearTo && year > filters.yearTo)) return false;
    }
    if (skip !== "type" && filters.types.size && !filters.types.has(facets.type[doc])) return false;
    if (skip !== "folder" && filters.folders.size && !filters.folders.has(facets.folder[doc])) return false;
    if (skip !== "status" && filters.statuses.size && !filters.statuses.has(facets.status[doc])) return false;
    return true;
  };
  const counts = { type: new Map(), folder: new Map(), status: new Map(), year: new Map() };
  const kept = [];
  for (const doc of ordinals) {
    if (passes(doc, null)) kept.push(doc);
    for (const name of ["type", "folder", "status", "year"]) {
      if (!passes(doc, name)) continue;
      const value = name === "year" ? facets.year[doc] : facets[name][doc];
      // -1 means "none" for a list position, and 0 means "undated" for a year; position 0 itself is a real value.
      if (value === -1 || (name === "year" && value === 0)) continue;
      counts[name].set(value, (counts[name].get(value) || 0) + 1);
    }
  }
  return { kept, counts };
}

export function sortResults(ordinals, sort, scores, facets) {
  const list = ordinals.slice();
  if (sort === "oldest") list.sort((a, b) => a - b);
  else if (sort === "newest") list.sort((a, b) => (facets.year[b] || 0) - (facets.year[a] || 0) || b - a);
  else if (sort === "id") list.sort((a, b) => facets.idrank[a] - facets.idrank[b]);
  else list.sort((a, b) => (scores.get(b) || 0) - (scores.get(a) || 0) || a - b);
  return list;
}

/** Marks every form of the query's words in plain text: matched by stem, then every piece escaped, so entities are never split. */
export function highlight(text, query, escapeHtml) {
  const wanted = new Set();
  const prefixes = [];
  for (const clause of query.clauses.filter((c) => !c.negate)) {
    for (const atom of clause.alts) {
      for (const word of atom.words || []) {
        if (STOPWORDS.has(word) || word.length < 2) continue;
        if (atom.kind === "prefix") prefixes.push(word);
        wanted.add(stem(word));
      }
    }
  }
  const raw = String(text ?? "");
  if (!wanted.size && !prefixes.length) return escapeHtml(raw);
  let out = "";
  let last = 0;
  for (const match of raw.matchAll(/[\p{L}\p{N}]+/gu)) {
    const folded = fold(match[0]);
    if (!wanted.has(stem(folded)) && !prefixes.some((p) => folded.startsWith(p))) continue;
    out += escapeHtml(raw.slice(last, match.index)) + `<mark>${escapeHtml(match[0])}</mark>`;
    last = match.index + match[0].length;
  }
  return out + escapeHtml(raw.slice(last));
}

function editDistance(a, b, limit) {
  if (Math.abs(a.length - b.length) > limit) return limit + 1;
  let previous = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const current = [i];
    let best = i;
    for (let j = 1; j <= b.length; j++) {
      current[j] = Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      best = Math.min(best, current[j]);
    }
    if (best > limit) return limit + 1;
    previous = current;
  }
  return previous[b.length];
}

/** For a search that found nothing, the closest indexed word to each word typed, from the same index file. */
export async function suggest(query, index) {
  const out = [];
  for (const clause of query.clauses.filter((c) => !c.negate)) {
    const atom = clause.alts[0];
    if (!atom || atom.kind !== "term" || atom.field === "id") continue;
    const word = stem(atom.words[0]);
    if (word.length < 4) continue;
    let best = null;
    for (const term of Object.keys(await index.shard(shardOf(word)))) {
      if (term.includes(" ") || term === word) continue;
      const distance = editDistance(word, term, 2);
      if (distance <= 2 && (!best || distance < best.distance)) best = { term, distance };
    }
    if (best) out.push({ typed: atom.words[0], suggestion: best.term });
  }
  return out;
}
