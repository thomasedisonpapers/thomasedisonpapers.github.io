// Every topic as a card with its picture, description and size.
import { catalog, crumbs, esc, footer, header, sources } from "./core.js";
import { topicCard } from "./cards.js";

async function render() {
  header("topics.html");
  footer();
  const [data, { dataset }] = await Promise.all([catalog(), sources()]);
  document.getElementById("page").innerHTML = `${crumbs([["Home", "index.html"], ["Topics", null]])}
    <div class="page-head"><h1>Topics</h1><p>Each topic gathers the documents that mention it, across every collection. Open one to read them,
      or narrow it with a search.</p></div>
    <div class="topics" style="padding-bottom:72px">${data.topics.map((t) => topicCard(t, dataset[0])).join("")}</div>`;
}

render().catch((error) => { document.getElementById("page").innerHTML = `<div class="notice warn">Topics could not be loaded: ${esc(error.message)}</div>`; });
