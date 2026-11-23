// Every collection: the folders of documents, and the account books, each with its first page, description and size.
import { catalog, crumbs, dataUrl, esc, fetchJSON, footer, header, plural, sources } from "./core.js";
import { folderEntry, volumeEntry } from "./cards.js";

async function render() {
  header("collections.html");
  footer();
  const [data, { dataset }, volumes] = await Promise.all([catalog(), sources(), dataUrl("volumes.json").then(fetchJSON).catch(() => [])]);
  const page = document.getElementById("page");
  // A reader arriving from an account book's page lands on the account books, the list they came from.
  const shown = location.hash === "#accounts" && volumes.length ? "accounts" : "folders";
  page.innerHTML = `${crumbs([["Home", "index.html"], ["Collections", null]])}
    <div class="page-head"><h1>Collections</h1><p>Documents are kept in the folders they were filed in; account books are read scan by scan.
      ${plural(data.folders.length, "folder")} of documents and ${plural(volumes.length, "account book")} so far.</p></div>
    ${volumes.length ? `<div class="segmented collection-switch" role="radiogroup" aria-label="Show">
      <label><input type="radio" name="kind" value="folders"${shown === "folders" ? " checked" : ""}><span>Folders of documents</span></label>
      <label><input type="radio" name="kind" value="accounts"${shown === "accounts" ? " checked" : ""}><span>Account books</span></label></div>` : ""}
    <div class="results" data-kind="folders" style="padding-bottom:72px"${shown === "folders" ? "" : " hidden"}>${data.folders.map((f) => folderEntry(f, dataset[0])).join("")}</div>
    <div class="results" data-kind="accounts" id="accounts" style="padding-bottom:72px"${shown === "accounts" ? "" : " hidden"}>
      ${volumes.map((v) => volumeEntry(v, dataset[0])).join("")}</div>`;
  page.querySelectorAll("input[name=kind]").forEach((radio) => radio.addEventListener("change", () => {
    page.querySelectorAll(".results[data-kind]").forEach((list) => { list.hidden = list.dataset.kind !== radio.value; });
    history.replaceState(null, "", radio.value === "accounts" ? "#accounts" : location.pathname);
  }));
}

render().catch((error) => { document.getElementById("page").innerHTML = `<div class="notice warn">Collections could not be loaded: ${esc(error.message)}</div>`; });
