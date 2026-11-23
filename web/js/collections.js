// Every collection: the folders of documents, the account books and the notebooks, each with its first page, description and size.
import { catalog, crumbs, dataUrl, esc, fetchJSON, footer, header, plural, sources } from "./core.js";
import { VOLUME_KINDS, folderEntry, volumeEntry, volumeKind } from "./cards.js";

async function render() {
  header("collections.html");
  footer();
  const [data, { dataset }, volumes] = await Promise.all([catalog(), sources(), dataUrl("volumes.json").then(fetchJSON).catch(() => [])]);
  const page = document.getElementById("page");
  const groups = Object.values(VOLUME_KINDS).map((kind) => ({ ...kind, items: volumes.filter((v) => volumeKind(v.collection).tab === kind.tab) }))
    .filter((group) => group.items.length);
  // A reader arriving from a volume's page lands on the list they came from.
  const shown = groups.some((g) => `#${g.tab}` === location.hash) ? location.hash.slice(1) : "folders";
  const tabs = [{ tab: "folders", plural: "Folders of documents" }, ...groups];
  const counts = [plural(data.folders.length, "folder") + " of documents", ...groups.map((g) => plural(g.items.length, g.name.toLowerCase()))];
  page.innerHTML = `${crumbs([["Home", "index.html"], ["Collections", null]])}
    <div class="page-head"><h1>Collections</h1><p>Documents are kept in the folders they were filed in; account books and notebooks are read scan by scan.
      ${esc(counts.slice(0, -1).join(", "))}${counts.length > 1 ? " and " : ""}${esc(counts[counts.length - 1])} so far.</p></div>
    ${groups.length ? `<div class="segmented collection-switch" role="radiogroup" aria-label="Show">
      ${tabs.map((t) => `<label><input type="radio" name="kind" value="${t.tab}"${shown === t.tab ? " checked" : ""}><span>${esc(t.plural)}</span></label>`).join("")}</div>` : ""}
    <label class="field topic-find volume-find"${shown === "folders" ? " hidden" : ""}><span class="sr-only">Find a volume by title, ID or subject</span>
      <input id="volume-find" type="search" autocomplete="off" placeholder="Find by title, ID or subject"></label>
    <div class="results" data-kind="folders" style="padding-bottom:72px"${shown === "folders" ? "" : " hidden"}>${data.folders.map((f) => folderEntry(f, dataset[0])).join("")}</div>
    ${groups.map((g) => `<div class="results" data-kind="${g.tab}" id="${g.tab}" style="padding-bottom:72px"${shown === g.tab ? "" : " hidden"}>
      ${g.items.map((v) => volumeEntry(v, dataset[0])).join("")}</div>`).join("")}`;
  const find = page.querySelector("#volume-find");
  const filter = () => {
    const words = find.value.toLowerCase().split(/\s+/).filter(Boolean);
    page.querySelectorAll(".results[data-kind]:not([data-kind=folders]) .item").forEach((item) => {
      item.hidden = !words.every((word) => item.textContent.toLowerCase().includes(word));
    });
  };
  find.addEventListener("input", filter);
  page.querySelectorAll("input[name=kind]").forEach((radio) => radio.addEventListener("change", () => {
    page.querySelectorAll(".results[data-kind]").forEach((list) => { list.hidden = list.dataset.kind !== radio.value; });
    page.querySelector(".volume-find").hidden = radio.value === "folders";
    history.replaceState(null, "", radio.value === "folders" ? location.pathname : `#${radio.value}`);
  }));
}

render().catch((error) => { document.getElementById("page").innerHTML = `<div class="notice warn">Collections could not be loaded: ${esc(error.message)}</div>`; });
