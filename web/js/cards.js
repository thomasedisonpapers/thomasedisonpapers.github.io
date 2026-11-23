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

/** Where an account-book scan is read: the book's reader, opened at that scan. */
export function accountHref(volume, scan) {
  if (!VOLUME_ID.test(String(volume || ""))) return "collections.html#accounts";
  const n = Number(scan);
  return `account.html?id=${encodeURIComponent(volume)}${Number.isInteger(n) && n > 0 ? `&scan=${n}` : ""}`;
}

/** A listing's link for a summary: the account reader for an account-book scan, the document reader for anything else. */
export function entryHref(s) {
  return s.kind === "account" ? accountHref(s.volume, s.scan) : `document.html?id=${encodeURIComponent(s.id)}`;
}

export function volumeEntry(v, base) {
  const href = accountHref(v.id);
  return `<article class="item">
    <a href="${href}" tabindex="-1" aria-hidden="true">${thumbHtml(v, base, { alt: "" })}</a>
    <div class="item-body"><a class="item-title" href="${href}">${esc(v.title)}</a>
      ${v.subjects && v.subjects.length ? `<p class="item-desc">${esc(v.subjects.join(" · "))}</p>` : ""}
      <ul class="facts">${v.date ? `<li>${icon("calendar")}${esc(v.date)}</li>` : ""}<li>${icon("book-open")}Account book ${esc(v.id)}</li>
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
      <div class="item-foot">${badge(s.status)}${s.kind === "account"
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
