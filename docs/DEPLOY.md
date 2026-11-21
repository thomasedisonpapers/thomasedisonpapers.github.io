# Deploying The Edison Papers on GitHub Pages

The site is plain static files with relative links. It runs unchanged at `https://<org>.github.io/` today and at a domain
later. `tests/js/site_policy.test.js` keeps it that way by refusing any root-absolute path (`/search.html`) or hard-coded
host (`github.io`) in `web/` or `builder/`.

**This project (set up 21 November):**
- **Organization:** `thomasedisonpapers`, on both GitHub and Hugging Face.
- **Site repository:** [`thomasedisonpapers/thomasedisonpapers.github.io`](https://github.com/thomasedisonpapers/thomasedisonpapers.github.io), public.
  - The site will be served at `https://thomasedisonpapers.github.io/`.
  - Pages is set to deploy from GitHub Actions.
  - Actions must be pinned to full commit SHAs, and the workflow token is read-only by default.
  - Secret scanning and push protection are on.
- **Rulesets on `main`:**
  - "No force pushes or deleting main" has no bypass;
  - "Tests must pass on main" requires the `check` job, and organization admins may bypass it.
- **Dataset:** [`thomasedisonpapers/edison-papers`](https://huggingface.co/datasets/thomasedisonpapers/edison-papers), private
  until the first upload passes the boundary checks.

## 1. Accounts (done once, by a person, in the browser)

1. **GitHub organization:** create one, for example `edison-papers`.
   - Add a second owner, so the site does not depend on one person.
   - Require two-factor sign-in under *Settings → Authentication security*.
2. **Hugging Face organization:** create one with the same name. It holds the records, images and PDFs (see `PLAN.md`).

## 2. The repository

1. **Create `<org>/<org>.github.io`** as a public repository.
   - That exact name serves the site at the root, `https://<org>.github.io/`.
   - Any other name serves it under `/<repo>/`, and those paths would change on a move to a domain, so use this name.
2. **Push this folder** to `main`.
3. **Settings → Pages → Build and deployment → Source:** choose **GitHub Actions**.
   - The deploy workflow uploads the built site straight to Pages, so built files and data never enter git history.
   - That is what makes a takedown complete.
4. **Settings → Rules:** protect `main`.
   - The `tests` workflow must pass.
   - Force pushes and deletion are blocked.
5. **Settings → Actions → General:**
   - require actions to be pinned to a full commit SHA;
   - give the workflow token read-only access by default.
6. **Settings → Secrets and variables → Actions:**
   - `HF_TOKEN`: a fine-grained Hugging Face token that can write only to the dataset;
   - `HF_DATASET`: the dataset's name.
7. **Publisher on the host:** run `publish --site-url https://<org>.github.io`, so each PDF prints the site's address.

## 3. Moving to a domain later

Nothing in the site's code changes; these are settings only.

1. **Choose the domain.** A Rutgers subdomain (for example `edisonpapers.rutgers.edu`) is best, because the university
   renews it. A bought domain must be renewed every year, and if it lapses every link breaks.
2. **Verify the domain first,** under *Organization settings → Pages → Add a domain*.
   - Add the TXT record GitHub shows (`_github-pages-challenge-<org>…`) and keep it forever.
   - Verifying before pointing DNS at GitHub stops anyone else from claiming the domain for their own Pages site.
3. **Point DNS at GitHub:**
   - a subdomain gets a `CNAME` record to `<org>.github.io`;
   - a bare domain gets the `A` and `AAAA` records listed in GitHub's "Managing a custom domain" page.
4. **Repository Settings → Pages → Custom domain:** enter the domain, wait for the DNS check, then tick **Enforce HTTPS**.
5. **Republish with the new address:** `publish --site-url https://<domain>`. Each PDF's stamp includes the address, so the
   publisher remakes every PDF by itself.
6. **Check:**
   - an old link such as `https://<org>.github.io/document.html?id=E2002AAA` must answer
     `301 Moved Permanently` to the same path and query on the new domain;
   - then run the link check.

### What carries over, and what does not

- **Old links keep working.** GitHub answers every `github.io` request with a permanent redirect to the same path and query
  on the domain. It was tested on 21 November against `jupyter.github.io/about?x=1`, which redirects to `jupyter.org/about?x=1`.
  - The redirect lasts only while the repository exists and Pages stays on, so never delete the repository, rename the
    organization, or switch Pages off.
- **Citations keep working.** "Cite this" gives the document's own address, `document.html?id=…`, which the redirect carries
  over.
- **PDFs already downloaded** keep the old address in their stamp, which still redirects.
- **The data does not move.** Hugging Face and the Content Security Policy (`'self'` plus Hugging Face's hosts) do not
  depend on the site's address.
- **Search engines move their entries** to the new address, because the redirect is permanent.
- **Each visitor's saved preferences** (theme, the viewer's divider, recent searches) start fresh once. Browsers keep them
  per address, so this cannot be carried over.
