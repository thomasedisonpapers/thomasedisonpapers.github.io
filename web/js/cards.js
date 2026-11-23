// The repeated pieces of every listing: topic cards, collection entries and document entries.
import { STATUS, badge, esc, facts, icon, plural, thumbHtml } from "./core.js";

export function topicCard(t, base) {
  return `<a class="topic" href="topic.html?id=${encodeURIComponent(t.id)}">
    ${thumbHtml(t, base, { alt: "" })}
    <h3>${esc(t.title)}</h3><p>${esc(t.description || "")}</p><span class="count">${plural(t.count, "document")}</span></a>`;
}

export function folderEntry(f, base) {
  const href = `folder.html?id=${encodeURIComponent(f.id)}`;
  return `<article class="item">
    <a href="${href}" tabindex="-1" aria-hidden="true">${thumbHtml(f, base, { alt: "" })}</a>
    <div class="item-body"><a class="item-title" href="${href}">${esc(f.title)}</a>
      <p class="item-desc">${esc(f.description)}</p>
      <ul class="facts"><li>${icon("folder")}Collection ${esc(f.id)}</li><li>${icon("files")}${plural(f.count, "document")}</li>
        <li>${icon("badge-check")}${f.approved ? `${f.approved.toLocaleString()} verified` : "None verified yet"}</li></ul></div></article>`;
}

const VOLUME_ID = /^[A-Za-z0-9_-]+$/;
// Each kind of bound volume: its reader page, the collections tab that lists it, and what one is called.
export const VOLUME_KINDS = {
  account: { page: "account.html", tab: "accounts", name: "Account book", plural: "Account books" },
  notebook: { page: "notebook.html", tab: "notebooks", name: "Notebook", plural: "Notebooks" },
};
const KIND_OF_COLLECTION = { accounts: "account", notebooks: "notebook" };

export function volumeKind(collectionOrKind) {
  return VOLUME_KINDS[collectionOrKind] || VOLUME_KINDS[KIND_OF_COLLECTION[collectionOrKind]] || VOLUME_KINDS.account;
}

/** Where a volume's scan is read: its reader, opened at that scan. */
export function volumeHref(volume, scan, kind = "account") {
  const { page, tab } = volumeKind(kind);
  if (!VOLUME_ID.test(String(volume || ""))) return `collections.html#${tab}`;
  const n = Number(scan);
  return `${page}?id=${encodeURIComponent(volume)}${Number.isInteger(n) && n > 0 ? `&scan=${n}` : ""}`;
}

export function accountHref(volume, scan) {
  return volumeHref(volume, scan, "account");
}

/** A listing's link for a summary: a volume's reader for an account-book or notebook scan, the document reader for anything else. */
export function entryHref(s) {
  return VOLUME_KINDS[s.kind] ? volumeHref(s.volume, s.scan, s.kind) : `document.html?id=${encodeURIComponent(s.id)}`;
}

export function volumeEntry(v, base) {
  const kind = volumeKind(v.collection);
  const href = volumeHref(v.id, null, v.collection);
  const about = v.subjects && v.subjects.length ? v.subjects.join(" · ") : (v.description || "");
  return `<article class="item">
    <a href="${href}" tabindex="-1" aria-hidden="true">${thumbHtml(v, base, { alt: "" })}</a>
    <div class="item-body"><a class="item-title" href="${href}">${esc(v.title)}</a>
      ${about ? `<p class="item-desc">${esc(about)}</p>` : ""}
      <ul class="facts">${v.date ? `<li>${icon("calendar")}${esc(v.date)}</li>` : ""}<li>${icon("book-open")}${esc(kind.name)} ${esc(v.id)}</li>
        <li>${icon("files")}${plural(v.count, "scan")}</li>
        <li>${icon("badge-check")}${v.approved ? `${v.approved.toLocaleString()} verified` : "None verified yet"}</li></ul></div></article>`;
}

// `title` and `description` may arrive already highlighted (escaped, with <mark>); otherwise they are escaped here.
export function documentEntry(s, base, { title = null, description = null, matched = "", collection = true, byMeaning = false } = {}) {
  const href = entryHref(s);
  return `<article class="item">
    <a href="${href}" tabindex="-1" aria-hidden="true">${thumbHtml(s, base, { alt: "" })}</a>
    <div class="item-body"><a class="item-title" href="${href}">${title ?? esc(s.title)}</a>
      ${s.description || description ? `<p class="item-desc">${description ?? esc(s.description)}</p>` : ""}
      ${facts(s, { collection })}
      <div class="item-foot">${badge(s.status)}${VOLUME_KINDS[s.kind]
        // The opening's own ID counts scans from zero, so a reader sees the book and its scan number instead.
        ? `<span>${esc(s.volume)}, scan ${esc(s.scan)}</span>` : `<span>${esc(s.id)}</span>${s.pages ? `<span>${plural(s.pages, "page")}</span>` : ""}`}
        ${matched ? `<span>Matched in ${esc(matched)}</span>` : ""}
        ${byMeaning ? `<span class="by-meaning" title="None of your words appear here, but it is about the same thing">${icon("sparkles")}Matched by meaning</span>` : ""}</div></div></article>`;
}

/** A row of documents related by meaning, each a thumbnail with its title, date and status, linking to the document. */
export function relatedShelf(items, base) {
  return items.map((s) => `<a class="shelf-item" href="${entryHref(s)}">
    ${thumbHtml(s, base, { alt: "" })}<strong>${esc(s.title)}</strong>
    <span>${esc(s.date || "Undated")} · ${esc((STATUS[s.status] || STATUS.none).label)}</span></a>`).join("");
}
