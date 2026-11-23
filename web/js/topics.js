// The featured topics as cards, then every topic from A to Z, with a box to find one as the list grows.
import { crumbs, dataUrl, esc, fetchJSON, footer, header, plural, sources } from "./core.js";
import { topicCard } from "./cards.js";

async function render() {
  header("topics.html");
  footer();
  const [topics, { dataset }] = await Promise.all([dataUrl("topics.json").then(fetchJSON), sources()]);
  const featured = topics.filter((t) => t.featured);
  // A leading "The" is not where a reader looks for a title, as in a library catalogue.
  const sortTitle = (t) => t.title.replace(/^the\s+/i, "");
  const all = topics.slice().sort((a, b) => sortTitle(a).localeCompare(sortTitle(b), "en", { sensitivity: "base" }));
  const page = document.getElementById("page");
  page.innerHTML = `${crumbs([["Home", "index.html"], ["Topics", null]])}
    <div class="page-head"><h1>Topics</h1><p>Each topic gathers the documents the editors filed under a subject, across every collection.
      New topics appear as the collection grows.</p></div>
    <h2 class="section-title">Featured topics</h2>
    <div class="topics">${featured.map((t) => topicCard(t, dataset[0])).join("")}</div>
    <section class="topic-index" aria-labelledby="all-topics">
      <div class="topic-index-head"><h2 id="all-topics" class="section-title">All ${plural(all.length, "topic")}, A to Z</h2>
        <label class="field topic-find"><span class="sr-only">Find a topic</span>
          <input id="topic-find" type="search" autocomplete="off" placeholder="Find a topic"></label></div>
      <ul class="topic-list">${all.map((t) => `<li data-title="${esc(t.title.toLowerCase())}"><a href="topic.html?id=${encodeURIComponent(t.id)}">
        <span>${esc(t.title)}</span><span class="count">${t.count.toLocaleString()}</span></a></li>`).join("")}</ul>
      <p class="muted topic-none" hidden>No topic matches. Try a search of the whole archive instead.</p>
    </section>`;
  const items = [...page.querySelectorAll(".topic-list li")];
  const none = page.querySelector(".topic-none");
  page.querySelector("#topic-find").addEventListener("input", (event) => {
    const words = event.target.value.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const item of items) {
      item.hidden = !words.every((word) => item.dataset.title.includes(word));
      shown += item.hidden ? 0 : 1;
    }
    none.hidden = shown > 0;
  });
}

render().catch((error) => { document.getElementById("page").innerHTML = `<div class="notice warn">Topics could not be loaded: ${esc(error.message)}</div>`; });
