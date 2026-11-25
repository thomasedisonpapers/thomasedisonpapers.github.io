# Edison Papers public archive site — system design

## Step 0 — Working folder and environment

This repository is the home of the public site, kept separate from the review platform (edison-pipeline). It holds:
- **`CLAUDE.md`:** the working rules (comments, tests, git, the public boundary, gentleness with edisondigital).
- **`docs/PLAN.md`:** this plan.
- **Environment:**
  - `requirements.txt` and `package.json`;
  - the test gate `scripts/check.mjs`, also run by the pre-commit hook in `.githooks/` and by CI.

## Context

The review platform's side-by-side viewer is far easier to read than edisondigital.rutgers.edu, and the
project will end. The client wants a **separate, maintainable, public, read-only site**: search the
collection, browse each document as a thumbnail with its title and description, read the scan and
transcription side by side (no editing, no comments), and download a PDF, the JSON and the images.

Decisions made with the user:
- **Hosting:** static only, no domain. GitHub Pages for the app; a Hugging Face (HF) dataset repo for the files;
  Internet Archive for a preservation copy.
- **Coverage:** mirror all of edisondigital, but our local copy wins wherever we have one (per field and per
  file).
- **Status:** shown with a label, never hidden. Admin-approved documents carry a **verified check-mark badge**;
  local drafts carry "**Under review by admin — transcription may not be accurate**".
- **Notebooks and Accounts:** a later phase.
- **Live updates.** The site follows the review platform as things happen:
  - when an admin **approves** a document, the site shows it as verified;
  - when a folder is **exported, edited and re-exported**, the site serves the latest export;
  - target: live within about 10 minutes, with no manual step.

Facts checked on 20 November that shape the design:
- **edisondigital can't be read from a browser:** its `/api/*` responses send no `Access-Control-Allow-Origin`.
- **edisondigital is unreliable:** today every URL redirects to `sorry.rutgers.edu`; the logs show ReadTimeouts,
  504s on search and bot challenges.
- **So the live site never calls edisondigital.** Everything, including scans, is mirrored and served from
  our hosts. edisondigital is a last-resort image source only.
- **Hosts:**
  - HF file URLs send CORS headers (tested). Internet Archive file servers don't (tested).
  - GitHub Pages: 1 GB cap, about 100 GB a month, no Git LFS, files cached for about 10 minutes.

## Architecture

```
 edison-pipeline (host)          GitHub Actions — edison-archive repo           Browser
 publish ──────────────▶ HF dataset ◀── harvest: edisondigital metadata + image derivatives
   (allowlisted, DB-built)  local/ edisondigital/ site/{rev}/   (resolve/{commit-sha}/… URLs)
                                  ▲          │
                                  └── build ─┴─▶ GitHub Pages: app, own thumbnails, db/{hash}/ search
                                               └─▶ Internet Archive: delayed release snapshot
```

The `edison-archive` repo is standalone (harvester, builder, front end, workflows), so the site outlives the
pipeline and its host. Only the publisher lives in edison-pipeline.

## Module 1 — Publisher (edison-pipeline: `public_export.py`, CLI `publish`)

**Built from the database through an allowlist, never by copying `records/`.** Those files carry
`pipeline_info.cost_estimate`, model, provider, `review.reviewer` and old internal notes.

**Output** per document, at `local/{DocID}/` (paths by DocID, so renaming a folder never breaks a URL):
- **`record.json`:**
  - **Metadata:** title, date, type, author, recipient, places, people, subjects, folder id and title,
    description.
  - **Transcription:** the effective text, laid out by `transcript_layout.py`.
  - **`status`:** `approved` when `review_status='accepted'`, else `draft`; plus `status_date`.
  - **`relation`:** `new` or `supplements-edisondigital`.
  - **Pages:** each page's `thumb`, `view` and `full` paths, width and height, and sha256.
- **Images:** `thumb/` and `view/` from `imaging.cached_render`; `full/` reoriented with `imaging.orient`, as
  `folder_exports.py:185-197` does.
- **`{DocID}.pdf`:** one document via `folder_latex.document_body`. Every page is stamped with the status
  (verified, or under review), the "as of" date and the canonical URL.

**Field rules:**
- **Description:** first one present of the abstract (from `raw_metadata_json`), the editors' notes (accepted
  documents only) or the first sentence of the transcription.
- **Effective values:** `db.effective` / `effective_list` and the `COALESCE` queries (`db.py:868-890`,
  `2369-2397`).

**Vulnerabilities:**
- **`tests/test_public_export_boundary.py`,** modelled on `test_student_admin_boundary.py`. It fails on any
  field outside the allowlist: reviewer or student identities, verdicts by role, cost, claims, comments,
  `pipeline_info`, or AI fields when a reviewed value exists.
- **Folders are published only from an explicit list** in config, so the test folders in the bucket (ACTTEST,
  LOCALTEST, SPLITUI, ZLONGTEST, ZNAMETEST) never go out.
- **The HF token** is fine-grained to one repo and lives only on the host.

**Changes to the pipeline:**
- **Published DocIDs are frozen.** `reorder.py` refuses to renumber a published document. Any past rename is
  kept in `aliases.json`, and the site shows "This ID was reassigned on {date}".
- **Allocating a new DocID** (`db.next_document_ids`, `db.py:1775`) also checks the harvested edisondigital ID
  list, so a new document can't take an ID that's already on edisondigital.

**Triggers, so the site follows the platform live:**
- **What enqueues a publish:**
  - **Admin approval** of a document (the admin-verdict path in `collab.py`) enqueues its folder.
  - **Admin un-approval** or a send-back enqueues its folder the same way, so the badge comes off too.
  - **A finished folder export** (the `folder_exports.py` job) enqueues that folder, with the export's
    `source_version`. A re-export after edits carries a new `source_version`, and that version is what goes
    live.
- **Durable outbox:** a new `publish_queue` table (`folder_id, workflow, reason, source_version, requested_at,
  attempts, next_attempt_at, last_error`), added to the `CREATE TABLE IF NOT EXISTS` block and `db._migrate()`.
  Enqueueing is one insert inside the same transaction as the approval or export, so a request never waits
  on Hugging Face and a crash can't lose the event.
- **Publish worker:** a background thread in the review server, in the same pattern as
  `folder_pdf.start_timer`.
  - **Coalescing:** requests for the same folder within 60 s are merged, so approving twenty documents in a row
    is one publish.
  - **Retries:** on failure, backoff of 1, 5, then 30 minutes, with the error recorded. The admin sees a quiet
    "site update pending / failed" note on the folder card, using an admin-only field (checked against
    `scoping.strip_admin_fields`).
- **Reconciling sweep, every 30 minutes:**
  - compares each published folder's content hash with the database, and publishes any difference;
  - so draft edits, missed events and anything published while HF was down all catch up on their own.

**Versions:**
- **Latest export wins.** For a folder that has an export, the publisher builds from the database state that
  export was taken from, and records `export_version` and `exported_at` in each record and in the manifest.
- **Out-of-order publishes can't roll back.** A publish whose `source_version` is older than the one already
  live is skipped.

**Redundancy:**
- **Upload:** `huggingface_hub` uploads only files whose sha256 changed; the hash includes the status, so a
  flip from approved to draft always republishes. It's resumable.
- **Manifest last:** `local/manifest.json` (every DocID, status, hash, export version, and a `withdrawn` list)
  is written last in one commit, so a half-finished upload is never read as complete.
- **Rebuild:** a `repository_dispatch`, with a token scoped to that one repo and a payload naming the folders
  that changed, starts the rebuild.
- **Slowdowns:** the first upload is about 9 GB and runs once, off-hours. Event publishes send one folder's
  changed files, usually seconds. The worker and the sweep need approval to run on the host at deploy time.

## Module 2 — Harvester (edison-archive, GitHub Actions)

**2a. Metadata, weekly:**
- **Items:** `/api/items` and `/api/media` in pages of 100, using the endpoints and property ids `discover.py`
  uses. Bulk media pages give each page's image URLs in order.
- **Descriptions:** HTML is stripped to plain text, porting `notebooks._plain`.
- **Incremental:** sorted by modified date, newest first. A **full sweep of every ID runs monthly** to catch
  deleted items and media changes.

**2b. Image derivatives, continuous and slow:**
- **What:** `/files/medium` (thumbnails) and `/files/large` (viewer) for every item, mirrored to
  `edisondigital/{DocID}/`.
- **Order:** thumbnails first, then the viewer size. Originals stay linked, not copied.
- **Volume:** measured in Phase 0 from the `/api/media` total count. Expect weeks at a polite rate.

**Politeness and outage handling:**
- **Load:** one or two requests at a time, at least a 1 s delay, an honest User-Agent with a contact address.
- **Retries:** jittered backoff on 429, 5xx and timeouts, honouring `Retry-After`. A 429 pauses the run.
- **Outages:** a `sorry.rutgers.edu` or `/bot-challenge` redirect stops the run cleanly (port of
  `notebooks.refuse_outage_page`).
- **Bad responses:** anything that isn't JSON is rejected.

**Redundancy and integrity:**
- **State lives on HF, not the runner:** the checkpoint (page cursor, image progress) survives the 6-hour job
  limit and outages.
- **Keep-alive:** each run commits a state file, so GitHub doesn't switch the schedule off after 60 days
  without activity.
- **New snapshot, then flip the pointer:** only after validation does `edisondigital/current.json` move.
- **Validation:**
  - the count must be within 0.5% of `Omeka-S-Total-Results`;
  - it must not drop more than 2%;
  - the share of items with each field and with media must stay stable;
  - every record needs an identifier.
- **On failure** the last good snapshot stays live and a GitHub issue is opened.

## Module 3 — Builder (edison-archive, GitHub Actions; after a publish or a harvest)

**Fast path for live updates:**
- **Triggered by the publish dispatch.**
- **Latest wins:** the workflow uses `concurrency: {group: build, cancel-in-progress: false}`, so GitHub keeps
  only the newest waiting run. A burst of approvals becomes one build that includes all of them, and a run
  already deploying is never cut off.
- **Only what changed:**
  - only the folders named in the payload are rebuilt (their folder JSON and Pages thumbnails);
  - the search database is rebuilt from the cached merged records, with the harvest snapshot cached between
    runs, so nothing is re-downloaded;
  - target: build plus deploy under about 5 minutes.
- **What visitors see:** records are read through `current.json`, which is fetched with `no-store`, so a page
  load after the deploy gets the new version. An open document page re-checks `current.json` every 5 minutes;
  if its record changed, it shows "A newer version of this document is available — Refresh".

**Merge,** per DocID and per field. Fields: reviewed local value, then edisondigital editorial value, then local
AI draft. Files: local, then edisondigital mirror, then the edisondigital original URL.

**Statuses and labels:**
- **`approved`:** the verified badge.
- **`draft`:** the "Under review by admin…" banner.
- **`edition`:** an edisondigital Scripto transcription, labelled "Transcription from the Edison Papers digital
  edition".
- **None:** no transcription yet.

**Integrity gates.** The build fails and the live site stays unchanged when:
- a DocID matches but its folder or title disagree, without `relation`;
- the record count falls;
- the approved count drops unexpectedly;
- a page points at a missing file;
- or a `withdrawn.json` entry is still present in the output.

**Outputs:**
- **`site/{rev}/folders/{FolderID}.json` on HF:** read through `resolve/{commit-sha}/…`, so URLs are
  immutable, cacheable and atomic. Folders over 1 MB are split.
- **`db/{hash}/` search database on Pages:**
  - **Format:** SQLite with one FTS5 index (`unicode61 remove_diacritics 2`, `prefix='2 3'`), with columns
    and bm25 weights (title 5, people 3, description 2, text 1).
  - **Layout:** 32–64 KB pages, rowids in date order so sorting by date is cheap, chunked files.
  - **Facets:** counts for broad queries (more than about 5,000 hits) are precomputed.
  - **Versions:** the previous version is kept for one cycle. `current.json`, fetched with `no-store`, names the
    live hash, so a browser never mixes pages from two builds.
- **Our own thumbnails on Pages:** about 2,100 pages at 160 px WebP, under 20 MB. They spare the HF rate limit.
- **Also:** `sitemap.xml`, a no-JavaScript HTML page per folder, and "Catalogue updated {date}".

**Takedowns:**
- **Pages:** deployed with `actions/deploy-pages`, so no git history is kept.
- **HF:** a takedown squashes the repo history (`super_squash_history`) and adds the item to `withdrawn.json`.
- **Internet Archive:** each release is deposited only after a 30-day delay.

## Module 4 — Front end (edison-archive/web, vanilla JS)

**Lifted from the review UI:**
- **From `style.css:1-162`:** tokens, dark mode and reduced motion.
- **From `app.js`:** `transcriptionBlocks`, `isBodyParagraph`, `renderHighlighted`, `markUnclear`,
  `zoomAt`/`applyTransform` and `orientationTransform`, with the review state stripped out.
- **Hostile-input tests:** `renderHighlighted` builds HTML strings, so it gets tests with hostile edisondigital
  text before reuse.
- **No editing, saving or comment code** comes across.

**Routes** use query strings, so Pages needs no rewrites. All state is in the URL.

**Look and navigation.** Modern and self-explanatory, with our own design. It must look better than
edison.rutgers.edu (tae.rutgers.edu doesn't resolve), not copy it. That site is used only as a guide to **which
kind of image represents each topic**:

| Topic | Kind of image |
|---|---|
| Life and biography | a portrait or group photograph |
| Inventions | the device or the laboratory |
| Correspondence | a handwritten letter or letterhead |
| Patents | a patent drawing |
| Motion pictures | a film poster or catalogue page |
| Places | a map |

Cover images are picked in that spirit from our own documents and edisondigital's scans, and set in
`topics.json`.

Design direction, all our own:
- **Header on every page:** the site name, four plain-word links (**Explore topics · Collections · Search ·
  About**) and a search box that's always there. Breadcrumbs (Home › Topic › Collection › Document) on every
  inner page.
- **No icon-only buttons.** Every page opens with one line saying what you can do there, and empty states
  suggest searches.
- **Explained badge:** the verified badge has a "What does this mean?" popover.
- **Type and colour:**
  - **Type:** serif display headings (e.g. Source Serif 4), with the review UI's sans for body text. Fonts are
    self-hosted woff2, so there's no Google Fonts call.
  - **Colour:** a warm paper-toned neutral palette with one accent. The review UI's tokens are re-valued for
    this, with dark mode.
- **Feel:** generous spacing, skeleton loaders, subtle motion that respects reduced motion.

**`index.html` — landing page** (unique, and topic-first):
1. **Hero:** a full-width detail of a real scan from our verified documents (for example a "From the Laboratory
   of Thomas A. Edison" letterhead), one sentence on what the archive is, a large search box, and example
   searches as chips ("phonograph", "letters to Mina", "1880–1885", "patents").
2. **"How to use this archive":** three short steps, each with a small picture: Search, Browse a topic, Read
   the scan and transcription side by side.
3. **Explore topics:** a grid of tiles. Each tile has a thumbnail cropped from a representative document, the
   topic name, one line of description and a document count.
   - **Defined in `web/data/topics.json`** (title, description, query, cover DocID), which editors can change
     without code.
   - **The builder** fills in the counts and picks a cover when none is set.
   - **Starting set,** built from the data: inventions (phonograph, electric light, motion pictures,
     batteries…), the families of correspondence, people, places, companies and document types.
4. **Browse by decade:** an 1860s–1930s strip with a small histogram of counts. Each decade opens a filtered
   search.
5. **Recently verified:** a row of the latest admin-approved documents with the badge, fed by the live publish
   path.
6. **Collections:** each folder as a row with its first-page thumbnail, title and description.
7. **Footer:** about, credits and funders as text, the rights statement, data downloads, and "Catalogue updated
   {date}".

**`topic.html?id=`:** a banner thumbnail, the description, then the topic's documents as search results with the
same filters.

**`search.html` — search and browse** (PubMed and archival catalogues as the model):
- **Search box with a mode switch:** All words (default) / Any word (broad: prefix-expands each term) / Exact
  phrase.
- **Field tags:** optional `title:` `author:` `recipient:` `text:` `place:` `type:` `id:` `folder:`
  `year:1878..1880`, plus quotes, `prefix*`, `OR` and `-exclude`.
- **Safe query translation:** the query is turned into FTS5 by our parser, with every term quoted. Syntax
  errors become a "Search details" message, never an exception.
- **Advanced:** a builder of rows with a live query preview; recent-search history kept in `localStorage`.
- **Filters:** a year histogram and range, type, folder, author, recipient, status (verified / under review /
  edition), has a transcription.
- **Sort and paging:** best match, date (oldest or newest), DocID. 25 results a page.
- **Result row:** a thumbnail (badge when verified, "Under review" chip when a draft), then the **title**, then
  the **description** (clamped to three lines), then DocID · date · author → recipient · type · folder, then a
  highlighted snippet.

**`folder.html?id=`:** the folder's title and description, downloads, and its documents as the same rows.

**`document.html?id=`:**
- **Header:** title, verified badge or draft banner, and "Updated {date}" (the export or status date).
- **Scan, left:** zoom, pan and rotate (view only), touch pinch and a page strip.
- **Transcription, right:** page breaks synced to the scan.
- **Also:** collapsible metadata; Download (PDF, JSON, images); browser print with a print stylesheet when
  there's no pre-built PDF; Cite this; the edisondigital link; previous and next.
- **Mobile:** below 900 px, Scan / Transcription tabs.
- **Old IDs:** an ID in `aliases.json` shows the reassignment notice; a withdrawn ID shows "withdrawn".

**Accessibility:**
- **Alt text:** "Page 2 of 5, letter from X to Y".
- **Keyboard viewer:** + / − / arrow keys / R, with focusable controls.
- **Labels in words:** the badge and draft label are text, not colour or an icon alone.
- **Forms and announcements:** facets in fieldsets; the result count in an `aria-live` region, with focus moved
  to it; AA contrast in both themes.

## Module 5 — Resilience and security (front end)

- **Ordered sources** in `sources.json`: Pages (own thumbnails, search), HF (records, images, PDFs), the
  edisondigital mirror, the edisondigital original URL, then a placeholder.
- **Every fetch:**
  - an `AbortController` timeout of 15 s (as in `folder_exports.js:17-21`);
  - one jittered retry on network errors or 5xx;
  - a 404 goes straight to the next source;
  - a 429 pauses that host for the session, with a quiet banner.
- **Images:** the notebooks pattern (`notebooks.js:975-1017`): fallback URLs, a guard against stale requests, a
  Retry button. Lazy-loaded, 6 at a time, in fixed-ratio boxes.
- **Search:** debounced, stale queries cancelled. If the database can't be read, the precomputed title and
  facet shard is shown for browsing.
- **Freshness:** a banner appears when the catalogue is more than 14 days old.
- **Content Security Policy,** in a meta tag:
  - `default-src 'self'`;
  - `img-src` self, `huggingface.co`, `*.hf.co`, `cas-bridge.xethub.hf.co` and edisondigital;
  - `connect-src` self and the same HF hosts;
  - `script-src 'self' 'wasm-unsafe-eval'`.
- **Untrusted text:** escaped or set with `textContent`. IDs are checked against a regex before any URL is
  built.
- **Supply chain:** no third-party runtime scripts, no analytics, no cookies. Dependencies are vendored and
  pinned; Actions are pinned to a commit SHA; Dependabot watches the repo. LaTeX never runs on harvested text in
  CI.
- **Accounts:** GitHub and HF accounts are owned by an organisation, with two admins.
- **Deferred to v2:** a service worker, which must skip Range requests, use a cache key per build, and go to
  the network first for HTML and `current.json`.

**Monitoring:** a nightly Action checks 200 sampled records, thumbnails and PDFs on every source. Failed
harvests, builds and checks open issues automatically.

## Phases

0. **Spikes:**
   - HF policy for 10 GB or more, and Range and CORS through its redirect hosts.
   - The `/api/media` total, to size the image mirror, and harvest speed, once edisondigital is back.
   - A chunked 150–250 MB SQLite database on Pages: query and facet timings.
   - Rights confirmation with the editors and the Thomas Edison National Historical Park.
1. **Publisher,** boundary test, frozen IDs and ID allocation against edisondigital.
2. **Harvester:** 2a metadata, then 2b image derivatives (runs for weeks in the background).
3. **Builder** and search database.
4. **Front end.**
5. **Monitoring,** takedown runbook, Internet Archive deposit.
6. **Launch.**
7. **Later:** notebooks and accounts, plus the service worker.

## Critical files

- **edison-pipeline, new:**
  - `src/edison_pipeline/public_export.py`
  - `cli.py` (`publish`)
  - `tests/test_public_export_boundary.py`, `tests/test_public_export.py`
- **edison-pipeline, changed:**
  - `reorder.py`: refuse published documents.
  - `db.py`: the `publish_queue` table and migration; `next_document_ids` checks the harvested ID list.
  - `collab.py`: enqueue on admin approval, un-approval and send-back.
  - `folder_exports.py`: enqueue when an export finishes.
  - `review_server.py`: start the publish worker and the 30-minute sweep; add the admin-only publish state to
    the folder payload.
  - `app/static/app.js`: the folder card's "site update pending / failed" note (admin only).
- **edison-pipeline, reused:** `transcript_layout.py`, `folder_latex.py`, `imaging.py`, `docid.py`,
  `folder_exports.py`, `manifest.py`, `notebooks.py` (`refuse_outage_page`, `_plain`), and `app/static/app.js`
  and `style.css` (lifted, not shared).
- **edison-archive, new repo:**
  - `builder/harvest.py`, `builder/harvest_images.py`, `builder/build.py`
  - `web/` (`index.html` landing page, `search.html`, `topic.html`, `folder.html`, `document.html`, `js/`,
    `css/`, `fonts/`, `data/topics.json`, `sources.json`)
  - `.github/workflows/{harvest,harvest-images,build,deploy,check}.yml`

## Verification

- **pytest:**
  - the publisher allowlist and boundary, the explicit folder list, and the status hash;
  - merge precedence, statuses, collisions, aliases and withdrawals;
  - the harvester's outage detection, validation gates and resume.
- **`node --test`:**
  - the query parser (modes, field tags, quoting, syntax errors);
  - source fallback and 429 pausing;
  - the transcription renderer against `test_folder_pdf_matches_site.py`'s fixtures and hostile inputs.
- **Integration:** build from fixtures (two local folders plus about 500 harvested items), serve `web/`
  locally, and drive it in the built-in browser:
  - search modes, filters and sort;
  - badge, draft and edition labels;
  - downloads;
  - mobile tabs;
  - keyboard viewer.
- **Failure drills:**
  - block HF → Pages thumbnails, edisondigital fallback, banner;
  - block edisondigital → placeholders and Retry;
  - deploy a new database mid-session → no mixed-page errors;
  - corrupt a chunk → a clear message.
- **Live-update drills** (local review server on port 8010, plus a test HF repo and a test Pages site):
  - an admin approves a draft → the badge appears on the site within about 10 minutes;
  - edit and re-export a folder → the new text and "Updated" date go live;
  - approve twenty documents quickly → one publish and one build;
  - HF unreachable during an approval → the folder card shows "pending", the outbox retries, and the sweep
    publishes once HF is back;
  - an older `source_version` arrives late → it's skipped;
  - a student's payload never contains the publish state (`test_student_admin_boundary.py`).
- **Targets:** first results under 1.5 s on a cold cache; the document page usable under 2 s; approval to live
  in about 10 minutes.
- **Before launch:** the link-check Action and the boundary test both pass.

## What v0 built, and where it departs from the plan (20 November)

- **Built and tested:**
  - the publisher (`edison-pipeline/src/edison_pipeline/public_export.py`, CLI `publish`);
  - the harvester (`builder/harvest.py`);
  - the builder (`builder/build.py`, `builder/textindex.py`);
  - the site (`web/`): landing page, topics, collections, search, topic, collection, document reader and about.
  - **Run locally:** publish into `out/dataset/local/`, then `builder/build.py --dataset out/dataset --out out/site`. The preview serves
    `out/` on port 8020 at `/site/`.
- **Search engine changed:** a static index built in Python and queried in plain JS, in place of SQLite FTS5 over HTTP range
  requests.
  - **Why:** both HTTP-range SQLite libraries are unmaintained (last published 2022 and 2023), and some need headers GitHub
    Pages cannot send.
  - **How it works:** terms and neighbouring word pairs, each with a field mask, sharded by their first two letters; phrases match
    by word pairs; field searches by mask.
  - **Filters:** a facet table gives year, type, collection and status filters, and sorting by date or DocID.
  - **One tokenizer:** Python and JS split words the same way, enforced by `tests/fixtures/tokenize.json`.
- **Asset versioning:** the builder copies `css/`, `js/` and `fonts/` to `assets/{content-hash}/` and rewrites the HTML, so a deploy
  can never mix old code with new data.
- **Not yet built:**
  - the live-update outbox and worker in the review server;
  - the Hugging Face upload;
  - scheduled harvest workflows;
  - mirroring edisondigital's image derivatives (the harvester records their addresses);
  - incremental harvests (v0 always does a full, resumable sweep);
  - freezing published DocIDs in `reorder.py`;
  - checking new DocIDs against the harvested list.

## Deploying the archive

Set the Pages publishing source to **GitHub Actions** and set the repository variable
`HF_DATASET_REPO` to the public, ungated Hugging Face dataset ID (`owner/name`). The
`Deploy archive` workflow runs the test gate, builds from one dataset commit, and
deploys only the generated site to Pages. Browser requests for scans and downloads
use that same dataset commit. The local preview still uses `../dataset/`.
If a deployment removes a revision an open page is using, a missing data file
prompts the page to check the live pointer and reload with its current URL.

Deployments run on pushes to `main`, on manual runs from the default branch, and on
`repository_dispatch` events of type `archive-published` or `archive-harvested`.
Publishers and harvesters send those events after their dataset updates finish.

## Changes after the first review round (21 November)

- **Name:** the site is called **The Edison Papers**.
- **Look:**
  - a light, Apple-style interface modelled on the Library of Congress, Smithsonian, Europeana and Wikimedia Commons;
  - Inter for the interface and Source Serif 4 only for transcriptions;
  - Lucide line icons (ISC licence);
  - the Noun Project Edison portrait as the logo, credited in the footer and on the About page.
- **Search index:**
  - single words are kept in files named by their first three letters, and word pairs in 512 files chosen by an FNV-1a hash;
  - common words (and pairs containing them) are left out; the list ships in `meta.json`;
  - postings store the gap from the previous document.
- **Per-build data:** each build's data lives in `data/{revision}/`, with the previous build kept, so an open page never mixes two builds.
- **Fixes from the first reviews:**
  - **Front end:** fetch failure handling, wheel zoom, the Retry button, unknown search modes, highlighting, dark-mode button
    contrast, and the label popover.
  - **Search:** parser edge cases, and scoring that rewards matching more of an OR group.
  - **Builder:** withdrawals of edisondigital copies, and checking every ID before anything is written.
- **Still open from the reviews:**
  - **Publisher:** a stale PDF stays linked after a document is sent back; the plain-text PDF fallback fails; approval needs the
    folder's state; unpublishing; EXIF stripping.
  - **Harvester:**
    - only template-1 items should be harvested;
    - a refused harvest should go to quarantine;
    - per-page checkpoints on Hugging Face;
    - overlapping pages;
    - a media count check;
    - unescaping `'`;
    - lean memory use.
  - **Builder:** gates in CI against the deployed pointer, a clean output directory, and splitting large folders.

## Second review round (21 November)

- **What counts as Verified:** a collection that has an approved folder export (the ZIP in `data/exports/new/`) is Verified,
  because a folder is only exported once an admin has accepted every document in it.
  - **The publisher:** `publish --exports data/exports/new` publishes each such collection straight from its ZIP. It uses only
    the allowlisted fields; the reviewer's name, cost and model in the ZIP never come across.
  - **Precedence:** exported collections take precedence over the database. Other folders come from the database as drafts.
  - **A document accepted inside a folder that is still with a student, or was sent back,** stays "Under review" until the folder
    is approved.
- **Search,** modelled on Lunr, MiniSearch, Pagefind and Elasticsearch defaults:
  - **Porter stemming,** in both the index and the query, written identically in Python and JS. Parity is checked on all 3,812
    distinct words of our transcriptions.
  - **Variants:** an editable list (`web/data/synonyms.json`) used in both directions, such as lab ↔ laboratory and co ↔ company.
  - **Side by side:** a bonus for query words that stand next to each other in the document.
  - **Field weights:** title above names above text.
  - **No complete match:** a search that finds no document with every word falls back to documents with most of them, and says so.
  - **Nothing at all:** "Did you mean…" suggests the closest indexed word.
- **Controls:**
  - **Menus:** the site's own menus replace native selects (sort, results per page, the landing page's scope, the advanced-search
    rows). The select stays underneath for forms.
  - **Date slider:** a two-handle slider sits over the date histogram, its handles hidden until the chart is hovered or focused.
  - **Checkboxes:** styled to match.
- **Viewer:**
  - **Fit:** Fit page and Fit width. The reader's row is pinned to its height; a long transcription used to stretch the scan pane, so
    "Fit" fitted to a viewer partly off-screen.
  - **Space:** the document page is full width, with a draggable, remembered divider and a Focus mode.
- **Test gate:** `node scripts/check.mjs`, also run by the pre-commit hook and the CI workflow. See `TESTING.md`.

## Scale: 100,000 documents and about 1 TB (21 November)

Measured on 21 November from our own published files and a synthetic build:

- **Per page:** the full scan averages 3.0 MB, the viewer image 324 KB, the thumbnail 37 KB; a document's PDF about 130 KB.
  So almost all of the terabyte is full-resolution scans, and the site itself needs only about 4% of it.
- **Hugging Face limits** (storage-limits docs, checked today): fewer than 100,000 files per repo and at most 10,000 per folder;
  free public storage is "best-effort", and TB-scale datasets need a storage grant (datasets@huggingface.co) or a paid plan
  (about $10 per TB a month). Anonymous file URLs allow 3,000 resolves per 5 minutes per IP (the `RateLimit` header).
- **Range reads work:** a `resolve` URL answers a byte-range request with `206` and CORS headers on both hops, so the
  browser can read one image out of a large packed file.
- **Search index growth:** 10,000 documents gave 51 MB of site data; 40,000 gave 186 MB (75 MB of index, 3,702 index files,
  the largest 0.9 MB) and an 11-minute build peaking at 1.4 GB. Straight-line growth puts 200,000 documents (ours plus
  edisondigital's catalogue) near 0.9 GB, beyond GitHub Pages' 1 GB once the previous build is kept.

What changes:

1. **Three storage tiers.**
   - *Site tier* (GitHub Pages): only the app and the search index.
   - *Reading tier* (Hugging Face): records, viewer images, thumbnails and PDFs, about 40 GB at 100,000 documents.
   - *Preservation tier*: the full-resolution scans, about 1 TB. Internet Archive items of about 50 GB each, linked from the
     viewer's "Full resolution" download; a Hugging Face grant is the alternative.
2. **Packed files, not one file per image.** Each collection's viewer images and thumbnails go into a few large pack files
   with a small offset table, read by byte range. At about 8 files per document today, 100,000 documents would be 800,000
   files, eight times Hugging Face's limit; packed, they are a few thousand.
3. **One resolve per pack per visit.** The browser resolves a pack's CDN address once and reuses it for every image in it, so
   a page of 25 thumbnails costs one resolve, not 25, which keeps a shared campus address within the rate limit.
4. **The index and folder records move off Pages.** The folder records (most of the 186 MB) go to Hugging Face, as the plan
   first said. The index is split by term hash into fixed-size files so no file grows with the corpus.
5. **The builder streams.** It writes postings in batches instead of holding every document's terms in memory, so a 200,000
   document build stays within a CI runner.

## Semantic search: embeddings (21 November)

- **`builder/embed.py`** turns the published public records into passages and stores their vectors:
  - **Passages:** one catalogue passage (title, date, writer and recipient, description, people, subjects, places), then
    windows of about 180 words of transcription that overlap by 36. Every window opens with the document's heading.
  - **Scope:** `approved` (the exported collections, the default), `transcribed` or `all`.
  - **Encoded once:** a passage's key is a hash of its text, so an edit re-encodes only the windows it touched.
  - **Steps:**
    1. `plan` writes job-sized chunks of new passages;
    2. `encode` runs on Amarel, one Slurm array task per chunk, resumable;
    3. `collect` folds the results into an append-only part and writes the manifest last;
    4. `related` precomputes each document's nearest documents.
  - **Store:** float16 unit vectors in separate parts. Collection rewrites any part containing unreferenced rows and
    clears stale related-document results when the document list or passage keys change.
- **Amarel:** `deploy/amarel/embed.sbatch` reuses the drafting job's environment. Models are fetched once to
  `~/edison-amarel/embed/models` by `fetch_models.py`. The CPU partition `cmain` started small runs within minutes when the
  GPU queue was five hours deep.
- **First run (294 verified documents, 1,005 passages):** arctic-embed-s took 55 seconds on CPU. Without sharing words, it found:
  - "letters about hearing loss" → the Deafness collection;
  - "houses built from poured concrete" → Cement House;
  - "flying machines" → Aviation.

  For 248 of 294 documents, the nearest neighbour is in the same collection.
- **At 100,000 documents,** expect about 350,000 passages: 270 MB of vectors with arctic-embed-s, 720 MB with Qwen3 at full
  width. That is minutes on one L40S, and an hour or two as a CPU job array.

## Search by meaning in the site (21 November)

- **Builder:** writes `data/{revision}/vectors/`:
  - `vectors.i8`: each arctic-embed-s passage vector as signed bytes (scale 127);
  - `ordinals.u32`: each passage's document;
  - `meta.json`: the model, its pinned revision and the query prefix, with `layout: "flat"`.
  Only documents in the build are included. At about 5,300 passages this is 2 MB.
- **Browser:**
  - `js/semantic-worker.js` runs Snowflake's 8-bit ONNX query model (32 MB, downloaded once and cached) with vendored
    Transformers.js 4.3.2 and the WebAssembly-only ONNX Runtime (14 MB), served with the worker in the same versioned asset bundle. The page's
    security policy is unchanged, because a same-origin worker gets its own policy.
  - `js/semantic.js` scores every passage, and shows no meaning matches unless the best document reaches 0.55. The
    archive-wide cutoff uses the top 40% of the gap between the archive's median document and the best one, within 0.12
    of the best match. Filters apply before the limit of 40 additional results. The median matters because how alike
    unrelated documents look changes with the query: 0.43 for "houses
    built from poured concrete", 0.56 for "letters about hearing loss".
  - Results are merged with keyword results by Reciprocal Rank Fusion (k = 60). A keyword match with a similarity score
    below the meaning cutoff counts half. Documents without embeddings retain their full keyword weight.
  - When the archive has a strict keyword or meaning match, the completed search shows only strict keyword and meaning
    matches within the selected filters. Documents found only by meaning are tagged "Matched by meaning".
- **When it runs:** for plain multi-word queries only. Quotes, field searches, exclusions and Exact phrase stay
  keyword-only. Keyword results always show first. A "Search by meaning" switch is on by default (off when the browser
  asks to save data), and its setting is remembered.
- **Measured with the model warm:** a search, including the meaning pass, takes about 0.12 s. For example, "houses built
  from poured concrete" adds 6 Cement House letters to the 1 keyword hit.
- **At scale:** above roughly 20,000 passages, `layout` becomes a clustered index on Hugging Face, and only the clusters
  nearest each query are downloaded. See "Scale" above.

## Account books (23 November)

Account books come first among the bound volumes, because they are the smaller set: 207 books and 17,790 scans,
against 69,976 notebook scans. Every scan has an AI draft and none had been reviewed on 23 November.

- **Decisions with the user:**
  - every book is published now, each scan labelled "Under review" until an admin accepts it, as drafted documents are;
  - scans are mirrored to Hugging Face rather than shown from edisondigital;
  - the viewer draws from the structured pages the review platform itself shows; the PDF stays the platform's LaTeX one.
- **Publisher** (`edison-pipeline` `public_volumes.py`, CLI `publish-volumes`):
  - writes `volumes/{ID}/volume.json` from an allowlist (`VOLUME_FIELDS`, `OPENING_FIELDS`, the reviewer page schema);
  - names people and places by the editors' index forms, and draws subjects from the catalogue series, leaving out
    filing labels, years and IDs;
  - copies the platform's `notebook.pdf` only when its stamp matches the text being published;
  - writes `volumes/manifest.json` last; withdrawals last until restored.
- **Scan mirror** (`builder/mirror_scans.py`): one request at a time, at least a second apart, honest User-Agent, stopping on
  the outage page or the bot challenge. It copies each book's first scan before any book's later scans, strips JPEG
  metadata, makes a 400 px thumbnail, and runs for a few minutes in each publish so it never crowds the schedule.
- **Upload:** `volumes/` and `scans/` have their own allowlist in `upload.py`, independent of the publisher's, and are
  managed as whole trees so a removed book is removed from Hugging Face.
- **Build:**
  - each scan becomes a searchable entry (type "Account book", collection = its book), with the book's subjects;
  - amounts and folio numbers are left out of the searchable text (years kept), which halved the index;
  - account scans make generated topics but are left out of the featured topics, whose descriptions promise letters and papers;
  - `volumes.json` lists the books for the collections page, with which scans the mirror holds;
  - the full book records stay on Hugging Face; the reader downloads one book's `volume.json` (at most about 1 MB).
- **Reader** (`account.html`, `js/account.js`, `js/ledger.js`, `js/latex.js`, shared viewer in `js/reader.js`): the scan
  beside its ledger pages, with tables set column by column and figures flush right; drafting marks such as `[tick]` and
  `[struck out: …]` are shown, not printed raw. A scan not yet mirrored is shown from edisondigital as an image only.
- **Meaning search:** each scan's passages carry the book's title, date and subjects. The vectors grow from about 5,300 to
  about 25,000; notebooks will need the clustered layout described under "Scale".

## Notebooks (23 November)

1,047 notebooks and 69,976 scans, all drafted and none reviewed. They reuse the account-book path end to end (publisher,
upload, build, reader); what differs:

- **Decisions with the user:** scans are linked from archive.org rather than mirrored; notebooks join search by meaning only
  after the vectors are split, so the first phase is keyword search.
- **Publisher:** `notebook_title_parts` reads titles such as "N-80-10-15.2 (1880)" and "(NS-78-007) Phonograph" (the NS
  code's digits are the year, the words after it the subject). Series that name a place, person or line of work are
  subjects; "Notebooks by Edison", "Miscellaneous …" and similar filing groups are not. Each scan carries its archive.org
  IIIF service as `iiif`.
- **Images:** on 23 November archive.org's IIIF server timed out (504 after 60 s) on many first requests at every size,
  repeatably, while its book-viewer images (`archive.org/download/{item}/page/n{index}_w{width}.jpg`) answered in 1–2 s.
  The site uses the book viewer first and IIIF as the fallback. Book-viewer page index n−1 is the manifest's canvas n−1,
  which is how our scans are numbered (checked against the N149 manifest). Pages allow `archive.org` and
  `*.us.archive.org` images, because the book viewer redirects to a numbered server.
- **Mirror:** never copies a scan that has `iiif`.
- **Scale:** the build holds 88,087 entries in about 1 min 40 s; Pages data is about 150 MB (index 104 MB). Summaries are
  written 200 to a file, so a results page scattered across the years downloads a few hundred kilobytes.
- **Next:** the split vector layout for search by meaning, then the notebook passages encoded on Amarel.

## Search by meaning in clusters (24 November)

Notebooks add about 150,000 passages, which would have made the single vector file about 75 MB.

- **Layout:** above 20,000 passages the builder writes `vectors/centroids.i8` (each cluster's mean, as signed bytes) and
  one `vectors/clusters/{n}.bin` per cluster (its vectors, then each one's document ordinal). Clusters come from
  spherical k-means with a fixed seed (about 200 passages each, 20 rounds), so unchanged vectors give unchanged files.
- **Search:** the browser scores the centres, reads the nearest clusters until they hold `probe.vectors` passages, and
  keeps each cluster once read. With active filters it can also probe up to the same budget in clusters that contain
  matching documents, using a compact list of their ordinals. The cutoff's "typical document" comes from every centre's mean similarity weighted by
  size, because only the nearest clusters are scored (on real queries it was within 0.015 of the true median).
- **Measured on 50,653 passages and 25 real queries:** reading 16,000 passages (6.3 MB) found 91% of the single file's
  top 40; 10,000 (3.9 MB) found 82%. Storing each passage in two clusters did not help at the same download.
  `PROBE_VECTORS` is retuned on the full ~200,000 once the notebooks are encoded.
- **Notebook encoding:** planned on the laptop against the published records and the live store, so only the 150,531
  new notebook passages were encoded, on Amarel (job 64362128). `embed.py plan --collections accounts,notebooks` names
  them; the host's publish script switches to it in the same gap between runs that its store receives the vectors,
  because `collect` deletes unplanned finished vectors and the host would otherwise encode them on its CPU.
