# The Edison Papers (public site)

A public, read-only, static website for the Thomas A. Edison Papers, called simply "The Edison Papers". Visitors search the collection, browse
documents as thumbnails, and read each scan beside its transcription, with PDF, JSON and image downloads. It is
deliberately separate from the review platform (edison-pipeline) and from edisondigital.rutgers.edu, so it
outlives both. The full design is in `docs/PLAN.md`; read it before starting a module.

## How it fits together

- **Publisher** (lives in edison-pipeline): sends our documents to the Hugging Face dataset whenever an admin
  approves something or re-exports a folder.
- **`builder/harvest.py`:** mirrors edisondigital's metadata and image derivatives into the dataset, weekly
  and resumably.
- **`builder/build.py`:**
  - merges the two sources (ours wins, per field and per file);
  - writes per-folder JSON to Hugging Face;
  - writes the search database and our thumbnails to GitHub Pages.
- **`web/`:** the site itself, in vanilla JS. Pages: landing page, search, topic, folder, document.
- **Hosting:** GitHub Pages (the app, search and thumbnails) and a Hugging Face dataset (records, images and
  PDFs). No domain, no server.

## Commands

```bash
.venv/Scripts/python.exe -m pytest -q -p no:randomly      # builder tests
node --test "tests/js/**/*.test.js"                       # front-end tests
.venv/Scripts/python.exe builder/build.py --dataset out/dataset --out out/site   # build; preview serves out/ at /site/
.venv/Scripts/python.exe builder/embed.py plan --dataset out/dataset --store out/dataset/embeddings --model arctic-embed-s
# then encode on Amarel (deploy/amarel/embed.sbatch), copy done/ back, and run: embed.py collect --store … --model …
.venv/Scripts/python.exe builder/upload.py --dataset out/dataset --dry-run   # then without --dry-run; see docs/DEPLOY.md
```

## Rules

- **Every change passes the gate first:** `node scripts/check.mjs` (also `npm test`, the pre-commit hook and CI). A change is
  not done, and is never described as done, while any test fails. See `TESTING.md`.

- **Comments say why, in one sentence at most,** in code, tests, JS, CSS and HTML. Longer reasoning goes in
  the commit message.
- **Tests are named as sentences about behaviour, and each asserts one thing.** A regression test names the
  report it came from.
- **Git:**
  - never commit or push unless asked in that turn;
  - commit messages are one short subject line, with no Co-Authored-By or other attribution trailer;
  - Actions are pinned to a commit SHA.
- **The public boundary:**
  - a field reaches the site only if it's on the publisher's allowlist;
  - reviewer and student identities, verdicts by role, costs, claims, comments and pipeline internals never do;
  - every new field gets the question: should the public see this?
- **Untrusted text** (transcriptions, harvested metadata) is escaped or set with `textContent`, never inserted
  as HTML. IDs are validated before any URL is built.
- **Be gentle with edisondigital.rutgers.edu:**
  - its API sends no CORS headers and it is often down, so browsers never call it except for `<img>`;
  - the harvester runs at most two requests at a time, with an honest User-Agent;
  - it stops on a `sorry.rutgers.edu` or `/bot-challenge` redirect.
- **Reuse proven code:** lift the pipeline's renderers and helpers rather than rewriting them. Check data
  conventions against a live API sample, not one fixture.
- **Never change the Windows host or the review platform** without confirming first. Never enter or repeat
  passwords.
- **UI:** modern and self-explanatory. Plain-word navigation, no icon-only buttons, both themes, reduced motion
  respected, keyboard accessible.

## Related places

- **`../edison-pipeline`** (a sibling folder): the review platform and data source.
  - `transcript_layout.py` and `app/static/app.js` (`transcriptionBlocks`, `renderHighlighted`): the
    transcription layout.
  - `manifest.py` and `notebooks.py` (`refuse_outage_page`): edisondigital access.
  - Its server runs on port 8010.
- **`../latex-for-omeka`** (a sibling folder): the Omeka LaTeX module (GPLv3), a separate project.
