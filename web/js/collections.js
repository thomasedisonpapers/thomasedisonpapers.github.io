// Every collection (archival folder) with its first page, description and size.
import { catalog, crumbs, esc, footer, header, plural, sources } from "./core.js";
import { folderEntry } from "./cards.js";

async function render() {
  header("collections.html");
  footer();
  const [data, { dataset }] = await Promise.all([catalog(), sources()]);
  document.getElementById("page").innerHTML = `${crumbs([["Home", "index.html"], ["Collections", null]])}
    <div class="page-head"><h1>Collections</h1><p>Documents are kept in the folders they were filed in. ${plural(data.folders.length, "collection")} so far.</p></div>
    <div class="results" style="padding-bottom:72px">${data.folders.map((f) => folderEntry(f, dataset[0])).join("")}</div>`;
}

render().catch((error) => { document.getElementById("page").innerHTML = `<div class="notice warn">Collections could not be loaded: ${esc(error.message)}</div>`; });
