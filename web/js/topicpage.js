// One topic: its picture and description, then its documents with the full search tools to narrow them.
import { crumbs, dataUrl, esc, fetchJSON, footer, header, param, plural, sources, thumbHtml } from "./core.js";
import { mountResults } from "./results.js";

const TOPIC_ID = /^[a-z0-9][a-z0-9-]{0,79}$/;

async function render() {
  header("topics.html");
  footer();
  const page = document.getElementById("page");
  const id = param("id") || "";
  const topics = TOPIC_ID.test(id) ? await fetchJSON(await dataUrl("topics.json")) : [];
  const topic = topics.find((t) => t.id === id);
  if (!topic) {
    page.innerHTML = `${crumbs([["Home", "index.html"], ["Topics", "topics.html"], ["Not found", null]])}
      <div class="empty-state"><h1>Topic not found</h1><p><a href="topics.html">See all topics</a></p></div>`;
    return;
  }
  document.title = `${topic.title} · The Edison Papers`;
  const [{ dataset }, { ordinals }] = await Promise.all([sources(), fetchJSON(await dataUrl(`topics/${topic.id}.json`))]);
  page.innerHTML = `${crumbs([["Home", "index.html"], ["Topics", "topics.html"], [topic.title, null]])}
    <div class="topic-head">${thumbHtml(topic, dataset[0], { alt: "" })}
      <div class="page-head" style="padding:0"><p class="eyebrow">Topic</p><h1>${esc(topic.title)}</h1><p>${esc(topic.description || "")}</p>
      <p class="muted" style="font-size:.92rem">${plural(topic.count, "document")}. Search below to narrow them down.</p></div></div>
    <div id="search-root"></div>`;
  await mountResults(document.getElementById("search-root"), { within: ordinals, placeholder: `Search within ${topic.title}` });
}

render().catch((error) => { document.getElementById("page").innerHTML = `<div class="notice warn">This topic could not be loaded: ${esc(error.message)}</div>`; });
