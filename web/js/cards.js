// The repeated pieces of every listing: topic cards, collection entries and document entries.
import { badge, esc, facts, icon, plural, thumbHtml } from "./core.js";

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

// `title` and `description` may arrive already highlighted (escaped, with <mark>); otherwise they are escaped here.
export function documentEntry(s, base, { title = null, description = null, matched = "", collection = true } = {}) {
  const href = `document.html?id=${encodeURIComponent(s.id)}`;
  return `<article class="item">
    <a href="${href}" tabindex="-1" aria-hidden="true">${thumbHtml(s, base, { alt: "" })}</a>
    <div class="item-body"><a class="item-title" href="${href}">${title ?? esc(s.title)}</a>
      ${s.description || description ? `<p class="item-desc">${description ?? esc(s.description)}</p>` : ""}
      ${facts(s, { collection })}
      <div class="item-foot">${badge(s.status)}<span>${esc(s.id)}</span>${s.pages ? `<span>${plural(s.pages, "page")}</span>` : ""}
        ${matched ? `<span>Matched in ${esc(matched)}</span>` : ""}</div></div></article>`;
}
