// The search page: the shared results view with no fixed query.
import { crumbs, esc, footer, header } from "./core.js";
import { mountResults } from "./results.js";

header("search.html");
footer();
const page = document.getElementById("page");
page.innerHTML = `${crumbs([["Home", "index.html"], ["Search", null]])}
  <div class="page-head"><h1>Search</h1><p>Search titles, people, places and the full text of every transcription, then refine by date,
    document type, collection or how far a transcription has been checked.</p></div><div id="search-root"></div>`;
mountResults(document.getElementById("search-root"), { placeholder: "Search names, places, inventions or a phrase" })
  .catch((error) => { document.getElementById("search-root").innerHTML = `<div class="notice warn">Search could not start: ${esc(error.message)}</div>`; });
