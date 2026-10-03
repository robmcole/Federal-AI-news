# Federal AI News

A weekday TLDR of AI and tech across the U.S. federal government.

The site is published at <https://robmcole.github.io/Federal-AI-news/>.

## Add an issue

Create one file, `issues/YYYY-MM-DD.md`, and commit it to `main`. No other edits are required. The home page, archive, permalink, and RSS feed are generated from the files in `issues/`.

```markdown
---
date: YYYY-MM-DD
title: "Issue title"
---

**TL;DR:** One or two sentences.

## Section name

**[Headline](https://example.com/story)**
What happened and why it matters.
*Source: Outlet name · Reliability Score: 4/5*
*Primary document: [Document name](https://example.com/doc)*
*Image: https://example.com/preview.jpg*
```

The filename date and the `date` field must match. Scores are 5 (official source), 4 (established outlet), 3 (analysis, vendor, or third-party copy), or 2 (needs a second source).

`*Image:*` is optional. Put it with the source line when you want to force a thumbnail. If you leave it out, the build reads the story link’s `og:image` or `twitter:image`, saves a resized copy, and shows that on the card. When a preview can’t be fetched (SAM.gov pages often can’t), the card shows a tile with the outlet name instead of a broken image.

## Favorites

Each story has a star button. Saved stories are stored in this browser only (`localStorage`) and listed on the Favorites page. They are not sent to a server. Clearing site data, or opening the site in another browser, starts from an empty list. Use the same star to remove a story, or choose “Show saved only” on an issue.

## Deploy

Pushes to `main` run [`.github/workflows/pages.yml`](.github/workflows/pages.yml). The workflow builds the site with `python build.py` and publishes it with the official GitHub Pages actions (`actions/upload-pages-artifact` and `actions/deploy-pages`). Thumbnail downloads are cached between workflow runs.

In the repository settings, set **Settings → Pages → Build and deployment → Source** to **GitHub Actions** (one time, after this workflow is on `main`).

Links in the generated pages are relative, so the same build works locally and under the `/Federal-AI-news/` project-site path.

## Build locally

```bash
pip install -r requirements.txt
python build.py
python -m http.server -d dist 8000
```

Open <http://localhost:8000/>.
