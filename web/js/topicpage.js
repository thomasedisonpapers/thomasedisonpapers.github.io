// One topic: its picture and description, then its documents with the full search tools to narrow them.
import { catalog, crumbs, esc, footer, header, param, plural, sources, thumbHtml } from "./core.js";
import { mountResults } from "./results.js";

// The topic's words become one OR clause, the same rule the builder used to count it.
function topicQuery(topic) {
  const fields = topic.fields && topic.fields.length ? topic.fields.map((f) => (f === "names" ? "people" : f)) : [""];
  const parts = [];
  for (const word of topic.any || []) for (const field of fields) parts.push(`${field ? `${field}:` : ""}"${word.replace(/"/g, "")}"`);
  return parts.join(" OR ");
}

async function render() {
  header("topics.html");
  footer();
  const page = document.getElementById("page");
  const data = await catalog();
  const topic = data.topics.find((t) => t.id === param("id"));
  if (!topic) {
    page.innerHTML = `${crumbs([["Home", "index.html"], ["Topics", "topics.html"], ["Not found", null]])}
      <div class="empty-state"><h1>Topic not found</h1><p><a href="topics.html">See all topics</a></p></div>`;
    return;
  }
  document.title = `${topic.title} · The Edison Papers`;
  const { dataset } = await sources();
  page.innerHTML = `${crumbs([["Home", "index.html"], ["Topics", "topics.html"], [topic.title, null]])}
    <div class="topic-head">${thumbHtml(topic, dataset[0], { alt: "" })}
      <div class="page-head" style="padding:0"><p class="eyebrow">Topic</p><h1>${esc(topic.title)}</h1><p>${esc(topic.description || "")}</p>
      <p class="muted" style="font-size:.92rem">${plural(topic.count, "document")}. Search below to narrow them down.</p></div></div>
    <div id="search-root"></div>`;
  await mountResults(document.getElementById("search-root"), { base: topicQuery(topic), placeholder: `Search within ${topic.title}` });
}

render().catch((error) => { document.getElementById("page").innerHTML = `<div class="notice warn">This topic could not be loaded: ${esc(error.message)}</div>`; });
