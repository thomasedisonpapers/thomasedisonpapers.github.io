# Testing: how a change is accepted

A change to the site is accepted only when every earlier test still passes. That holds for a security fix, a
feature update and a new feature alike. Three things enforce it:

| Where | What runs | When |
|---|---|---|
| `npm test` (`node scripts/check.mjs`) | every suite below | by hand, before saying a change is done |
| `.githooks/pre-commit` | the same gate | on every commit; a failure blocks the commit |
| `.github/workflows/test.yml` | the same gate, on a clean Linux machine | on every push and pull request |

A fresh clone turns the hook on with `npm run hooks`, which runs `git config core.hooksPath .githooks`. Once the
repository is on GitHub, mark the `tests` check as required in branch protection, so nothing merges past it.

## The suites

- **Builder and harvester** (`tests/py/`, pytest):
  - merge rules: ours first, per field and per file;
  - integrity gates: shrink, missing files, unsafe IDs, withdrawals;
  - the index layout;
  - the harvester's politeness, outages, resume, overlap, quarantine and validation.
- **Embeddings** (`tests/py/test_embed.py`, with a stand-in model so no download is needed):
  - passages come only from public records, never drafts outside the chosen scope or withdrawn documents;
  - each passage is encoded once, and an edit re-encodes only what changed;
  - a store is whole or untouched, a re-run job skips finished chunks, and dead rows are compacted away.
- **Search** (`tests/js/search.test.js`):
  - the query language and ranking;
  - filters and sorting;
  - Python and JS splitting words identically, checked through the shared fixture `tests/fixtures/tokenize.json`.
- **Security** (`tests/js/security.test.js`):
  - hostile text in every renderer (cards, facts, badges, breadcrumbs, transcriptions, highlighting) must never become a
    live tag, handler or `javascript:` link;
  - ID validation;
  - a fuzz pass that no query can break the parser.
- **Page policy** (`tests/js/site_policy.test.js`):
  - every page declares a strict Content Security Policy;
  - no inline scripts or event handlers;
  - no outside script hosts;
  - no `eval`, `new Function` or `document.write`.
- **Publisher** (in edison-pipeline: `tests/test_public_export.py`, `tests/test_public_export_boundary.py`): the public
  boundary (no reviewer, cost, model or internal note), the status rules, unpublishing, PDFs and EXIF stripping. These run in
  edison-pipeline's own suite.

## Writing tests

- **Name each test as a sentence about behaviour,** and have it assert one thing.
- **Name the report in a regression test,** for example `(regression: AT&T read as AT&<mark>amp</mark>;T, 20 November)`.
- **Security tests check what reaches the page,** not how the code gets there, so a refactor cannot quietly weaken one.
- **Never relax a test to make a change pass.** If a test's expectation is wrong, say so in the change and replace it with the
  right one.
