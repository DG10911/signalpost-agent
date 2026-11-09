# Hosting the Signalpost viewer

`signalpost/viewer/` is a finished static site: plain HTML/CSS/JS, relative
paths, hash routes, and a `.nojekyll` file. It needs no build step and runs on
any static host. It is meant to be served at
**https://dg10911.github.io/signalpost-agent/**.

## Publish on GitHub Pages (recommended: a `gh-pages` branch)

GitHub Pages only serves a branch root or `/docs`, and the viewer lives in
`signalpost/viewer/`. So we publish that folder as the root of its own
`gh-pages` branch. In this repo, `submit` is the remote for
`github.com/DG10911/signalpost-agent`. Use `origin` if your clone points there.

```bash
# 1. make the gh-pages branch from signalpost/viewer (history is kept)
git subtree split --prefix signalpost/viewer -b gh-pages
git push submit gh-pages
```

2. On GitHub, open **DG10911/signalpost-agent → Settings → Pages**.
3. Under **Build and deployment**, set **Source: Deploy from a branch**, then
   **Branch: `gh-pages`**, **Folder: `/ (root)`**, and press **Save**.
4. Wait about a minute, then open https://dg10911.github.io/signalpost-agent/.
   The repository must be public, or on a plan that allows Pages for private
   repos.

### Update after a new run

```bash
cd signalpost
uv run python scripts/build_viewer.py --contract out/submission-contract.jsonl --out viewer
git add viewer && git commit -m "Viewer: rebuild from latest run"
cd ..
git subtree split --prefix signalpost/viewer -b gh-pages   # same branch, new commit on top
git push submit gh-pages
```

## Alternative: GitHub Actions (no extra branch)

Set **Settings → Pages → Source: GitHub Actions** and add
`.github/workflows/pages.yml` at the repo root. Your token needs the `workflow`
scope to push this file.

```yaml
name: viewer
on:
  push:
    branches: [main]
    paths: ["signalpost/viewer/**"]
  workflow_dispatch:
permissions: { contents: read, pages: write, id-token: write }
concurrency: { group: pages, cancel-in-progress: true }
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: { name: github-pages, url: "${{ steps.d.outputs.page_url }}" }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with: { path: signalpost/viewer }
      - id: d
        uses: actions/deploy-pages@v4
```

## Preview locally

```bash
python3 -m http.server 8000 -d signalpost/viewer   # then open http://localhost:8000/
```

You must serve the folder. Opening `index.html` straight from disk does not
work, because browsers block `fetch()` on `file://` URLs.

## Check it

```bash
uv venv /tmp/vw && uv pip install --python /tmp/vw playwright && /tmp/vw/bin/playwright install chromium
/tmp/vw/bin/python signalpost/scripts/check_viewer.py --viewer signalpost/viewer --shots /tmp/viewer-shots
```

The check runs the find, open, compare and verify flows at 1440 px and 390 px,
and the compare view at 360 px. It fails on any console error or horizontal
overflow.
